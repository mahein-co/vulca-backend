import sys
import os

# Ajouter le répertoire src au path pour pouvoir importer ocr
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ocr.pcg_loader import get_account_suggestions

def test_mapping():
    test_cases = [
        "IBS / IR",
        "lbs/ir",
        "IBS",
        "IR",
        "Impôt sur les bénéfices",
        "Charge d'impôt"
    ]
    
    print("--- Test du Mapping Manuel PCG ---")
    for case in test_cases:
        suggestions = get_account_suggestions(case, top_n=1)
        if suggestions:
            best = suggestions[0]
            print(f"Libellé: '{case}' -> Compte: {best['numero_compte']} (Libellé: {best['libelle']}, Score: {best['score']})")
            if best['numero_compte'] == '695':
                print("  ✅ [OK]")
            else:
                print(f"  ❌ [ERREUR] Attendu: 695, Reçu: {best['numero_compte']}")
        else:
            print(f"Libellé: '{case}' -> ❌ Aucune suggestion trouvée")

    # Test pour le bilan
    case_bilan = "État, impôt sur les"
    suggestions = get_account_suggestions(case_bilan, top_n=1)
    if suggestions:
        best = suggestions[0]
        print(f"Libellé: '{case_bilan}' -> Compte: {best['numero_compte']} (Libellé: {best['libelle']}, Score: {best['score']})")
        if best['numero_compte'] == '444':
            print("  ✅ [OK]")
        else:
            print(f"  ❌ [ERREUR] Attendu: 444, Reçu: {best['numero_compte']}")

if __name__ == "__main__":
    test_mapping()
