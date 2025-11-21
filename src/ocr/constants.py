from ocr.pcg_loader import load_pcg_mapping_from_pdf


EXTRACTION_FIELDS_PROMPT = """
Tu es un expert en extraction de données de pièces comptables.

Analyse le texte fourni et :

1. Identifie automatiquement tous les champs pertinents (numéro facture, Montant TTC, TVA, client, dates, identifiants, devise, etc.) ainsi que les details possible. Ne mets pas d'espace entre les nombres puis stock toujours l'unité monétaire dans le champs devise et mets en MGA pour l'Ar et autre pour les autres devises d'un autre pays.

2. Donne-moi un JSON propre
3. Utilise des clés JSON standardisées en snake_case
4. Inclus uniquement les champs réellement présents dans le texte
5. N'invente pas de valeurs
6. Ne renvoie que du JSON, sans ``` ni texte autour
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
