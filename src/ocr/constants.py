from ocr.pcg_loader import load_pcg_mapping_from_pdf


UNIFIED_EXTRACTION_PROMPT = """
Tu es un expert comptable et analyste de données de haute précision. 

TON OBJECTIF : Extraire TOUTES les informations financières et administratives d'un document, même si le texte est incomplet ou manuscrit.

RÈGLES CRITIQUES :
- Réponds UNIQUEMENT avec un objet JSON. 
- Aucun texte avant ou après.
- Si un champ est incertain, fais une estimation intelligente basée sur le contexte.

STRUCTURE DU JSON ATTENDUE :
{
  "is_professional": boolean, // true SAUF si le document est manifestement personnel (photo de vacances, dessin, etc.)
  "document_type": "ACHAT" | "VENTE" | "BANQUE" | "CAISSE" | "OD" | "PAIE",
  "numero_facture": string, // Le numéro le plus probable (Facture, Devis, Proforma)
  "date": "YYYY-MM-DD",
  "client": string,
  "fournisseur": string,
  "montant_ht": number,
  "montant_tva": number,
  "montant_ttc": number,
  "devise": "MGA" | "EUR" | "USD" | etc.,
  "description": string // Résumé clair du contenu
}

RÈGLES POUR is_professional :
- Mettre TRUE pour : factures fournisseur/client, devis, proformas, relevé bancaire, reçus, bons d'achat, fiches de paie, JIRAMA (eau/électricité), TELMA, ORANGE, factures de services publics, notes de frais, tout document avec un montant.
- Mettre FALSE SEULEMENT si le document est clairement non-comptable : photo personnelle, dessin, texte sans montant, document vide.
- EN CAS DE DOUTE : mettre TRUE.

CONSEILS D'EXTRACTION :
- CHIFFRES : Ignore les espaces. Remplace la virgule par un point (ex: 650 000,00 -> 650000.00).
- DEVIS/PROFORMA : Considère-les comme pro (is_professional: true) et classifie en "OD".
- MANUSCRIT : Lis attentivement les montants écrits à la main (ex: KIT CAR).
- FACTURES JIRAMA/TELMA/ORANGE/services publics : is_professional=true, document_type="ACHAT".
"""

CLASSE_PROMPT_TEMPLATE = """
Tu es expert-comptable malgache, spécialiste du Plan Comptable Général 2005.
On te fournit un JSON issu d'un document (facture, paiement, virement, etc.).
1) Classifie le type de document (facture_fournisseur / facture_client / encaissement / paiement_fournisseur / autre).
2) Propose un "journal" d'écriture sous forme JSON avec date, libelle et une liste d'écritures (compte, sens: debit|credit, montant).
3) Utilise les comptes PCG usuels (ex: 401, 411, 512, 606, 4456, 707...).
4) Retourne UNIQUEMENT l'objet JSON final, sans texte d'accompagnement.


Input JSON : {json_in}
"""


PCG_MAPPING = load_pcg_mapping_from_pdf()


# Liste des feuilles Excel à ignorer lors de l'importation
EXCLUDED_SHEET_NAMES = [
    'INSTRUCTIONS', 
    'SOMMAIRE', 
    'METADATA', 
    'CONFIG', 
    'PARAMETRES'
]

