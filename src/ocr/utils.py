import PyPDF2, pytesseract, re, pandas as pd
from PIL import Image
from datetime import datetime


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

# Extract content from various file types for OCR processing
def extract_content(file, file_type):
    text = ""
    if file_type == "pdf":
        reader = PyPDF2.PdfReader(file)
        for page in reader.pages:
            text += page.extract_text() + "\n"

    elif file_type in ["png", "jpg", "jpeg"]:
        image = Image.open(file)
        text = pytesseract.image_to_string(image)

    elif file_type in ["xls", "xlsx"]:
        df = pd.read_excel(file)
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

    elif file_type == "csv":
        df = pd.read_csv(file)
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

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


def convertir_dates_longues(data):
    """
    Transforme automatiquement toute date au format dd/mm/yyyy en date longue.
    Ex : 06/09/2024 → 6 septembre 2024
    """
    fr_months = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"
    ]
    print("DATA : ", data)

    for key, value in data.items():
        if isinstance(value, str) and re.match(r"^\d{2}/\d{2}/\d{4}$", value):
            # transformation
            d = datetime.strptime(value, "%d/%m/%Y")
            data[key] = f"{d.day} {fr_months[d.month-1]} {d.year}"

    return data


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

