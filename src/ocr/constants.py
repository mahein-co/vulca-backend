from ocr.pcg_loader import load_pcg_mapping_from_pdf


EXTRACTION_FIELDS_PROMPT = """
Tu es un expert en extraction de données de pièces comptables.

Analyse le texte fourni et :

1. Identifie automatiquement tous les champs pertinents (numéro facture, Montant TTC, Montant HT (Hors Taxe), TVA, client, dates, identifiants, devise, Objet/Description, etc.) ainsi que les details possible. Ne mets pas d'espace entre les nombres puis stock toujours l'unité monétaire dans le champs devise et mets en MGA pour l'Ar et autre pour les autres devises d'un autre pays.

2. Donne-moi un JSON propre
3. Utilise des clés JSON standardisées en snake_case (ex: montant_ht, montant_tva, montant_ttc)
4. Inclus uniquement les champs réellement présents dans le texte
5. N'invente pas de valeurs
6. Ne renvoie que du JSON, sans ``` ni texte autour
7. IMPORTANT : Pour les montants, remplace la VIRGULE par un POINT (ex: "190101,00" -> 190101.00). Ne renvoie JAMAIS d'entiers si le montant a des décimales.

8. ⚠️ CRITIQUE - NUMÉRO DE FACTURE (OBLIGATOIRE) :
   Le numéro de facture est LE champ le plus important pour identifier un document.
   
   ⚠️ ATTENTION : Ne confonds PAS le numéro de facture avec :
   - Le numéro de client (N°Client, Client ID)
   - Le numéro de compte
   - Le numéro de téléphone
   
   Cherche ACTIVEMENT dans TOUT le texte les patterns suivants :
   - "NeFacure XXX" ou "N°Facture XXX" (ex: "NeFacure 0000636289")
   - "Facture N°XXX" ou "FACTURE N°XXX"
   - "N°XXX" ou "N° XXX" (ex: "N°001", "N° 2024-123")
   - "Invoice #XXX" ou "Invoice: XXX"
   - "Ref: XXX" ou "REF: XXX" ou "Référence: XXX"
   - "Numéro: XXX" ou "NUMERO: XXX"
   - "#XXX" près du mot FACTURE/INVOICE
   - Tout numéro isolé sur la même ligne que "FACTURE" ou "INVOICE"
   
   Exemple ORANGE :
   Texte: "N°Client NeFacure Date facture"
          "1.00391850 0000636289 01/06/2020"
   → "numero_facture": "0000636289" (PAS "1.00391850" qui est le N°Client)
   
   Le numéro peut contenir : chiffres, lettres, tirets, slashes
   Exemples valides : "001", "0000636289", "FAC-2024-001", "2024/001", "INV-12345"
   
   TOUJOURS inclure "numero_facture" dans le JSON.
   Si vraiment aucun numéro trouvé après recherche exhaustive, utilise null.
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
