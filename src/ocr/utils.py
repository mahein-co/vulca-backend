import PyPDF2, pytesseract, re, pandas as pd
from PIL import Image
from datetime import datetime
from pdf2image import convert_from_path
import io
import tempfile
import os


def clean_ai_json(raw: str) -> str:
    """
    Nettoie une réponse OpenAI susceptible de contenir des fences ```json``` ou du texte autour.
    Tente d'extraire la première occurrence d'un objet JSON complet {...}.
    """
    raw = raw.strip()

    # Retirer balises de code ```...```
    if raw.startswith("```") and raw.endswith("```"):
        # supprime les fences
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
        # parfois la première ligne est 'json'
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
def extract_content(file, file_type):
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
                return extracted_text
        except:
            pass

        # ---- 2) PDF scanné → OCR ----
        file_stream.seek(0)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_stream.read())
            tmp_path = tmp.name

        try:

            # -------------------------
            # 🔍 Auto-détection POPPLER
            # -------------------------
            system = platform.system()
            poppler_path = None

            if system == "Windows":
                # essai automatique dans le répertoire utilisateur
                base = os.path.expanduser("~")
                possible_poppler = os.path.join(base, "poppler", "Library", "bin")

                if os.path.isdir(possible_poppler):
                    poppler_path = possible_poppler
                else:
                    # si poppler n'existe pas → avertissement
                   # print("⚠️ Poppler n'est pas installé localement dans ~/poppler/")
                    poppler_path = None  # laisser None → tentera sans chemin

            # Linux / Render → poppler_path = None (pdftoppm dans PATH)
            # -------------------------

            images = convert_from_path(
                tmp_path,
                poppler_path=poppler_path
            )

            for img in images:
                text += pytesseract.image_to_string(img)

        except Exception as e:
            print("❌ OCR PDF ERROR :", e)
            raise e

        finally:
            os.remove(tmp_path)

        return text

    # ---- IMAGES ----
    elif file_type in ["png", "jpg", "jpeg"]:
        image = Image.open(io.BytesIO(file_bytes))
        return pytesseract.image_to_string(image)

    # ---- EXCEL ----
    elif file_type in ["xls", "xlsx"]:
        df = pd.read_excel(io.BytesIO(file_bytes))
        return df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

    # ---- CSV ----
    elif file_type == "csv":
        df = pd.read_csv(io.BytesIO(file_bytes))
        return df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

    return text


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
    Ex : 06/09/2024 → 6 septembre 2024
    """
    fr_months = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"
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

    Génère une description claire, professionnelle et fluide en français,
    sans lister les clés, mais en interprétant intelligemment le contenu.
    """

    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )

    return completion.choices[0].message.content

