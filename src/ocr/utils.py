import PyPDF2, pytesseract, re, pandas as pd
from PIL import Image


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


# charger pcg 2005 
# def charger_classes_1_a_7_pdf(pdf_path):
#     doc = fitz.open(pdf_path)
#     texte_total = ""
#     for page in doc:
#         texte_total += page.get_text()
#     lignes = texte_total.split('\n')
#     paragraphe_classe = []
#     garder = False
#     for ligne in lignes:
#         texte = ligne.strip()
#         if re.match(r"^CLASSE\s+1", texte, re.IGNORECASE):
#             garder = True
#         elif re.match(r"^CLASSE\s+8", texte, re.IGNORECASE):
#             garder = False
#         if garder and texte:
#             paragraphe_classe.append(texte)
#     return "\n".join(paragraphe_classe)  
