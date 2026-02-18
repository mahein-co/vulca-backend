# chatbot/services/query_router.py

import re
from .accounting_queries import AccountingQueryService
from .text_to_sql import TextToSQLService

class QueryRouter:
    """
    Décide quelle stratégie utiliser pour répondre à la question.
    
    Stratégie 1: Méthodes existantes (calculs complexes : ROE, BFR, EBE...)
    Stratégie 2: Text-to-SQL (tout le reste : questions libres, détails, filtres custom)
    """

    # Questions qui bénéficient des méthodes calculées existantes
    CALCULATED_PATTERNS = {
        'roe':              [r'roe', r'rentabilit.* capitaux'],
        'roa':              [r'roa', r'rentabilit.* actif'],
        'bfr':              [r'bfr', r'besoin.*fonds.*roulement'],
        'ebe':              [r'\bebe\b', r'excédent brut'],
        'marge_brute':      [r'marge brute'],
        'marges':           [r'marge nette', r'marge opérat'],
        'rotation_stocks':  [r'rotation.*stock', r'stock.*rotation'],
        'ratios_structure': [r'leverage', r'current ratio', r'ratio.*structure'],
    }

    def __init__(self, project_id: int, llm_client):
        self.project_id = project_id
        self.llm_client = llm_client
        self.accounting_service = AccountingQueryService(project_id)
        self.sql_service = TextToSQLService(project_id)

    def route(self, question: str) -> dict:
        """Point d'entrée principal."""
        
        annee = self._extract_year(question)
        intent = self._detect_calculated_intent(question)

        if intent:
            # Utiliser la méthode calculée existante
            return self._use_calculated_method(intent, annee)
        else:
            # Text-to-SQL pour toutes les autres questions
            return self._use_text_to_sql(question)

    def _use_calculated_method(self, intent: str, annee: int | None) -> dict:
        method_map = {
            'roe':              self.accounting_service.get_roe,
            'roa':              self.accounting_service.get_roa,
            'bfr':              self.accounting_service.get_bfr,
            'ebe':              self.accounting_service.get_ebe,
            'marge_brute':      self.accounting_service.get_marge_brute,
            'marges':           self.accounting_service.get_marges_profitabilite,
            'rotation_stocks':  self.accounting_service.get_rotation_stocks,
            'ratios_structure': self.accounting_service.get_ratios_structure,
        }
        method = method_map[intent]
        result = method(annee=annee) if annee else method()
        
        return {
            "source": "calculated",
            "intent": intent,
            "data": result
        }

    def _use_text_to_sql(self, question: str) -> dict:
        try:
            sql = self.sql_service.generate_sql(question, self.llm_client)
            results = self.sql_service.execute(sql)
            
            return {
                "source": "text_to_sql",
                "sql": sql,
                "nb_resultats": len(results),
                "data": results
            }
        except ValueError as e:
            return {
                "source": "error",
                "error": str(e),
                "data": []
            }

    def _detect_calculated_intent(self, question: str) -> str | None:
        q = question.lower()
        for intent, patterns in self.CALCULATED_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, q):
                    return intent
        return None

    def _extract_year(self, question: str) -> int | None:
        match = re.search(r'\b(20\d{2})\b', question)
        return int(match.group(1)) if match else None