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
        Cherche des expressions 'mois AAAA' ou 'JJ mois AAAA' dans le texte.
        Retourne une liste de (jour, mois_num, annee) triée par ordre d'apparition.
        """
        # Mois avec gestion des typos communes (f[ée]vrier, fervier, etc.)
        mois_regex = r'(janvier|f[ée]vrier|fervier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)'
        
        # Pattern 1: DD mois YYYY
        pattern_full = rf'(\d{{1,2}})\s+{mois_regex}\s+(20\d{{2}})'
        matches_full = re.findall(pattern_full, text, re.IGNORECASE)
        
        # Pattern 2: mois YYYY
        pattern_month_year = rf'{mois_regex}\s+(20\d{{2}})'
        matches_my = re.findall(pattern_month_year, text, re.IGNORECASE)
        
        result = []
        # On garde une trace des positions pour trier si besoin, mais ici on va simplifier
        # Priorité aux dates complètes
        for d, m_str, y in matches_full:
            m_num = MOIS_FR.get(m_str.lower()) or 2 if 'ferv' in m_str.lower() else MOIS_FR.get(m_str.lower())
            if m_num:
                result.append(date(int(y), m_num, int(d)))
        
        # Ajouter les mois-années s'ils ne font pas partie d'une date complète déjà trouvée
        for m_str, y in matches_my:
            m_num = MOIS_FR.get(m_str.lower()) or 2 if 'ferv' in m_str.lower() else MOIS_FR.get(m_str.lower())
            if m_num:
                # Vérifier si on n'a pas déjà cette année/mois en date complète
                exists = any(d.year == int(y) and d.month == m_num for d in result)
                if not exists:
                    result.append((m_num, int(y)))
        
        return result

    @staticmethod
    def detect(user_input: str) -> dict:
        """
        Détecte l'intention et extrait les paramètres.
        """
        user_input_lower = user_input.lower()
        import calendar

        # 1. Détection du type de requête (Multi-intents)
        query_types = []
        for key, pattern in IntentDetector.PATTERNS.items():
            if re.search(pattern, user_input_lower):
                query_types.append(key)
        
        # Priorisation : Si on a des indicateurs précis, on peut supprimer 'analyse_globale'
        # pour éviter les messages d'avertissement inutiles.
        if any(t in query_types for t in ['ca', 'charges', 'ebe', 'roe', 'roa', 'bfr', 'tresorerie', 'resultat']):
            if 'analyse_globale' in query_types:
                query_types.remove('analyse_globale')

        if not query_types:
            query_type = None
        else:
            # Pour la rétrocompatibilité si besoin, on garde le premier
            query_type = query_types[0]

        # 2. Détection de la demande de détails
        demande_details = bool(re.search(
            r'détails?|liste|lignes?|ventil|décompos|tous les|chaque|par (date|compte|mois)|réparti|précis|exact|quels?|quelles?|combien|montant|composition',
            user_input_lower
        ))

        # 3. Extraction de toutes les dates possibles
        found_start_dates = []
        found_end_dates = []

        # A. Format numérique JJ/MM/AAAA
        date_matches = re.findall(r'(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})', user_input)
        for d_str in date_matches:
            d_str = d_str.replace('-', '/').replace('.', '/')
            try:
                # Gérer JJ/MM/AAAA ou J/M/AAAA
                dt = datetime.strptime(d_str, '%d/%m/%Y').date()
                found_start_dates.append(dt)
                found_end_dates.append(dt)
            except ValueError:
                pass

        # B. Format littéral (mars 2024, 03 mars 2024, etc.)
        literal_dates = IntentDetector._extract_month_year(user_input_lower)
        for d in literal_dates:
            if isinstance(d, date):
                found_start_dates.append(d)
                found_end_dates.append(d)
            else:
                m_num, y = d
                found_start_dates.append(date(y, m_num, 1))
                last_day = calendar.monthrange(y, m_num)[1]
                found_end_dates.append(date(y, m_num, last_day))

        # C. Années seules (en évitant les années faisant partie d'une date JJ/MM/AAAA ou mois AAAA)
        # On extrait d'abord toutes les occurrences potentielles d'années
        potential_years = re.findall(r'(?<![\/\-\.\d])(20\d{2})(?![\/\-\.\d])', user_input)
        annees = []
        for y_str in potential_years:
            y_val = int(y_str)
            # On vérifie si cette année n'est pas déjà présente dans les dates littérales trouvées
            is_redundant = False
            for d in literal_dates:
                if isinstance(d, date) and d.year == y_val:
                    is_redundant = True; break
                elif isinstance(d, tuple) and d[1] == y_val:
                    is_redundant = True; break
            
            if not is_redundant:
                annees.append(y_val)

        for y in annees:
            found_start_dates.append(date(y, 1, 1))
            found_end_dates.append(date(y, 12, 31))

        if not query_types and (found_start_dates or annees):
            query_types = ['analyse_globale']
            query_type = 'analyse_globale'

        if not query_types:
            return None

        # 4. Synthèse des paramètres
        params = {}
        
        if found_start_dates and found_end_dates:
            # Cas particulier comparaison : on garde les deux premières années si comparaison
            if query_type == 'comparaison' and len(annees) >= 2:
                params['annee1'] = annees[0]
                params['annee2'] = annees[1]
            else:
                # Sinon on prend l'enveloppe globale de toutes les dates mentionnées
                params['start_date'] = min(found_start_dates)
                params['end_date'] = max(found_end_dates)
                
                # Si l'enveloppe couvre exactement une année civile, on met aussi le flag 'annee'
                if params['start_date'].day == 1 and params['start_date'].month == 1 and \
                   params['end_date'].day == 31 and params['end_date'].month == 12 and \
                   params['start_date'].year == params['end_date'].year:
                    params['annee'] = params['start_date'].year

        # 5. Filtre suggéré pour le frontend
        suggested_filter = {
            'type': 'date',
            'value': {
                'start': params.get('start_date').isoformat() if params.get('start_date') else None,
                'end':   params.get('end_date').isoformat() if params.get('end_date') else None
            },
            'label': "Période personnalisée"
        }
        
        if 'annee' in params:
            suggested_filter['label'] = f"Année {params['annee']}"
        elif 'annee1' in params and 'annee2' in params:
            suggested_filter['label'] = f"Comparaison {params['annee1']} vs {params['annee2']}"

        return {
            'type': query_type,       # Premier intent (rétrocompatibilité)
            'types': query_types,     # Liste de tous les intents détectés
            'params': params,
            'include_details': demande_details,
            'suggested_filter': suggested_filter
        }
