# chatbot/services/intent_detector.py

import re
from datetime import datetime, date

# Mapping mois français → numéro (incluant abréviations)
MOIS_FR = {
    'janvier': 1, 'janv': 1, 'jan': 1,
    'février': 2, 'fevrier': 2, 'fév': 2, 'fev': 2, 'fervier': 2,
    'mars': 3, 'mar': 3,
    'avril': 4, 'avr': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7, 'juil': 7,
    'août': 8, 'aout': 8,
    'septembre': 9, 'sept': 9, 'sep': 9,
    'octobre': 10, 'oct': 10,
    'novembre': 11, 'nov': 11,
    'décembre': 12, 'decembre': 12, 'déc': 12, 'dec': 12
}

class IntentDetector:
    """
    Service centralisé pour détecter l'intention de l'utilisateur
    et extraire les paramètres financiers (dates, années, etc.)
    """

    PATTERNS = {
        'ca': r'chiffre.*affaires?|ca\b|ventes?|revenus?',
        'charges': r'charges?|dépenses?|coûts?|frais',
        'ebe': r'ebe\b|exc[eé]dent\s*brut',
        'roe': r'roe\b|rentabilité.*capit',
        'marge_brute': r'marge\s*brute|marge\s*commerciale',
        'bfr': r'bfr\b|besoin.*fonds.*roulement',
        'roa': r'roa\b|rentabilité.*actif',
        'leverage': r'leverage\b|levier\s*Financier|endettement',
        'marge_nette': r'marge\s*nette',
        'marge_operationnelle': r'marge\s*opérationnelle',
        'current_ratio': r'current\s*ratio|ratio\s*liquidité',
        'rotation_stocks': r'rotation.*stock',
        'resultat': r'r[eéè]sul|b[eéè]n[eéè]fice|profit|perte',
        'tresorerie': r'tr[eé]sorerie|liquidité|banque|caisse',
        'bilan': r'bilan|actif|passif|capit',
        'etats_financiers': r'[éée]tats? financiers?',
        'tva': r'tva\b|taxe.*valeur.*ajoutée',
        'factures': r'factures?|clients?|fournisseurs?|impay[ée]s?',
        'anomalies': r'anomalies?|erreurs?|doublons?|déséquilibre',
        'grand_livre': r'solde|mouvements?|compte\s\d+',
        'comparaison': r'compar|différence|évolution|versus|vs',
        'export': r'générer|export|rapport|télécharger|excel|pdf',
        'analyse_globale': r'analyser|interpréter|audit|santé|vue|résumé|situation|dashboard|tableau|rapport|exercice|période'
    }

    @staticmethod
    def _extract_month_year(text: str):
        """
        Cherche des expressions 'mois AAAA' ou 'JJ mois AAAA' dans le texte.
        Supporte les abréviations.
        """
        # Construction de la regex dynamique à partir de MOIS_FR
        clés_triées = sorted(MOIS_FR.keys(), key=len, reverse=True)
        mois_regex = '(' + '|'.join(clés_triées) + ')'
        
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
                # Vérifier si on n'a pas déjà cette année/mois en date complète ou mois-année
                exists = any(
                    (d.year == int(y) and d.month == m_num) if isinstance(d, date)
                    else (d[1] == int(y) and d[0] == m_num)
                    for d in result
                )
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
        potential_years = re.findall(r'(?<![\/\-\.\d])(20\d{2})(?![\/\-\.\d])', user_input)
        annees = []
        for y_str in potential_years:
            y_val = int(y_str)
            is_redundant = False
            for d in literal_dates:
                if isinstance(d, date) and d.year == y_val:
                    is_redundant = True; break
                elif isinstance(d, tuple) and d[1] == y_val:
                    is_redundant = True; break
            
            if not is_redundant:
                annees.append(y_val)

        # D. Trimestres (T1, T2, T3, T4, 1er trimestre, premier trimestre, etc.)
        quarter_pattern = r'\b(t|trimestre|trim)\.?\s*([1-4])\b|([1-4])(?:er|ème|eme)?\s+(?:trimestre|trim)\b|(premier|deuxième|troisième|quatrième|quatrieme)\s+trimestre\b'
        quarter_matches = re.finditer(quarter_pattern, user_input_lower)
        
        # Mapping pour les trimestres littéraux
        q_map = {'premier': 1, '1': 1, 'deuxième': 2, 'deuxieme': 2, '2': 2, 'troisième': 3, 'troisieme': 3, '3': 3, 'quatrième': 4, 'quatrieme': 4, '4': 4}

        temp_quarters = []
        for match in quarter_matches:
            groups = match.groups()
            q_val = None
            if groups[1]: q_val = q_map.get(groups[1]) # Cas "T1" ou "Trimestre 1"
            elif groups[2]: q_val = q_map.get(groups[2]) # Cas "1er trimestre"
            elif groups[3]: q_val = q_map.get(groups[3]) # Cas "premier trimestre"
            
            if q_val:
                temp_quarters.append(q_val)
        
        if temp_quarters and potential_years:
            for idx, q in enumerate(temp_quarters):
                if idx < len(potential_years):
                    y = int(potential_years[idx])
                else:
                    y = int(potential_years[-1])
                
                q_start_month = (q - 1) * 3 + 1
                q_end_month = q * 3
                found_start_dates.append(date(y, q_start_month, 1))
                last_day = calendar.monthrange(y, q_end_month)[1]
                found_end_dates.append(date(y, q_end_month, last_day))
                if y in annees: annees.remove(y)
        
        # Ajouter les années restantes (Annuel)
        for y in annees:
            found_start_dates.append(date(y, 1, 1))
            found_end_dates.append(date(y, 12, 31))

        # E. MOTS-CLÉS RELATIFS
        today = date.today()
        if re.search(r'aujourd\'hui|ce jour|maintenant|actuel', user_input_lower):
            found_end_dates.append(today)
            if not found_start_dates:
                found_start_dates.append(today)

        if re.search(r'ce mois-ci|ce mois\b', user_input_lower):
            found_start_dates.append(date(today.year, today.month, 1))
            found_end_dates.append(today)

        if re.search(r'moils? dernier|le mois passé', user_input_lower):
            prev_month = today.month - 1 or 12
            prev_year = today.year if today.month > 1 else today.year - 1
            found_start_dates.append(date(prev_year, prev_month, 1))
            last_day = calendar.monthrange(prev_year, prev_month)[1]
            found_end_dates.append(date(prev_year, prev_month, last_day))

        # "L'année dernière"
        if re.search(r'ann[ée]e\s+derni[èe]re|ann[ée]e\s+pr[ée]c[ée]dente', user_input_lower):
            prev_year = today.year - 1
            found_start_dates.append(date(prev_year, 1, 1))
            found_end_dates.append(date(prev_year, 12, 31))

        # "6 derniers mois"
        if re.search(r'6\s+derniers?\s+mois', user_input_lower):
            # Reculer de 6 mois à partir d'aujourd'hui
            start_month = today.month - 6
            start_year = today.year
            while start_month <= 0:
                start_month += 12
                start_year -= 1
            found_start_dates.append(date(start_year, start_month, 1))
            found_end_dates.append(today)
        else:
            # Autre nombre de mois
            match_last_months = re.search(r'(\d+)\s+derniers?\s+mois', user_input_lower)
            if match_last_months:
                nb_months = int(match_last_months.group(1))
                start_month = today.month - nb_months
                start_year = today.year
                while start_month <= 0:
                    start_month += 12
                    start_year -= 1
                found_start_dates.append(date(start_year, start_month, 1))
                found_end_dates.append(today)

        # F. Détection de mots-clés de PLAGE (Start/End)
        is_range_query = bool(re.search(r'\b(entre|de|du|depuis|à partir de|de la période)\b', user_input_lower))
        is_depuis = bool(re.search(r'\b(depuis|à partir de)\b', user_input_lower))
        
        # G. Détection automatique de comparaison
        has_explicit_comparison = bool(re.search(r'\b(comparer|comparaison|versus|vs|par rapport à)\b', user_input_lower))
        
        # Cas de comparaison complexe (T1 2025 vs T1 2026)
        # Si on a EXACTEMENT deux start_dates et deux end_dates et qu'on demande une comparaison
        if len(found_start_dates) == 2 and has_explicit_comparison:
            query_types.append('comparaison')
            if not query_type: query_type = 'comparaison'
        elif len(set(potential_years)) >= 2 and not is_range_query:
            query_types.append('comparaison')
            if not query_type: query_type = 'comparaison'
        elif has_explicit_comparison and 'comparaison' not in query_types:
            query_types.append('comparaison')
            if not query_type: query_type = 'comparaison'

        if not query_types and (found_start_dates or potential_years):
            query_types = ['analyse_globale']
            query_type = 'analyse_globale'

        if not query_types:
            return None

        # 4. Synthèse des paramètres
        params = {}
        
        # Extraction des numéros de compte
        comptes_detectes = re.findall(r'\b(\d{3,6})\b', user_input)
        if comptes_detectes:
            filtered_comptes = [c for c in comptes_detectes if not (c.startswith('20') and len(c) == 4)]
            if filtered_comptes:
                params['numero_compte'] = filtered_comptes[0]
                if 'grand_livre' not in query_types:
                    query_types.append('grand_livre')

        if found_start_dates and found_end_dates:
            if query_type == 'comparaison' and len(found_start_dates) >= 2:
                # Si comparaison d'années entières
                if all(d.month == 1 and d.day == 1 for d in found_start_dates) and \
                   all(d.month == 12 and d.day == 31 for d in found_end_dates):
                    params['annee1'] = sorted(list(set(d.year for d in found_start_dates)))[0]
                    params['annee2'] = sorted(list(set(d.year for d in found_start_dates)))[1]
                else:
                    # Comparaison de périodes (T1 vs T1 par exemple)
                    # On passera les dates brutes au router pour qu'il gère
                    params['start_date1'] = found_start_dates[0]
                    params['end_date1'] = found_end_dates[0]
                    params['start_date2'] = found_start_dates[1]
                    params['end_date2'] = found_end_dates[1]
            else:
                # Sinon on prend l'enveloppe globale
                params['start_date'] = min(found_start_dates)
                params['end_date'] = max(found_end_dates)
                
                # Règle "depuis [date]" -> jusqu'à aujourd'hui
                if is_depuis and not re.search(r'jusqu\'à|au|à|fin|jusquau', user_input_lower):
                    params['end_date'] = today
                
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
