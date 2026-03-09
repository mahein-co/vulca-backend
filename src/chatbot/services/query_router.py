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
        
        detection = IntentDetector.detect(question)
        
        if detection:
            # On utilise maintenant la liste 'types' pour agréger les résultats
            intents = detection['types']
            params = detection['params']
            
            return self._use_calculated_methods(intents, params)
        else:
            return self._use_text_to_sql(question)

    def _use_calculated_methods(self, intents: list, params: dict) -> dict:
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
            'etats_financiers': self.accounting_service.get_dashboard_kpis,
            'resultat_structuré': self.accounting_service.get_resultat_net,
            'analyse_globale':  self.accounting_service.get_structured_bilan,
            'tresorerie':       self.accounting_service.get_tresorerie,
            'bilan':            self.accounting_service.get_structured_bilan,
            'resultat':         self.accounting_service.get_resultat_net,
            'tva':              self.accounting_service.get_tva_report,
            'factures':         self.accounting_service.get_impayes_report,
            'anomalies':        self.accounting_service.detect_anomalies,
            'grand_livre':      self.accounting_service.get_compte_details,
        }
        
        results = {}
        for intent in intents:
            if intent in method_map:
                import inspect
                method = method_map[intent]
                try:
                    # Inspection de la signature pour ne passer que les arguments valides
                    sig = inspect.signature(method)
                    valid_params = {
                        k: v for k, v in params.items() 
                        if k in sig.parameters
                    }
                    
                    if intent == 'comparaison':
                        if 'start_date1' in params and 'start_date2' in params:
                            results[intent] = self.accounting_service.compare_custom_periods(
                                start1=params['start_date1'], end1=params['end_date1'],
                                start2=params['start_date2'], end2=params['end_date2']
                            )
                        elif 'annee1' in params and 'annee2' in params:
                            results[intent] = method(annee1=params['annee1'], annee2=params['annee2'])
                        else:
                            # Fallback si pas assez de params pour comparaison
                            results[intent] = method(**valid_params)
                    else:
                        results[intent] = method(**valid_params)
                except Exception as e:
                    print(f"[ERROR] Error calling method for intent {intent}: {str(e)}")
                    results[intent] = {"error": str(e)}

        if not results:
            return self._use_text_to_sql(f"Analyse {' '.join(intents)}")

        return {
            "source": "calculated",
            "intents": intents,
            "data": results
        }

    def _use_calculated_method(self, intent: str, params: dict) -> dict:
        """Conservé pour rétrocompatibilité"""
        return self._use_calculated_methods([intent], params)

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
