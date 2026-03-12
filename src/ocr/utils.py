import re, pandas as pd
from datetime import datetime
import io
import time
import logging

logger = logging.getLogger(__name__)

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
        "janvier", "fevrier", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "decembre"
    ]

    for key, value in data.items():
        if isinstance(value, str) and re.match(r"^\d{2}/\d{2}/\d{4}$", value):
            # transformation
            d = datetime.strptime(value, "%d/%m/%Y")
            data[key] = f"{d.day} {fr_months[d.month-1]} {d.year}"

    return data

def safe_openai_call(client, model, messages, temperature=0, max_tokens=None, max_retries=5):
    """
    Exécute un appel OpenAI avec retry progressif (exponential backoff).
    Gère les RateLimitError et autres erreurs temporaires.
    """
    base_delay = 1.0  # secondes
    
    for i in range(max_retries):
        try:
            params = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                params["max_tokens"] = max_tokens
                
            completion = client.chat.completions.create(**params)
            return completion
        except Exception as e:
            error_str = str(e).lower()
            # Si c'est une erreur de quota ou de rate limit, on attend plus longtemps
            is_rate_limit = "rate_limit" in error_str or "quota" in error_str or "too many requests" in error_str
            
            if i == max_retries - 1:
                print(f"[ERROR] OpenAI API echec final apres {max_retries} tentatives: {e}")
                raise e
            
            delay = base_delay * (2 ** i)
            if is_rate_limit:
                delay *= 2  # Double le délai pour les rate limits
                
            print(f"[WARNING] OpenAI API erreur (tentative {i+1}/{max_retries}): {e}. Retrying in {delay}s...")
            time.sleep(delay)
    
    return None

# GENERATE DESCRIPTION FILE SOURCE =====================
def generate_description(data, json, client, model):

    # Convertit automatiquement les dates
    processed_data = convertir_dates_longues(data)

    # GPT va analyser tout le JSON automatiquement
    prompt = f"""
    Voici un objet JSON contenant des informations diverses :

    {json.dumps(processed_data, indent=2, ensure_ascii=True)}

    Genre une description claire, professionnelle et fluide en francais,
    sans lister les cles, mais en interpretant intelligemment le contenu.
    """

    completion = safe_openai_call(
        client=client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )

    return completion.choices[0].message.content

# GENERATE EXCEL DESCRIPTION =====================
def generate_excel_description(data, json, client, model):
    """
    Génère une description détaillée et professionnelle pour un fichier Excel financier.
    Optimisée pour les bilans et comptes de résultat.
    """
    prompt = f"""
    Voici les informations d'un fichier Excel financier importé :

    {json.dumps(data, indent=2, ensure_ascii=False)}

    Génère une description claire, détaillée et professionnelle en français, structurée comme suit :

    1. **Introduction** : Présente le fichier (nom, type de document)
    2. **Contenu** : Décris chaque feuille (nom, type, nombre de lignes) de manière fluide
    3. **Synthèse** : Résume le nombre total d'écritures et de feuilles
    4. **Métadonnées** : Mentionne les informations d'entreprise si disponibles (RCS, NIF, STAT)

    La description doit être :
    - Rédigée en paragraphes fluides (pas de liste à puces)
    - Professionnelle et précise
    - Facile à comprendre pour un comptable
    - Complète sans être trop technique

    N'inclus pas de titres de sections, rédige directement les paragraphes.
    """

    completion = safe_openai_call(
        client=client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )

    return completion.choices[0].message.content

