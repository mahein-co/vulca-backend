import PyPDF2
import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PCG = os.path.join(BASE_DIR, "plan-comptable-general-2005.pdf")

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
