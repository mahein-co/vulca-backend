import re
import os
import PyPDF2
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PCG = os.path.join(BASE_DIR, "plan-comptable-general-2005.pdf")

def simplify(text):
    if not text:
        return ""
    # Normaliser pour enlever les accents (é -> e)
    text = unicodedata.normalize('NFD', str(text).lower())
    text = "".join([c for c in text if unicodedata.category(c) != 'Mn'])
    # Garder uniquement l'alphanumérique
    return re.sub(r'[^a-z0-9]', '', text)

def load_pcg_mapping_from_pdf(pdf_path=PDF_PCG):
    """
    Lire le Plan Comptable Général 2005 et générer un mapping dynamique.
    """
    pcg_text = ""

    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pcg_text += text + "\n"

    # Ex: "401 Fournisseurs"
    # pattern = r"(\d{3,4})\s+([A-Za-zÀ-ÖØ-öø-ÿ\s'\-]+)"
    pattern = r"([1-7]\d{2,4})\s+([A-Za-zÀ-ÖØ-öø-ÿ\s'\-]+)"
    matches = re.findall(pattern, pcg_text)

    # dictionnaire dynamique PCG
    return {code.strip(): label.strip() for code, label in matches}
pcg_cache = None

def get_pcg_label(account_code: str):
    """
    Retourne le libellé le plus précis possible pour un numéro de compte.
    Gère les sous-comptes TVA (4456 / 4457 / 44566 / 44571…)
    """

    global pcg_cache
    if pcg_cache is None:
        pcg_cache = load_pcg_mapping_from_pdf()

    # 1️⃣ Recherche exacte 4 chiffres
    if len(account_code) >= 4:
        code4 = account_code[:4]
        if code4 in pcg_cache:
            return pcg_cache[code4]

    # 2️⃣ Recherche sur 3 chiffres
    code3 = account_code[:3]
    if code3 in pcg_cache:
        label = pcg_cache[code3]

        # 3️⃣ RÈGLES TVA SPÉCIALES
        if code3 == "445":
            # TVA déductible
            if account_code.startswith("4456"):
                return "TVA déductible"

            # TVA collectée
            if account_code.startswith("4457"):
                return "TVA collectée"

        return label  # libellé normal PCG

    # 4️⃣ Si rien trouvé
    return "-"


def get_account_suggestions(description: str, top_n: int = 5):
    """
    Suggère des comptes PCG basés sur une description textuelle.
    Utilise un score basé sur le nombre de mots-clés correspondants.
    """
    global pcg_cache
    if pcg_cache is None:
        try:
            pcg_cache = load_pcg_mapping_from_pdf()
        except Exception:
            pcg_cache = {}

    if not pcg_cache:
        pcg_cache = {}

    # 0️⃣ Mapping manuel pour les cas spécifiques (priorité absolue)
    # On utilise une comparaison "simplifiée" pour être robuste aux espaces/caractères spéciaux
    MANUAL_LABEL_MAPPING = {
        "ibs": "695",
        "ir": "695",
        "ibsir": "695",
        "lbsir": "695",
        "impotsurlesbenefices": "695",
        "impotsurlesbenefice": "695",
        "chargedimpot": "695",
        "etatimpotsurles": "444",
        "autresproduitsdegestioncourante": "758",
        "produitsdegestioncourante": "758",
        "produitdegestioncourante": "758",
        "dotation": "681",
        "dotations": "681",
        "amortissement": "681",
        "amortissements": "681",
        "dotationsauxamortissements": "681",
        "dotationsauxprovisions": "681",
        "dotationsauxamortissementsdesimmobilisationsincorporellesetcorporelles": "6811",
        "dotationsauxprovisionspourrisquesetcharges": "6815",
        "dotationsauxprovisionspourdepreciation": "6817",
    }

    desc_simplified = simplify(description)
    
    # Vérification directe ou par mot-clé pour IBS/IR
    target_code = None
    if desc_simplified in MANUAL_LABEL_MAPPING:
        target_code = MANUAL_LABEL_MAPPING[desc_simplified]
    elif "ibsir" in desc_simplified or "lbsir" in desc_simplified or desc_simplified in ["ibs", "ir"]:
        target_code = "695"

    if target_code:
        # Trouver le label correspondant s'il existe dans le cache, sinon utiliser une valeur par défaut
        label = pcg_cache.get(target_code, description.capitalize())
        return [{
            'numero_compte': target_code,
            'libelle': label,
            'score': 100 # Score maximum pour le mapping manuel
        }]

    # Nettoyage de la description pour la recherche
    words = re.findall(r'\w+', description.lower())
    if not words:
        return []

    suggestions = []
    for code, label in pcg_cache.items():
        label_lower = label.lower()
        score = 0
        for word in words:
            if len(word) > 2 and word in label_lower: # Ignorer les trop petits mots
                score += 1
        
        if score > 0:
            suggestions.append({
                'numero_compte': code,
                'libelle': label,
                'score': score
            })

    # Trier par score décroissant, puis par longueur de code (plus précis d'abord)
    suggestions.sort(key=lambda x: (x['score'], len(x['numero_compte'])), reverse=True)
    
    return suggestions[:top_n]


