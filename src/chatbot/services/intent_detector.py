# chatbot/services/intent_detector.py

import re
from datetime import datetime, date

# Mapping mois français → numéro
MOIS_FR = {
    'janvier': 1, 'février': 2, 'fevrier': 2, 'mars': 3,
    'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7,
    'août': 8, 'aout': 8, 'septembre': 9, 'octobre': 10,
    'novembre': 11, 'décembre': 12, 'decembre': 12
}

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
    def _extract_month_year(text: str):
        """
        Cherche des expressions 'mois AAAA' dans le texte (ordre quelconque).
        Retourne une liste de (mois_num, annee) triée par ordre d'apparition.
        """
        pattern = r'(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\s+(20\d{2})'
        matches = re.findall(pattern, text, re.IGNORECASE)
        result = []
        for mois_str, annee_str in matches:
            mois_num = MOIS_FR.get(mois_str.lower())
            if mois_num:
                result.append((mois_num, int(annee_str)))
        return result

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
        # Priorité 1: dates au format DD/MM/YYYY ou DD-MM-YYYY
        date_matches = re.findall(r'(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})', user_input)
        date_matches = [d.replace('-', '/').replace('.', '/') for d in date_matches]

        # Priorité 2: expressions "mois AAAA"
        month_year_pairs = IntentDetector._extract_month_year(user_input_lower)

        # Toutes les années présentes dans le texte
        annees = re.findall(r'\b(20\d{2})\b', user_input)

        if not query_type and (date_matches or month_year_pairs or annees):
            query_type = 'analyse_globale'

        if not query_type:
            return None

        # 4. Construction des paramètres
        params = {}

        # Cas A: deux dates DD/MM/YYYY explicites
        if len(date_matches) >= 2:
            try:
                params['start_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
                params['end_date']   = datetime.strptime(date_matches[1], '%d/%m/%Y').date()
            except ValueError:
                pass
        elif len(date_matches) == 1:
            try:
                params['end_date'] = datetime.strptime(date_matches[0], '%d/%m/%Y').date()
            except ValueError:
                pass

        # Cas B: expressions "mois AAAA" (priorité si pas de dates DD/MM/YYYY)
        if not params.get('start_date') and not params.get('end_date') and month_year_pairs:
            if len(month_year_pairs) >= 2:
                # Plage : du premier mois au dernier mois détectés
                first = month_year_pairs[0]
                last  = month_year_pairs[-1]
                # 1er jour du premier mois
                params['start_date'] = date(first[1], first[0], 1)
                # Dernier jour du dernier mois
                import calendar
                last_day = calendar.monthrange(last[1], last[0])[1]
                params['end_date'] = date(last[1], last[0], last_day)
            elif len(month_year_pairs) == 1:
                # Un seul mois → tout le mois
                import calendar
                m, y = month_year_pairs[0]
                params['start_date'] = date(y, m, 1)
                last_day = calendar.monthrange(y, m)[1]
                params['end_date']   = date(y, m, last_day)

        # Cas C: années seules (si aucune date trouvée)
        if not params.get('start_date') and not params.get('end_date'):
            if annees:
                if len(annees) >= 2 and query_type == 'comparaison':
                    params['annee1'] = int(annees[0])
                    params['annee2'] = int(annees[1])
                elif len(annees) >= 2:
                    # Deux années différentes → plage (ex: "2024 et 2025")
                    y1, y2 = int(annees[0]), int(annees[-1])
                    if y1 != y2:
                        params['start_date'] = date(min(y1, y2), 1, 1)
                        params['end_date']   = date(max(y1, y2), 12, 31)
                    else:
                        params['annee'] = y1
                elif len(annees) == 1:
                    params['annee'] = int(annees[0])

        # 5. Filtre suggéré pour le frontend
        suggested_filter = {
            'type': 'date',
            'value': {
                'start': params.get('start_date').isoformat() if params.get('start_date') else
                         (date(params['annee'], 1, 1).isoformat() if 'annee' in params else None),
                'end':   params.get('end_date').isoformat() if params.get('end_date') else
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
