# chatbot/services/intent_detector.py

import re
from datetime import datetime, date

class IntentDetector:
    """
    Service centralisé pour détecter l'intention de l'utilisateur
    et extraire les paramètres financiers (dates, années, etc.)
    """

    PATTERNS = {
        'ca': r'chiffre.*affaires?|ca\b|ventes?|revenus?',
        'charges': r'charges?|dépenses?|coûts?|frais',
        'ebe': r'ebe\b|excédent brut d\'exploitation',
        'roe': r'roe\b|rentabilité des capitaux propres',
        'marge_brute': r'marge brute|marge commerciale',
        'bfr': r'bfr\b|besoin en fonds de roulement',
        'roa': r'roa\b|rentabilité des actifs',
        'leverage': r'leverage\b|levier Financier|endettement',
        'marge_nette': r'marge nette',
        'marge_operationnelle': r'marge opérationnelle',
        'current_ratio': r'current ratio|ratio de liquidité',
        'rotation_stocks': r'rotation des stocks|rotation stock',
        'resultat': r'résultat|bénéfice|profit|perte',
        'tresorerie': r'trésorerie|liquidité|banque|caisse',
        'bilan': r'bilan|actif|passif|capitaux propres',
        'etats_financiers': r'[éée]tats? financiers?',
        'comparaison': r'compar|différence|évolution|versus|vs',
        'analyse_globale': r'analyser|interpréter|audit|santé|vue|résumé|situation|dashboard|tableau|rapport|exercice|période'
    }

    @staticmethod
    def detect(user_input: str) -> dict:
        """
        Détecte l'intention et extrait les paramètres.
        """
        user_input_lower = user_input.lower()
        
        # 1. Détection du type de requête
        query_type = None
        for key, pattern in IntentDetector.PATTERNS.items():
            if re.search(pattern, user_input_lower):
                query_type = key
                break
        
        # 2. Détection de la demande de détails
        demande_details = bool(re.search(
            r'détails?|liste|lignes?|ventil|décompos|tous les|chaque|par (date|compte|mois)|réparti|précis|exact|quels?|quelles?|combien|montant|composition',
            user_input_lower
        ))

        # 3. Extraction des dates et années
        date_matches = re.findall(r'(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})', user_input)
        annees = re.findall(r'\b(20\d{2})\b', user_input)
        
        # Normalisation
        date_matches = [d.replace('-', '/').replace('.', '/') for d in date_matches]
        
        if not query_type and (date_matches or annees):
            query_type = 'analyse_globale'
            
        if not query_type:
            return None

        # 4. Construction des paramètres
        params = {}
        
        # Dates précises
        if len(date_matches) >= 2:
            try:
                params['start_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
                params['end_date'] = datetime.strptime(date_matches[1], '%d/%m/%Y').date()
            except ValueError:
                pass
        elif len(date_matches) == 1:
            try:
                params['end_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
            except ValueError:
                pass
                
        # Années
        if not params.get('start_date') and not params.get('end_date'):
            if annees:
                if len(annees) >= 2 and query_type == 'comparaison':
                    params['annee1'] = int(annees[0])
                    params['annee2'] = int(annees[1])
                elif len(annees) == 1:
                    params['annee'] = int(annees[0])

        # 5. Filtre suggéré pour le frontend
        suggested_filter = {
            'type': 'date',
            'value': {
                'start': params.get('start_date').isoformat() if params.get('start_date') else 
                         (date(params['annee'], 1, 1).isoformat() if 'annee' in params else None),
                'end': params.get('end_date').isoformat() if params.get('end_date') else 
                       (date(params['annee'], 12, 31).isoformat() if 'annee' in params else None)
            },
            'label': f"Période {params.get('annee')}" if 'annee' in params else "Période personnalisée"
        }
        
        if 'annee1' in params and 'annee2' in params:
             suggested_filter['label'] = f"Comparaison {params['annee1']} vs {params['annee2']}"

        return {
            'type': query_type,
            'params': params,
            'include_details': demande_details,
            'suggested_filter': suggested_filter
        }