PCG_MAPPING = {
    # CLASSE 1 : CAPITAUX PROPRES & PASSIFS NON COURANTS
    '10': {'type_bilan': 'PASSIF', 'categorie': 'CAPITAUX_PROPRES'},
    '11': {'type_bilan': 'PASSIF', 'categorie': 'CAPITAUX_PROPRES'},
    '12': {'type_bilan': 'PASSIF', 'categorie': 'CAPITAUX_PROPRES'},
    '13': {'type_bilan': 'PASSIF', 'categorie': 'CAPITAUX_PROPRES'},
    '15': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_NON_COURANTS'},
    '16': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_NON_COURANTS'},
    '17': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_NON_COURANTS'},
    '18': {'type_bilan': 'PASSIF', 'categorie': 'CAPITAUX_PROPRES'},

    # CLASSE 2 : ACTIFS NON COURANTS
    '20': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_NON_COURANTS'},
    '21': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_NON_COURANTS'},
    '22': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_NON_COURANTS'},
    '26': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_NON_COURANTS'},
    '28': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_NON_COURANTS', 'is_negative': True},

    # CLASSE 3 : ACTIFS COURANTS (STOCKS)
    '30': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '31': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '32': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '35': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},

    # CLASSE 4 : COMPTES DE TIERS (COURANTS)
    '40': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '41': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '42': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '43': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '44': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},  # État - Impôts et taxes (442, 443, 444...) Etat, impôts et taxes
    '4456': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '4457': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '45': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '46': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '48': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},

    # CLASSE 5 : TRESORERIE
    '50': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '51': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},
    '519': {'type_bilan': 'PASSIF', 'categorie': 'PASSIFS_COURANTS'},
    '53': {'type_bilan': 'ACTIF', 'categorie': 'ACTIF_COURANTS'},

    # ===== CLASSE 6 : Charges =====
    '60': {'nature': 'CHARGE'},
    '61': {'nature': 'CHARGE'},
    '62': {'nature': 'CHARGE'},
    '63': {'nature': 'CHARGE'},
    '64': {'nature': 'CHARGE'},
    '65': {'nature': 'CHARGE'},
    '66': {'nature': 'CHARGE'},
    '67': {'nature': 'CHARGE'},
    '68': {'nature': 'CHARGE'},
    '69': {'nature': 'CHARGE'},

    # ===== CLASSE 7 : Produits =====
    '70': {'nature': 'PRODUIT'},
    '71': {'nature': 'PRODUIT'},
    '72': {'nature': 'PRODUIT'},
    '74': {'nature': 'PRODUIT'},
    '75': {'nature': 'PRODUIT'},
    '76': {'nature': 'PRODUIT'},
    '77': {'nature': 'PRODUIT'},
    '78': {'nature': 'PRODUIT'},
}
