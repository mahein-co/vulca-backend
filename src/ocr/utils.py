import re, pandas as pd
from datetime import datetime
import io

# ============================================================================
# FONCTIONS UTILITAIRES CONSERVÉES
# ============================================================================

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

# ============================================================================
# ANCIENNES FONCTIONS TESSERACT - SUPPRIMÉES
# Remplacées par openai_vision_ocr.py
# ============================================================================


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
    elif ext in ["png", "jpg", "jpeg", "webp"]:
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

