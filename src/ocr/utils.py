import PyPDF2, pytesseract, re, pandas as pd
from PIL import Image
from datetime import datetime
from pdf2image import convert_from_path
import io
import tempfile
import os
import cv2
import numpy as np
import time

OCR_HEADER = "--oem 3 --psm 7 -l fra+eng"
OCR_BODY   = "--oem 3 --psm 6 -l fra+eng"

def clean_ai_json(raw: str) -> str:
    """
    Nettoie une reponse OpenAI susceptible de contenir des fences ```json``` ou du texte autour.
    Tente d'extraire la premiere occurrence d'un objet JSON complet {...}.
    """
    raw = raw.strip()

    # Retirer balises de code ```...```
    if raw.startswith("```") and raw.endswith("```"):
        # supprime les fences
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
        # parfois la premiere ligne est 'json'
        raw = re.sub(r'^\s*json\s*', '', raw, flags=re.I).strip()

    start = raw.find("{")
    if start == -1:
        return raw 

    count = 0
    end_idx = None
    for i in range(start, len(raw)):
        if raw[i] == "{":
            count += 1
        elif raw[i] == "}":
            count -= 1
            if count == 0:
                end_idx = i
                break

    if end_idx:
        candidate = raw[start:end_idx+1]
        return candidate.strip()
    else:
        return raw  

def pil_to_cv2(img: Image.Image):
    """Convert PIL Image → OpenCV image"""
    if img.mode != "RGB":
        img = img.convert("RGB")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def resize_for_ocr(img, max_pixels= 2_000_000):
    h, w = img.shape[:2]
    pixels = h * w

    if pixels <= max_pixels:
        return img

    scale = (max_pixels / pixels) ** 0.5
    return cv2.resize(
        img,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_AREA
    )


def preprocess_fast(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]



def extract_content(file, file_type):
    start_time = time.time()
    import platform

    text = ""

    # Lire tout le fichier en bytes pour ne PAS perdre le curseur
    file_bytes = file.read()
    file_stream = io.BytesIO(file_bytes)

    if file_type == "pdf":

        # ---- 1) Lecture texte normal avec PyPDF2 ----
        try:
            reader = PyPDF2.PdfReader(file_stream)
            extracted_text = ""

            for page in reader.pages:
                try:
                    page_text = page.extract_text()
                    if page_text:
                        extracted_text += page_text + "\n"
                except:
                    pass

            if extracted_text.strip():
                print("⏱ EXTRACTION OCR (PDF texte):", round(time.time() - start_time, 2), "s")
                return extracted_text
        except:
            pass

        # ---- 2) PDF scanner OCR ----
        file_stream.seek(0)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_stream.read())
            tmp_path = tmp.name

        try:

            # -------------------------
            # ≡ƒöì Auto-detection POPPLER
            # -------------------------
            system = platform.system()
            poppler_path = None

            if system == "Windows":
                # essai automatique dans le repertoire utilisateur
                base = os.path.expanduser("~")
                possible_poppler = os.path.join(base, "poppler", "Library", "bin")

                if os.path.isdir(possible_poppler):
                    poppler_path = possible_poppler
                else:
                    # si poppler n'existe pas ΓåÆ avertissement
                   # print("ΓÜá∩╕Å Poppler n'est pas install├⌐ localement dans ~/poppler/")
                    poppler_path = None  # laisser None ΓåÆ tentera sans chemin

            # Linux / Render ΓåÆ poppler_path = None (pdftoppm dans PATH)
            # -------------------------

            images = convert_from_path(
                tmp_path,
                poppler_path=poppler_path
            )

            #for img in images:
            #    text += pytesseract.image_to_string(img)
            custom_config = r'--oem 3 --psm 6 -l fra+eng'

            for img in images:
                # PIL → OpenCV
                cv_img = pil_to_cv2(img)

                # Resize si trop grand
                cv_img = resize_if_needed(cv_img)

                # Preprocess OpenCV
                cv_img = preprocess_for_ocr(cv_img)

                # OCR
                page_text = pytesseract.image_to_string(cv_img, config=custom_config)
                text += page_text + "\n"

                # STOP intelligent (évite OCR inutile)
                if len(text.strip()) > 1200:
                    break


        except Exception as e:
            print("Γ¥î OCR PDF ERROR :", e)
            raise e

        finally:
            os.remove(tmp_path)
        
        print("⏱ EXTRACTION OCR:", round(time.time() - start_time, 2), "s")
        return text

    # ---- IMAGES ----
    elif file_type in ["png", "jpg", "jpeg"]:
        img_start = time.time()

        try:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            print("🖼️ IMAGE MODE:", img.mode, "SIZE:", img.size)

            cv_img = pil_to_cv2(img)

            # ✅ resize par nombre de pixels (clé performance)
            cv_img = resize_for_ocr(cv_img)

            # ✅ prétraitement léger
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)

            text = pytesseract.image_to_string(
                gray,
                config="--oem 3 --psm 6 -l fra+eng"
            )

        except Exception as e:
            print("❌ OCR IMAGE ERROR:", e)
            text = ""

        elapsed = round(time.time() - img_start, 2)
        print(f"⏱ OCR IMAGE: {elapsed} s")

        return clean_text_output(text)

   


    # ---- EXCEL ----
    elif file_type in ["xls", "xlsx"]:
        try:
            df = pd.read_excel(io.BytesIO(file_bytes))
            # Conversion en CSV string pour garder la structure
            return df.to_csv(index=False, sep=';')
        except Exception as e:
            print(f"Excel read error: {e}")
            return ""

    # ---- CSV ----
    elif file_type == "csv":
        try:
            df = pd.read_csv(io.BytesIO(file_bytes))
            return df.to_csv(index=False, sep=';')
        except Exception as e:
            print(f"CSV read error: {e}")
            return ""

    return clean_text_output(text)


