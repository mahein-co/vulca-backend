import PyPDF2, pytesseract, re, pandas as pd
from PIL import Image
from datetime import datetime
from pdf2image import convert_from_path
import io
import tempfile
import os
import json
import platform
# -------------------- Nettoyage texte OCR --------------------
def clean_text(text: str) -> str:
    """
    Nettoie le texte extrait :
    - Supprime doublons de lignes consécutives
    - Fusionne caractères éclatés 
    - Corrige espaces multiples
    """
    lines = text.splitlines()
    cleaned_lines = []
    prev = None
    for line in lines:
        line = line.strip()
        if line and line != prev:
            cleaned_lines.append(line)
        prev = line

    text = "\n".join(cleaned_lines)
    text = re.sub(r'(?<=\w)\s(?=\w)', '', text)  
    text = re.sub(r'\s+', ' ', text)  
    return text


def clean_ai_json(raw: str) -> str:
    """
    Nettoie une réponse OpenAI susceptible de contenir des fences ```json``` ou du texte autour.
    Tente d'extraire la première occurrence d'un objet JSON complet {...}.
    """
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
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
        return raw[start:end_idx+1].strip()
    else:
        return raw  


# -------------------- OCR PDF --------------------
def ocr_pdf(file_stream):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_stream.read())
        tmp_path = tmp.name

    try:
        system = platform.system()
        poppler_path = None
        if system == "Windows":
            base = os.path.expanduser("~")
            possible_poppler = os.path.join(base, "poppler", "Library", "bin")
            if os.path.isdir(possible_poppler):
                poppler_path = possible_poppler

        images = convert_from_path(tmp_path, poppler_path=poppler_path)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img, config="--psm 6")
        return text
    finally:
        os.remove(tmp_path)


# -------------------- Extraction contenu --------------------
def extract_content(file, file_type):
    # Lire tout le fichier en bytes
    file_bytes = file.read()
    file_stream = io.BytesIO(file_bytes)
    extracted_text = ""

    if file_type == "pdf":
        # ---- 1) Texte natif PDF ----
        try:
            reader = PyPDF2.PdfReader(file_stream)
            pdf_text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pdf_text += page_text + "\n"
            pdf_text = clean_text(pdf_text)
        except:
            pdf_text = ""

        # ---- 2) OCR PDF scanné ----
        file_stream.seek(0)
        try:
            ocr_text = ocr_pdf(file_stream)
            ocr_text = clean_text(ocr_text)
        except Exception as e:
            print("❌ OCR PDF ERROR:", e)
            ocr_text = ""

        # ---- 3) Fusionner PDF natif + OCR ----
        # On prend le PDF natif si il existe, sinon OCR
        if pdf_text.strip():
            extracted_text = pdf_text
        else:
            extracted_text = ocr_text

        return extracted_text

    elif file_type in ["png", "jpg", "jpeg"]:
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image, config="--psm 6")
        return clean_text(text)

    elif file_type in ["xls", "xlsx"]:
        df = pd.read_excel(io.BytesIO(file_bytes))
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')
        return clean_text(text)

    elif file_type == "csv":
        df = pd.read_csv(io.BytesIO(file_bytes))
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')
        return clean_text(text)

    return extracted_text


# -------------------- Détection type fichier --------------------
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


# -------------------- Conversion dates --------------------
def convertir_dates_longues(data):
    fr_months = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"
    ]

    for key, value in data.items():
        if isinstance(value, str) and re.match(r"^\d{2}/\d{2}/\d{4}$", value):
            d = datetime.strptime(value, "%d/%m/%Y")
            data[key] = f"{d.day} {fr_months[d.month-1]} {d.year}"

    return data


# -------------------- Génération description --------------------
def generate_description(data, json, client, model):
    processed_data = convertir_dates_longues(data)

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
