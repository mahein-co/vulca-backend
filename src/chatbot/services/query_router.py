# chatbot/services/query_router.py

import re
from datetime import datetime
from .accounting_queries import AccountingQueryService
from .text_to_sql import TextToSQLService
from .intent_detector import IntentDetector

class QueryRouter:
    """
    Décide quelle stratégie utiliser pour répondre à la question.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.accounting_service = AccountingQueryService(project_id)
        self.sql_service = TextToSQLService(project_id)

    def route(self, question: str) -> dict:
        """Point d'entrée principal."""
        
        # Utiliser le nouveau détecteur d'intentions centralisé
        detection = IntentDetector.detect(question)
        
        if detection:
            intent = detection['type']
            params = detection['params']
            
            # Utiliser la méthode calculée existante
            return self._use_calculated_method(intent, params)
        else:
            # Text-to-SQL pour toutes les autres questions
            return self._use_text_to_sql(question)

    def _use_calculated_method(self, intent: str, params: dict) -> dict:
        method_map = {
            'ca':               self.accounting_service.get_chiffre_affaires,
            'charges':          self.accounting_service.get_charges,
            'roe':              self.accounting_service.get_roe,
            'roa':              self.accounting_service.get_roa,
            'bfr':              self.accounting_service.get_bfr,
            'ebe':              self.accounting_service.get_ebe,
            'marge_brute':      self.accounting_service.get_marge_brute,
            'marges':           self.accounting_service.get_marges_profitabilite,
            'rotation_stocks':  self.accounting_service.get_rotation_stocks,
            'ratios_structure': self.accounting_service.get_ratios_structure,
            'comparaison':      self.accounting_service.get_comparative_report,
            'bilan_structuré':  self.accounting_service.get_structured_bilan,
            # ── Ces deux intents utilisent maintenant la source de vérité unique ──
            'etats_financiers': self.accounting_service.get_dashboard_kpis,
            'resultat_structuré': self.accounting_service.get_resultat_net,
            'analyse_globale':  self.accounting_service.get_dashboard_kpis,
            'tresorerie':       self.accounting_service.get_tresorerie,
        }
        
        if intent not in method_map:
            return self._use_text_to_sql(f"Analyse {intent}")

        method = method_map[intent]
        
        # Gestion spéciale pour comparaison (nécessite 2 dates/années)
        if intent == 'comparaison' and 'annee1' in params and 'annee2' in params:
            result = method(annee1=params['annee1'], annee2=params['annee2'])
        else:
            # Passer tous les paramètres extraits (start_date, end_date, annee)
            result = method(**params)
        
        return {
            "source": "calculated",
            "intent": intent,
            "data": result
        }

    def _use_text_to_sql(self, question: str) -> dict:
        try:
            sql = self.sql_service.generate_sql(question)
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
        """Conservé pour compatibilité avec views.py mais délègue au nouveau service"""
        detection = IntentDetector.detect(question)
        return detection['type'] if detection else None