def clean_text_output(text: str) -> str:
    """Nettoie le texte extrait (espaces multiples, lignes vides excessives)"""
    if not text:
        return ""
    # Remplacer les espaces multiples par un seul
    text = re.sub(r'[ \t]+', ' ', text)
    # Limiter les sauts de ligne consécutifs à 2
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    return text.strip()


# Detect file type based on extension
def detect_file_type(file_name):
    ext = file_name.split(".")[-1].lower()
    if ext in ["pdf"]:
        return ext
    elif ext in ["png", "jpg", "jpeg"]:
        return ext
    elif ext in ["xls", "xlsx"]:
        return ext
    elif ext in ["csv"]:
        return ext
    else:
        return "unknown"

# FORMAT DATE ======================
def convertir_dates_longues(data):
    """
    Transforme automatiquement toute date au format dd/mm/yyyy en date longue.
    Ex : 06/09/2024 a 6 septembre 2024
    """
    fr_months = [
        "janvier", "f├⌐vrier", "mars", "avril", "mai", "juin",
        "juillet", "ao├╗t", "septembre", "octobre", "novembre", "d├⌐cembre"
    ]

    for key, value in data.items():
        if isinstance(value, str) and re.match(r"^\d{2}/\d{2}/\d{4}$", value):
            # transformation
            d = datetime.strptime(value, "%d/%m/%Y")
            data[key] = f"{d.day} {fr_months[d.month-1]} {d.year}"

    return data

# GENERATE DESCRIPTION FILE SOURCE =====================
def generate_description(data, json, client, model):

    # Convertit automatiquement les dates
    processed_data = convertir_dates_longues(data)

    # GPT va analyser tout le JSON automatiquement
    prompt = f"""
    Voici un objet JSON contenant des informations diverses :

    {json.dumps(processed_data, indent=2, ensure_ascii=False)}

    Genre une description claire, professionnelle et fluide en francais,
    sans lister les cles, mais en interpretant intelligemment le contenu.
    """

    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )

    return completion.choices[0].message.content

