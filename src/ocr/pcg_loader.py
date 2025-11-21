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
    pattern = r"(\d{3,4})\s+([A-Za-zÀ-ÖØ-öø-ÿ\s'\-]+)"
    matches = re.findall(pattern, pcg_text)

    # dictionnaire dynamique PCG
    return {code.strip(): label.strip() for code, label in matches}
