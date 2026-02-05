"""
Script de vérification : Test de l'extraction JSON et génération du journal
"""
import json

# ===================================================================
# TEST 1: Vérification du mapping des clés
# ===================================================================
print("=" * 80)
print("TEST 1: Verification du mapping des cles")
print("=" * 80)

# Simulation de données extraites en anglais
sample_extracted_json = {
    "invoice_number": "2024-001",
    "invoice_date": "05/09/2024",
    "client_name": "SociétéABC",
    "total_invoice_amount": 1200000,
    "total_ht": 1000000,
    "vat_amount": 200000,
    "items": [
        {
            "quantity": 5,
            "unit_price": 200000,
            "amount": 1000000
        }
    ],
    "type_document": "VENTE"
}

# Mapping utilisé dans ocr/views.py (version mise à jour)
keys_mapping = {
    "client_number": "numero_client",
    "client": "nom_client",
    "client_name": "nom_client",
    "invoice_number": "numero_facture",
    "bank": "banque",
    "bank_name": "nom_banque",
    "bank_account": "numero_compte_bancaire",
    "vat_rate": "taux_tva",
    "vat_amount": "montant_tva",
    "tax_amount": "montant_tva",
    "total_invoice_amount": "montant_ttc",
    "total": "montant_ttc",
    "total_ttc": "montant_ttc",
    "amount_total": "montant_ttc",
    "subtotal": "sous_total",
    "subtotal_ht": "montant_ht",
    "total_ht": "montant_ht",
    "amount_ht": "montant_ht",
    "currency": "devise",
    "supplier": "fournisseur",
    "supplier_name": "nom_fournisseur",
    "invoice_date": "date",
    "due_date": "date_echeance",
    "description": "description",
    "type_document": "type_document",
    "items": "details",
    "details": "details",
    "quantity": "quantite",
    "unit_price": "prix_unitaire",
    "price": "prix_unitaire",
    "amount": "montant"
}

def translate_keys(obj, mapping):
    """Renomme les clefs d'un dict récursivement selon mapping."""
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            new_key = mapping.get(k, k)
            new[new_key] = translate_keys(v, mapping)
        return new
    elif isinstance(obj, list):
        return [translate_keys(item, mapping) for item in obj]
    else:
        return obj

# Application du mapping
translated_json = translate_keys(sample_extracted_json, keys_mapping)

print("\n[INFO] JSON TRADUIT :")
print(json.dumps(translated_json, indent=2, ensure_ascii=False))

# Vérification des champs attendus
expected_fields = ["numero_facture", "montant_ttc", "montant_ht", "montant_tva", "nom_client", "date", "type_document", "details"]
missing_fields = [f for f in expected_fields if f not in translated_json]

if missing_fields:
    print(f"\n[ERROR] : Champs manquants : {missing_fields}")
else:
    print(f"\n[SUCCESS] : Tous les champs attendus sont presents")

# Vérification des détails
if "details" in translated_json and len(translated_json["details"]) > 0:
    detail = translated_json["details"][0]
    expected_detail_fields = ["quantite", "prix_unitaire", "montant"]
    missing_detail_fields = [f for f in expected_detail_fields if f not in detail]
    
    if missing_detail_fields:
        print(f"[ERROR] : Champs manquants dans details : {missing_detail_fields}")
    else:
        print(f"[SUCCESS] : Tous les champs de details sont presents")
        print(f"   - quantite: {detail['quantite']}")
        print(f"   - prix_unitaire: {detail['prix_unitaire']}")
        print(f"   - montant: {detail['montant']}")

# ===================================================================
# TEST 2: Simulation de l'affichage du journal
# ===================================================================
print("\n" + "=" * 80)
print("TEST 2: Simulation de l'affichage du journal")
print("=" * 80)

# Données simulées pour le journal
sample_journal_data = {
    "type_journal": "VENTE",
    "numero_piece": "000063628901",
    "date": "2024-09-05",
    "lignes": [
        {
            "id": 1,
            "compte": "707",
            "libelle": "Ventes marchandises",
            "debit": 0,
            "credit": 190101
        },
        {
            "id": 2,
            "compte": "4457",
            "libelle": "TVA collectée",
            "debit": 0,
            "credit": 38020
        },
        {
            "id": 3,
            "compte": "411",
            "libelle": "Client CONNECTIC",
            "debit": 228121,
            "credit": 0
        }
    ]
}

# Simulation de l'affichage (comme dans compta/views.py)
print("\n" + "=" * 80)
print("START GENERATE JOURNAL VIEW")
print(f"   Input data keys: {list(translated_json.keys())}")
print()
print("=" * 50)
print(f"[INFO] JOURNAL GENERE (Type: {sample_journal_data['type_journal']}, Piece: {sample_journal_data['numero_piece']})")
print("-" * 50)

for idx, line in enumerate(sample_journal_data["lignes"], start=1):
    compte = line["compte"]
    libelle = line["libelle"]
    debit = int(line["debit"]) if line["debit"] else 0
    credit = int(line["credit"]) if line["credit"] else 0
    print(f"Ligne {idx}: {compte} - {libelle} | Debit: {debit} | Credit: {credit}")

print("=" * 50)
print()

print("\n" + "=" * 80)
print("[SUCCESS] TOUS LES TESTS SONT TERMINES")
print("=" * 80)
