# chatbot/services/text_to_sql.py

from django.db import connection
import json
import re

class TextToSQLService:
    """
    Traduit une question en langage naturel en requête SQL
    et retourne les résultats bruts.
    """

    DB_SCHEMA = """
    Tables PostgreSQL disponibles (toujours filtrer par project_id):

    compta_journal:
      - id, project_id, date (DATE), numero_piece (VARCHAR)
      - numero_compte (VARCHAR), libelle (VARCHAR)
      - montant_debit (DECIMAL), montant_credit (DECIMAL)

    compta_grandlivre:
      - id, project_id, date (DATE)
      - numero_compte (VARCHAR), libelle (VARCHAR)
      - montant_debit (DECIMAL), montant_credit (DECIMAL)

    compta_balance:
      - id, project_id, date (DATE)
      - numero_compte (VARCHAR), libelle (VARCHAR)
      - solde_debit (DECIMAL), solde_credit (DECIMAL)
      - nature (VARCHAR: 'ACTIF' ou 'PASSIF')

    compta_compteresultat:
      - id, project_id, date (DATE)
      - numero_compte (VARCHAR), libelle (VARCHAR)
      - montant_ar (DECIMAL)
      - nature (VARCHAR: 'PRODUIT' ou 'CHARGE')

    compta_bilan:
      - id, project_id, date (DATE)
      - numero_compte (VARCHAR), libelle (VARCHAR)
      - montant_ar (DECIMAL)
      - type_bilan (VARCHAR: 'ACTIF' ou 'PASSIF')
      - categorie (VARCHAR: 'ACTIF_COURANTS', 'ACTIF_NON_COURANTS', 
                             'PASSIFS_COURANTS', 'PASSIFS_NON_COURANTS', 
                             'CAPITAUX_PROPRES')

    Plan Comptable Général (PCG 2005) - Malgache:
      - 70x → Ventes / Chiffre d'affaires
      - 60x → Achats de marchandises
      - 61x-62x → Services extérieurs
      - 63x → Impôts et taxes
      - 64x → Charges de personnel
      - 65x → Autres charges exploitation
      - 66x → Charges financières
      - 67x → Charges exceptionnelles
      - 68x → Dotations amortissements
      - 71x-72x → Production stockée/immobilisée
      - 74x → Subventions exploitation
      - 75x → Autres produits exploitation
      - 76x → Produits financiers
      - 77x → Produits exceptionnels
      - 1xx → Capitaux propres et dettes long terme
      - 2xx → Immobilisations
      - 3xx → Stocks
      - 40x → Dettes fournisseurs
      - 41x → Créances clients
      - 51x → Banques
      - 53x → Caisse
    """

    def __init__(self, project_id: int):
        self.project_id = int(project_id)

    def generate_sql(self, question: str, llm_client) -> str:
        """Demande au LLM de générer le SQL."""
        
        prompt = f"""Tu es un expert SQL et comptabilité française/malgache (PCG 2005).

Schéma de la base de données:
{self.DB_SCHEMA}

RÈGLES ABSOLUES:
1. Toujours inclure WHERE project_id = {self.project_id} (ou AND project_id = {self.project_id})
2. Uniquement des requêtes SELECT
3. Ajouter LIMIT 200 sauf si agrégation (SUM, COUNT, AVG...)
4. Pour les dates: utiliser EXTRACT(YEAR FROM date) = 2023 pour filtrer par année
5. Les montants sont en Ariary malgache (MGA)
6. Utiliser COALESCE(montant, 0) pour éviter les NULL
7. ORDER BY date DESC par défaut pour les listes

Question: {question}

Retourne UNIQUEMENT la requête SQL brute, sans markdown, sans explication."""

        # Adapter selon ton client LLM (OpenAI, Anthropic, etc.)
        sql = llm_client.generate(prompt)
        return self._clean_sql(sql)

    def execute(self, sql: str) -> list[dict]:
        """Exécute la requête SQL de façon sécurisée."""
        self._validate_sql(sql)
        
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchmany(200)
        
        # Convertir en liste de dicts
        results = []
        for row in rows:
            row_dict = {}
            for col, val in zip(columns, row):
                # Convertir les types non-sérialisables
                if hasattr(val, 'strftime'):
                    row_dict[col] = val.strftime('%d/%m/%Y')
                elif hasattr(val, '__float__'):
                    row_dict[col] = float(val)
                else:
                    row_dict[col] = val
            results.append(row_dict)
        
        return results

    def _validate_sql(self, sql: str):
        """Sécurité : rejette tout ce qui n'est pas un SELECT."""
        cleaned = sql.upper().strip()
        
        if not cleaned.startswith('SELECT'):
            raise ValueError("Seules les requêtes SELECT sont autorisées.")
        
        dangerous = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 
                     'CREATE', 'TRUNCATE', 'GRANT', 'REVOKE', '--', ';--']
        for word in dangerous:
            if word in cleaned:
                raise ValueError(f"Requête non autorisée (mot-clé: {word})")
        
        # Vérifier que project_id est bien dans la requête
        if f'PROJECT_ID = {self.project_id}' not in cleaned:
            raise ValueError("La requête doit filtrer par project_id")

    def _clean_sql(self, raw: str) -> str:
        """Nettoie la réponse du LLM."""
        sql = raw.strip()
        sql = re.sub(r'```sql\s*', '', sql)
        sql = re.sub(r'```\s*', '', sql)
        # Supprimer les points-virgules finaux (sécurité)
        sql = sql.rstrip(';').strip()
        return sql