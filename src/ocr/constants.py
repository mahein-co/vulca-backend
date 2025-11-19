EXTRACTION_FIELDS_PROMPT = f"""
Tu es un expert en extraction de données de pièces comptables.

Analyse le texte fourni et :

1. Identifie automatiquement tous les champs pertinents (numéro facture, Montant TTC, TVA, client, dates, identifiants, devise, etc.) mais sans le details ni des adresses, et ne mets pas d'espace entre les nombres puis stock toujours l'unité monétaire dans le champs devise et met en MGA pour l'Ar et autre pour les autres devises d'un autre pays.

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


PCG_MAPPING = {
    # Achats
    "601": "Achats stockés - matières premières",
    "602": "Achats stockés - autres approvisionnements",
    "606": "Achats non stockés (fournitures)",
    "607": "Achats de marchandises",

    # Charges externes
    "615": "Entretien et réparations",
    "616": "Primes d’assurance",
    "622": "Honoraires",
    "623": "Publicité",
    "625": "Déplacements, missions, réceptions",
    "626": "Frais postaux et télécommunications",
    "627": "Services bancaires",

    # Personnel
    "641": "Rémunérations du personnel",
    "645": "Charges sociales",

    # Impôts et taxes
    "635": "Autres impôts, taxes et versements assimilés",

    # Produits
    "706": "Prestations de services",
    "707": "Ventes de marchandises",

    # TVA
    "4456": "TVA déductible",
    "44562": "TVA sur immobilisations",
    "44566": "TVA sur autres biens et services",
    "4457": "TVA collectée",

    # Trésorerie
    "512": "Banque",
    "531": "Caisse",

    # Tiers
    "401": "Fournisseurs",
    "411": "Clients"
}

