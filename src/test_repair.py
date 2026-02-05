import json
import re

def repair_json(content):
    try:
        print(f"   [INFO] Tentative de reparation du JSON tronque...")
        content_fixed = content
        
        # 1. Fermer les chaînes de caractères si nécessaire
        # Simple count doesn't handle escaped quotes, but usually OpenAI doesn't escape many quotes in the values we expect
        if content_fixed.count('"') % 2 != 0:
            content_fixed += '"'
        
        # 2. Fermer les structures [ ] et { }
        open_braces = content_fixed.count('{') - content_fixed.count('}')
        open_brackets = content_fixed.count('[') - content_fixed.count(']')
        
        # Fermer d'abord les éléments internes
        for _ in range(open_brackets):
            content_fixed += ']'
        for _ in range(open_braces):
            content_fixed += '}'
        
        # Nettoyer les virgules orphelines
        content_fixed = re.sub(r',\s*}', '}', content_fixed)
        content_fixed = re.sub(r',\s*]', ']', content_fixed)
        
        return json.loads(content_fixed)
    except Exception as e:
        print(f"Error: {e}")
        return None

# Test case matching the user's error
truncated_content = """{
  "company_metadata": {
    "nom_entreprise": "IMMO-HM",
    "nif": "3000018099",
    "stat": null,
    "adresse": "LOT III U Y G FACE"
  },
  "columns": ["Colonne1", "Colonne2", "Colonne3", "Colonne4", "Colonne5", "Colonne6", "Colonne7"],
  "rows": [
    ["DECLARATION", null, null, null, null, null, null],
    ["BUREAU", null, null, null, null, null, null],
    ["de", "SERVICE DES", null, null, null, null, null],
    ["C.C.P", null, null, null, null, null, null],
    ["IMPOT SUR LES", null, n"""

result = repair_json(truncated_content)
print(f"Result: {result}")
