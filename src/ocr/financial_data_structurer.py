"""
Module de structuration automatique de données financières.

Ce module fournit des outils pour :
- Détecter le type de document (BILAN/COMPTE_RESULTAT)
- Nettoyer et normaliser les années (2021.00 → 2021)
- Trier les colonnes par année croissante
- Classifier les comptes selon le Plan Comptable Général (PCG)
- Structurer les données en format JSON standardisé
"""

import pandas as pd
import re
from typing import Dict, List, Tuple, Optional, Any
from decimal import Decimal
from datetime import datetime, date

# Import du validateur de compte
from ocr.account_validator import AccountValidator

class FinancialDataStructurer:
    """
    Classe principale pour la structuration de données financières.
    """
    
    # Mapping des classes comptables selon le PCG
    ACCOUNT_CLASSES = {
        '1': 'Capitaux propres',
        '2': 'Immobilisations',
        '3': 'Stocks',
        '4': 'Tiers',
        '5': 'Trésorerie',
        '6': 'Charges',
        '7': 'Produits'
    }
    
    def __init__(self):
        """Initialise le structureur de données financières."""
        self.account_validator = AccountValidator()
    
    def detect_document_type(self, df: pd.DataFrame, columns_mapping: Dict[str, str]) -> Tuple[str, float]:
        """
        Détecte si le document est un BILAN, COMPTE_RESULTAT ou JOURNAL.
        
        Stratégies:
        1. Vérifier colonnes Débit/Crédit (JOURNAL)
        2. Analyser mots-clés dans le contenu (BILAN/CR sans numéros de compte)
        3. Analyser numéros de compte (BILAN/CR avec comptes)
        
        Args:
            df: DataFrame contenant les données
            columns_mapping: Mapping des colonnes
            
        Returns:
            tuple: (type_document, confidence)
                - type_document: 'BILAN', 'COMPTE_RESULTAT', 'JOURNAL' ou 'UNKNOWN'
                - confidence: Score de confiance (0.0 à 1.0)
        """
        # STRATÉGIE 1: Vérifier colonnes Débit/Crédit (JOURNAL) - PRIORITÉ ABSOLUE
        columns_lower = [str(col).lower() for col in df.columns]
        
        has_debit = any('debit' in col or 'débit' in col for col in columns_lower)
        has_credit = any('credit' in col or 'crédit' in col for col in columns_lower)
        
        if has_debit and has_credit:
            # C'est un Journal - RETOUR IMMÉDIAT, ne pas continuer l'analyse
            has_date = any('date' in col for col in columns_lower)
            has_compte = any('compte' in col for col in columns_lower)
            has_piece = any('piece' in col or 'pièce' in col or 'piéce' in col for col in columns_lower)
            
            # Vérifier aussi le nom de la feuille
            sheet_name = df.attrs.get('sheet_name', '').upper() if hasattr(df, 'attrs') else ''
            if 'JOURNAL' in sheet_name or 'GRAND LIVRE' in sheet_name:
                return 'JOURNAL', 0.98
            
            if has_date and has_compte and has_piece:
                return 'JOURNAL', 0.95
            elif has_date and has_compte:
                return 'JOURNAL', 0.90
            else:
                return 'JOURNAL', 0.80
        
        
        # STRATÉGIE 2: Analyser mots-clés dans le contenu ET le nom de la feuille
        # Concaténer tout le texte des 20 premières lignes
        text_content = ''
        for idx, row in df.head(20).iterrows():
            text_content += ' '.join(str(cell).upper() for cell in row if pd.notna(cell)) + ' '
        
        # Ajouter le nom de la feuille au contexte
        sheet_name = df.attrs.get('sheet_name', '').upper() if hasattr(df, 'attrs') else ''
        text_content += ' ' + sheet_name
        
        # Mots-clés pour COMPTE DE RESULTAT
        cr_keywords = ['COMPTE DE RESULTAT', 'COMPTE DE RÉSULTAT', 'PRODUITS', 'CHARGES', 
                       'TOTAL PRODUITS', 'TOTAL CHARGES', 'RESULTAT NET', 'CDR']
        cr_score = sum(1 for kw in cr_keywords if kw in text_content)
        
        # Mots-clés pour BILAN (ACTIF ou PASSIF)
        bilan_keywords = ['BILAN', 'ACTIF', 'PASSIF', 'TOTAL ACTIF', 'TOTAL PASSIF',
                          'CAPITAUX PROPRES', 'IMMOBILISATIONS'] # Retiré TRESORERIE car ambigu avec Flux de Trésorerie
        
        # Mots-clés spécifiques pour exclure les Flux de Trésorerie
        flux_keywords = ['FLUX DE TRESORERIE', 'FLUX DE TRÉSORERIE', 'FL TRESO', 'TABLEAU DE FLUX', 'CASH FLOW']
        is_flux = any(kw in text_content for kw in flux_keywords)
        
        if is_flux:
            print(f"   [INFO] Détecté comme Flux de Trésorerie (exclu): {sheet_name}")
            return 'UNKNOWN', 0.0
            
        bilan_score = sum(1 for kw in bilan_keywords if kw in text_content)
        
        # Décision basée sur les scores
        if cr_score >= 2:  # Au moins 2 mots-clés CR
            confidence = min(0.95, 0.70 + (cr_score * 0.05))
            return 'COMPTE_RESULTAT', confidence
            
        if bilan_score >= 1:  # Au moins 1 mot-clé BILAN (ACTIF ou PASSIF suffit)
            # Augmenter la confiance si le nom de la feuille contient ACTIF ou PASSIF
            if 'ACTIF' in text_content or 'PASSIF' in text_content:
                confidence = 0.95
            else:
                confidence = min(0.85, 0.60 + (bilan_score * 0.05))
            return 'BILAN', confidence
        
        # STRATÉGIE 3: Analyser les numéros de compte pour Bilan/CR
        account_col = columns_mapping.get('compte')
        
        if not account_col or account_col not in df.columns:
            # Chercher une colonne qui contient des numéros de compte
            for col in df.columns:
                non_na = df[col].dropna()
                if not non_na.empty:
                    # Vérifier si ce sont des numéros de compte (3-6 chiffres)
                    matches = sum(1 for x in non_na if re.match(r'^\d{3,6}$', str(x).strip()))
                    if len(non_na) > 0 and matches / len(non_na) > 0.6:
                        account_col = col
                        break
        
        if account_col and account_col in df.columns:
            counts = {'class_67': 0, 'class_15': 0, 'total': 0}
            for val in df[account_col].dropna():
                val_str = str(val).strip()
                if val_str.startswith(('6', '7')):
                    counts['class_67'] += 1
                elif val_str.startswith(('1', '2', '3', '4', '5')):
                    counts['class_15'] += 1
                counts['total'] += 1
            
            if counts['total'] > 5:
                if counts['class_67'] / counts['total'] > 0.7:
                    return 'COMPTE_RESULTAT', 0.90
                elif counts['class_15'] / counts['total'] > 0.7:
                    return 'BILAN', 0.90
        
        return 'UNKNOWN', 0.0
    
    def normalize_years(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Nettoie les années dans les colonnes.
        Convertit 2021.00, 2022.0 → 2021, 2022
        
        Args:
            df: DataFrame avec colonnes potentiellement mal formatées
            
        Returns:
            DataFrame avec colonnes nettoyées
        """
        # Créer une copie pour éviter les modifications inattendues
        df_cleaned = df.copy()
        
        # Renommer les colonnes
        new_columns = []
        for col in df_cleaned.columns:
            col_str = str(col)
            
            # Cas 1: Détecter si c'est une année avec décimales (2021.0, 2021.00, etc.)
            year_match = re.match(r'^(\d{4})\.0+$', col_str)
            if year_match:
                # Extraire l'année sans décimales
                year = int(year_match.group(1))
                new_columns.append(year)
                print(f"    Colonne nettoye: '{col}'  {year}")
                continue
            
            # Cas 2: Si c'est déjà un nombre (int ou float)
            if isinstance(col, (int, float)):
                # Vérifier si c'est une année (1900-2100)
                try:
                    year_val = int(col)
                    if 1900 <= year_val <= 2100:
                        new_columns.append(year_val)
                        if col != year_val:  # Si changement
                            print(f"    Colonne nettoye: '{col}'  {year_val}")
                        continue
                except (ValueError, TypeError):
                    pass
            
            # Cas 3: Si c'est une string qui ressemble à une année
            try:
                # Essayer de convertir en float puis int
                num_val = float(col_str)
                year_val = int(num_val)
                # Vérifier si c'est une année valide
                if 1900 <= year_val <= 2100 and num_val == year_val:
                    new_columns.append(year_val)
                    if col_str != str(year_val):
                        print(f"    Colonne nettoye: '{col}'  {year_val}")
                    continue
            except (ValueError, TypeError):
                pass
            
            # Cas 4: Garder la colonne telle quelle
            new_columns.append(col)
        
        df_cleaned.columns = new_columns
        
        return df_cleaned
    
    def extract_year_columns(self, df: pd.DataFrame) -> List[Any]:
        """
        Identifie les colonnes contenant des années ou des montants.
        
        Args:
            df: DataFrame
            
        Returns:
            Liste des années/colonnes de données détectées (triées)
        """
        years_or_montants = []
        
        for col in df.columns:
            # Si c'est déjà un int et que c'est une année
            if isinstance(col, int) and 1900 <= col <= 2100:
                years_or_montants.append(col)
                continue
            
            col_str = str(col)
            
            # Recherche d'une année (format 20XX ou 19XX) n'importe où dans le titre
            year_match = re.search(r'\b(19|20)\d{2}\b', col_str)
            if year_match:
                # Si c'est JUSTE l'année, on l'ajoute comme int
                if re.match(r'^\s*(19|20)\d{2}\s*$', col_str):
                    years_or_montants.append(int(year_match.group(0)))
                else:
                    # Sinon on garde l'intitulé complet (ex: "Net 2024")
                    years_or_montants.append(col)
                continue
            
            # Recherche de mentions N ou N-1 (Cas classique français)
            if re.search(r'\bN(-1)?\b', col_str):
                years_or_montants.append(col)
                continue

            # Vérifier si c'est un nombre qui ressemble à une année
            try:
                num_val = float(col_str)
                year_val = int(num_val)
                if 1900 <= year_val <= 2100 and num_val == year_val:
                    years_or_montants.append(year_val)
                    continue
            except (ValueError, TypeError):
                pass
        
        # Si aucune année trouvée, chercher des colonnes de montant génériques
        if not years_or_montants:
            generic_montant_keywords = ['montant', 'net', 'solde', 'balance', 'valeur', 'total', 'debit', 'credit']
            for col in df.columns:
                col_str = str(col).lower()
                if any(kw in col_str for kw in generic_montant_keywords):
                    # On garde la colonne si elle n'est pas déjà le compte ou le libellé
                    years_or_montants.append(col)
                    print(f"   [INFO] Colonne montant générique détectée comme fallback: {col}")
        
        # Supprimer les doublons tout en gardant l'ordre original autant que possible
        seen = set()
        result = []
        for x in years_or_montants:
            if x not in seen:
                result.append(x)
                seen.add(x)
        
        return result
    
    def sort_columns_by_year(self, df: pd.DataFrame, columns_mapping: Dict[str, str]) -> pd.DataFrame:
        """
        Trie les colonnes par année croissante.
        Garde les colonnes non-année (compte, libellé) en premier.
        
        Args:
            df: DataFrame
            columns_mapping: Mapping des colonnes
            
        Returns:
            DataFrame avec colonnes triées
        """
        # Identifier les colonnes fixes (compte, libellé, etc.)
        fixed_cols = []
        year_cols = []
        other_cols = []
        
        years = self.extract_year_columns(df)
        
        for col in df.columns:
            col_str = str(col)
            
            # Colonnes fixes en premier
            if col in columns_mapping.values():
                fixed_cols.append(col)
            # Colonnes années
            elif col in years or (isinstance(col, int) and col in years):
                year_cols.append(col)
            # Autres colonnes
            else:
                other_cols.append(col)
        
        # Trier les années
        year_cols_sorted = sorted(year_cols)
        
        # Réorganiser le DataFrame
        new_order = fixed_cols + year_cols_sorted + other_cols
        
        # Supprimer les doublons tout en préservant l'ordre
        seen = set()
        new_order_unique = []
        for col in new_order:
            if col not in seen:
                seen.add(col)
                new_order_unique.append(col)
        
        return df[new_order_unique]
    
    def classify_account(self, numero_compte: str) -> Dict[str, Any]:
        """
        Classifie un compte selon le Plan Comptable Général.
        
        Args:
            numero_compte: Numéro de compte
            
        Returns:
            dict: {
                'classe': int (1-7),
                'classe_libelle': str,
                'is_bilan': bool,
                'is_compte_resultat': bool
            }
        """
        if not numero_compte:
            return {
                'classe': None,
                'classe_libelle': None,
                'is_bilan': False,
                'is_compte_resultat': False
            }
        
        # Nettoyer le numéro de compte
        clean_account = re.sub(r'[^0-9]', '', str(numero_compte))
        
        if not clean_account:
            return {
                'classe': None,
                'classe_libelle': None,
                'is_bilan': False,
                'is_compte_resultat': False
            }
        
        # Extraire la classe (premier chiffre)
        first_digit = clean_account[0]
        
        if first_digit not in '1234567':
            return {
                'classe': None,
                'classe_libelle': None,
                'is_bilan': False,
                'is_compte_resultat': False
            }
        
        classe = int(first_digit)
        classe_libelle = self.ACCOUNT_CLASSES.get(first_digit, 'Inconnu')
        
        return {
            'classe': classe,
            'classe_libelle': classe_libelle,
            'is_bilan': classe in [1, 2, 3, 4, 5],
            'is_compte_resultat': classe in [6, 7]
        }
    
    def structure_to_json(
        self, 
        df: pd.DataFrame, 
        columns_mapping: Dict[str, str],
        document_type: str,
        company_metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Structure les données en format JSON standardisé.
        
        Format de sortie:
        {
            "type_document": "BILAN" ou "COMPTE_RESULTAT",
            "annees": [2021, 2022, 2023, 2024],
            "lignes": [
                {
                    "poste": "CAPITAUX PROPRES",
                    "classe": 1,
                    "valeurs": {
                        "2021": 1000000,
                        "2022": 1000000,
                        "2023": 1000000,
                        "2024": 1000000
                    }
                }
            ]
        }
        
        Args:
            df: DataFrame avec données nettoyées et triées
            columns_mapping: Mapping des colonnes
            document_type: 'BILAN' ou 'COMPTE_RESULTAT'
            
        Returns:
            dict: Structure JSON standardisée
        """
        # Extraire les années
        years = self.extract_year_columns(df)
        
        # SI AUCUNE ANNÉE DÉTECTÉE, essayer d'utiliser la colonne 'montant' comme fallback
        montant_col = columns_mapping.get('montant')
        print(f"   [DEBUG] Years detected: {years}")
        print(f"   [DEBUG] Montant column: {montant_col}")
        
        if not years and montant_col and montant_col in df.columns:
            print(f"   [INFO] Aucune colonne année détectée. Utilisation de la colonne montant: {montant_col}")
            # Essayer de trouver une année de référence
            # 1. Dans les métadonnées (le titre du bilan contient souvent l'année)
            # 2. Dans la date de la première ligne si présente
            # 3. Année en cours par défaut
            target_year = None
            
            # Essayer de trouver une date ou une année dans df si possible, sinon metadata
            from datetime import date
            target_year = date.today().year
            
            if company_metadata:
                for key, val in company_metadata.items():
                    if val and isinstance(val, str):
                        match = re.search(r'\b(20\d{2})\b', val)
                        if match:
                            target_year = int(match.group(1))
                            print(f"   [INFO] Année extraite de company_metadata['{key}']: {target_year}")
                            break
            
            years = [target_year]
            # Créer un alias de colonne temporaire pour que la boucle de valeurs fonctionne
            df = df.copy()
            df[target_year] = df[montant_col]
            print(f"   [INFO] Colonne {target_year} créée à partir de {montant_col}")
        
        # Identifier les colonnes
        compte_col = columns_mapping.get('compte')
        libelle_col = columns_mapping.get('libelle')
        
        if not compte_col or compte_col not in df.columns:
            # Si pas de colonne 'compte' explicite, chercher une colonne qui en contient
            # PRIORITÉ: Une colonne appelée exactement "compte"
            exact_match = next((col for col in df.columns if str(col).lower().strip() in ['compte', 'compte n°', 'n° compte', 'numero compte', 'numéro compte']), None)
            if exact_match:
                compte_col = exact_match
                print(f"   [INFO] Colonne compte détectée par correspondance exacte: {exact_match}")
            else:
                # Sinon, chercher par nom partiel avec exclusions
                for col in df.columns:
                    col_str = str(col).lower()
                    if 'compte' in col_str and not any(x in col_str for x in ['libelle', 'libellé', 'intitule', 'intitulé', 'piece', 'pièce', 'description', 'date']):
                        compte_col = col
                        print(f"   [INFO] Colonne compte détectée par nom: {col}")
                        break
            
            # Priorité 2: Chercher une colonne avec des numéros de compte (3-6 chiffres UNIQUEMENT)
            if not compte_col:
                for col in df.columns:
                    non_na = df[col].dropna()
                    if len(non_na) > 0:
                        # Vérifier si ce sont des numéros de compte (3-6 chiffres, PAS 7+)
                        # Exclure les montants qui sont généralement > 1000000 (7+ chiffres)
                        matches = sum(1 for x in non_na if re.match(r'^\d{3,6}$', str(x).strip()))
                        # Au moins 60% des valeurs doivent être des comptes valides
                        if matches / len(non_na) > 0.6:
                            compte_col = col
                            print(f"   [INFO] Colonne compte détectée par pattern: {col}")
                            break
            
            # Si toujours pas trouvé, essayer d'utiliser la colonne libellé comme fallback
            if not compte_col:
                print(f"   [WARNING] Aucune colonne 'compte' détectée. Utilisation du libellé comme clé.")
                # Ne pas lever d'erreur, on va utiliser le libellé comme identifiant
                compte_col = libelle_col if libelle_col and libelle_col in df.columns else None
        
        # Si libelle_col n'est pas défini, essayer de le trouver
        if not libelle_col or libelle_col not in df.columns:
            # Chercher une colonne de texte qui pourrait être le libellé
            for col in df.columns:
                col_str = str(col).lower()
                if any(x in col_str for x in ['libelle', 'libellé', 'description', 'designation', 'poste', 'intitule', 'intitulé']):
                    libelle_col = col
                    print(f"   [INFO] Colonne libellé détectée par nom: {col}")
                    break
            
            # Si toujours pas trouvé, prendre la première colonne string qui n'est pas le compte ni une année
            if not libelle_col:
                for col in df.columns:
                    if col == compte_col or col in years:
                        continue
                    # Vérifier si c'est une colonne string
                    if df[col].dtype == 'object':
                        # Vérifier que ce n'est pas des nombres
                        non_na = df[col].dropna()
                        if len(non_na) > 0:
                            is_numeric = all(re.match(r'^[\d\s\.,]+$', str(x)) for x in non_na.head(5))
                            if not is_numeric:
                                libelle_col = col
                                print(f"   [INFO] Colonne libellé détectée par type: {col}")
                                break

        # Si toujours pas de compte_col mais qu'on a trouvé un libelle_col, utiliser le libellé comme fallback
        if not compte_col and libelle_col:
             compte_col = libelle_col
             print(f"   [INFO] Utilisation de la colonne libellé trouvée comme clé de compte: {libelle_col}")


        
        # Construire les lignes
        lignes = []
        
        for idx, row in df.iterrows():
            # Essayer d'extraire le numéro de compte
            numero_compte_raw = str(row.get(compte_col, '')).strip() if compte_col else ''
            val_compte = str(row.get(compte_col, '')).strip() if compte_col else ''
            
            # Vérifier si c'est un vrai numéro de compte (3-6 chiffres)
            # Si c'est un grand numéro (montant), l'ignorer
            is_valid_account = False
            numero_compte = ""
            
            # Cas 1: C'est un numéro de compte valide (3-6 chiffres)
            if re.match(r'^\d{3,6}$', val_compte):
                numero_compte = val_compte
                is_valid_account = True
            
            # Cas 2: La colonne compte a été utilisée comme fallback libellé
            elif compte_col == libelle_col:
                numero_compte = "" # Pas de numéro de compte
            
            # Cas 3: C'est un texte (peut-être un libellé mal placé)
            elif val_compte and not re.match(r'^\d+$', val_compte):
                # Ce n'est pas un chiffre, donc probablement pas un compte ni un montant
                # On l'ignore comme numéro de compte
                pass

            poste = str(row.get(libelle_col, '')).strip() if libelle_col else ''
            
            # Si pas de poste mais qu'on a utilisé le compte comme libellé
            if not poste and compte_col == libelle_col:
                poste = val_compte
                
            # Si toujours pas de poste, essayer la colonne 'DESCRIPTION' ou la première colonne string
            if not poste:
                for col in df.columns:
                    if df[col].dtype == 'object' and col != compte_col:
                        val = str(row[col]).strip()
                        if val and not re.match(r'^\d+$', val):
                            poste = val
                            break

            # Nettoyer et normaliser le libellé pour affichage (Correction OCR)
            if poste:
                poste_clean = str(poste).strip()
                poste_lower = poste_clean.lower()
                
                # Remplacements pour affichage propre
                if 'caïtal' in poste_lower or 'caital' in poste_lower:
                    poste = "Capital"
                elif 'emprnts' in poste_lower or 'emprnt' in poste_lower:
                    poste = "Emprunts"
                elif 'repot' in poste_lower and 'nouveau' in poste_lower:
                    poste = "Report à nouveau"
                elif 'creance' in poste_lower and 'client' in poste_lower:
                    poste = "Créances clients"
                elif 'dette' in poste_lower and 'fournisseur' in poste_lower:
                    poste = "Dettes fournisseurs"
                elif 'dette' in poste_lower and ('fiscal' in poste_lower or 'social' in poste_lower):
                    poste = "Dettes fiscales et sociales"
            
            # Identifier si c'est un sous-total (Commence par un chiffre suivi d'un tiret ou mot-clé TOTAL)
            is_subtotal = False
            if poste:
                poste_upper = poste.upper()
                if re.match(r'^\d+\s*-\s+', poste) or any(kw in poste_upper for kw in ['TOTAL', 'RESULTAT', 'VARIATION', 'VALEUR AJOUTEE', 'EXCEDENT']):
                    is_subtotal = True
                    # Si c'est un sous-total, on vide le numéro de compte s'il est suspect
                    if numero_compte and len(numero_compte) <= 3:
                        numero_compte = ""
                        is_valid_account = False

            # Si on n'a ni compte valide ni poste, ignorer la ligne
            if not is_valid_account and not poste:
                continue

            # Déterminer la classe
            classification = {'classe': None, 'classe_libelle': ''}
            
            if is_valid_account or (poste and not is_subtotal):
                # Validation et correction automatique du compte
                validation = self.account_validator.validate_account(
                    numero_compte, 
                    poste, 
                    document_type
                )
                
                # Appliquer la correction SENSÉMENT
                if validation['is_corrected'] and validation['suggested_account']:
                    # Si c'est une erreur OCR flagrante ou un compte manquant, on applique la suggestion
                    # Sauf si l'utilisateur a explicitement demandé de rester "brut" (mais ici il veut du "smart")
                    if not numero_compte or validation['reason']:
                        numero_compte = validation['suggested_account']
                        is_valid_account = True
                    
                # Si le compte est TOUJOURS manquant, essayer de le deviner à nouveau
                if not numero_compte and poste and not is_subtotal:
                    suggested = self.account_validator.suggest_account_from_label(poste, document_type)
                    if suggested:
                        numero_compte = suggested
                        is_valid_account = True

                # Mettre à jour la classification avec le compte final (corrigé ou deviné)
                classification = self.classify_account(numero_compte)

                # Si la classification par numéro de compte échoue mais qu'on a une classe suggérée par le libellé
                if not classification['classe'] and validation['suggested_class']:
                    classification['classe'] = int(validation['suggested_class'])
                    classification['classe_libelle'] = self.ACCOUNT_CLASSES.get(str(validation['suggested_class']), 'Inconnu')

                # Si toujours pas de libellé de poste, utiliser le libellé de la classe
                if not poste:
                    poste = classification.get('classe_libelle', '')
            
            # Extraire les valeurs
            valeurs = {}
            has_nonzero = False
            for year in years:
                if year in df.columns:
                    value = row.get(year)
                    
                    # Nettoyer la valeur
                    if pd.isna(value):
                        valeurs[str(year)] = 0
                    else:
                        try:
                            # Convertir en nombre (gérer formats FR: 1 234,56)
                            if isinstance(value, str):
                                val_clean = value.replace(' ', '').replace('\xa0', '').replace(',', '.')
                                # Supprimer les caractères non numériques excepté le point et le signe moins
                                val_clean = re.sub(r'[^\d\.-]', '', val_clean)
                                num_value = float(val_clean)
                            else:
                                num_value = float(value)
                                
                            if num_value != 0:
                                has_nonzero = True
                                
                            # Si c'est un entier, garder comme int
                            if num_value == int(num_value):
                                valeurs[str(year)] = int(num_value)
                            else:
                                valeurs[str(year)] = num_value
                        except (ValueError, TypeError):
                            valeurs[str(year)] = 0
            
            # Ajouter la ligne seulement si elle a une valeur ou un poste significatif
            # On accepte une ligne à 0 si elle a un compte valide ou si c'est un sous-total important
            is_significant = has_nonzero or is_valid_account or (poste and len(poste) > 5)
            
            if is_significant:
                ligne = {
                    "poste": poste,
                    "numero_compte": numero_compte,
                    "classe": classification.get('classe'),
                    "classe_libelle": classification.get('classe_libelle'),
                    "valeurs": valeurs,
                    "is_subtotal": is_subtotal
                }
                lignes.append(ligne)
        
        # Construire la structure finale
        result = {
            "type_document": document_type,
            "annees": years,
            "lignes": lignes
        }
        
        # Ajouter les métadonnées d'entreprise si disponibles
        if company_metadata:
            result["company_metadata"] = company_metadata
        
        return result
    
    def process_dataframe(
        self, 
        df: pd.DataFrame, 
        columns_mapping: Dict[str, str],
        company_metadata: Optional[Dict[str, str]] = None,
        pre_detected_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Traitement complet d'un DataFrame : détection, nettoyage, tri, structuration.
        
        Args:
            df: DataFrame source
            columns_mapping: Mapping des colonnes
            pre_detected_type: Type de document déjà détecté (fallback)
            
        Returns:
            dict: Structure JSON complète
        """
        print("\n DBUT DU TRAITEMENT FINANCIER")
        print("=" * 80)
        
        # 1. Détecter le type de document
        print("[INFO] Etape 1: Detection du type de document...")
        document_type, confidence = self.detect_document_type(df, columns_mapping)
        
        # Fallback sur le type pré-détecté si le type actuel est UNKNOWN
        if document_type == 'UNKNOWN' and pre_detected_type and pre_detected_type != 'UNKNOWN':
            print(f"    [INFO] Utilisation du type pré-détecté comme fallback: {pre_detected_type}")
            document_type = pre_detected_type
            confidence = 1.0
            
        print(f"    Type dtect: {document_type} (confiance: {confidence:.2%})")
        
        # 2. Normaliser les années
        print("\n tape 2: Normalisation des annes...")
        df_normalized = self.normalize_years(df)
        years_before = [str(col) for col in df.columns if re.match(r'^\d{4}', str(col))]
        years_after = [str(col) for col in df_normalized.columns if re.match(r'^\d{4}', str(col))]
        print(f"   Avant: {years_before}")
        print(f"   Aprs: {years_after}")
        
        # 3. Trier les colonnes par année
        print("\n[INFO] Etape 3: Tri des colonnes par annee...")
        df_sorted = self.sort_columns_by_year(df_normalized, columns_mapping)
        print(f"    Colonnes tries: {list(df_sorted.columns)}")
        
        # 4. Structurer en JSON
        print("\n[INFO] Etape 4: Structuration JSON...")
        
        # Vérifier si c'est un Journal
        if document_type == 'JOURNAL':
            structured_data = self.structure_journal_data(df_sorted, columns_mapping)
        else:
            structured_data = self.structure_to_json(df_sorted, columns_mapping, document_type, company_metadata)
        
        print(f"    Type: {structured_data.get('type_document')}")
        if 'lignes' in structured_data:
            print(f"    {len(structured_data['lignes'])} lignes structurées")
        if 'annees' in structured_data:
            print(f"    Annees: {structured_data.get('annees')}")
        
        print("\n[SUCCESS] TRAITEMENT TERMINE")
        print("=" * 80)
        
        return structured_data
    
    def structure_journal_data(
        self,
        df: pd.DataFrame,
        columns_mapping: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Structure les données d'un Journal comptable.
        
        IMPORTANT: Le type de journal doit être IDENTIQUE pour toutes les lignes
        d'une même pièce (même numero_piece).
        
        Returns:
            {
                "type_document": "JOURNAL",
                "lignes": [...]
            }
        """
        print("\n[INFO] STRUCTURATION JOURNAL")
        print("=" * 80)
        
        # Identifier les colonnes avec nettoyage
        columns_lower = {str(col).lower().strip(): col for col in df.columns}
        print(f"[DEBUG] Colonnes détectées: {list(columns_lower.keys())}")
        
        # Mapping des colonnes Journal
        date_col = None
        compte_col = None
        libelle_col = None
        debit_col = None
        credit_col = None
        piece_col = None
        type_journal_col = None
        
        for col_lower, col_original in columns_lower.items():
            if 'date' in col_lower:
                date_col = col_original
            # PRIORITÉ: Une colonne appelée exactement "compte" ou "n° compte" est préférée
            elif col_lower in ['compte', 'compte n°', 'n° compte', 'numero compte', 'numéro compte']:
                compte_col = col_original
            # Sinon, chercher une colonne contenant "compte" mais sans les mots exclus (avec gestion accents)
            elif ('compte' in col_lower or 'n°' in col_lower or 'numero' in col_lower) and not any(x in col_lower for x in ['libelle', 'libellé', 'intitule', 'intitulé', 'piece', 'pièce', 'piéce', 'description', 'date']):
                # On ne remplace pas une colonne exacte déjà trouvée
                if not compte_col or (col_lower in ['compte', 'compte n°']):
                     compte_col = col_original

            # PRIORITÉ: Libellé/Description avant Intitulé du compte (pour les journaux)
            elif ('libelle' in col_lower or 'libellé' in col_lower or 'description' in col_lower) and not libelle_col:
                libelle_col = col_original
            elif any(x in col_lower for x in ['intitule', 'intitulé']) and not libelle_col:
                libelle_col = col_original
            elif 'debit' in col_lower or 'débit' in col_lower:
                debit_col = col_original
            elif 'credit' in col_lower or 'crédit' in col_lower:
                credit_col = col_original
            elif ('piece' in col_lower or 'pièce' in col_lower or 'piéce' in col_lower or 'n_p' in col_lower or 'n°_p' in col_lower or 'num_p' in col_lower):
                piece_col = col_original
            elif 'type' in col_lower and 'journal' in col_lower:
                type_journal_col = col_original
        
        print(f"[INFO] Colonnes détectées:")
        print(f"   - Date: {date_col}")
        print(f"   - Compte: {compte_col}")
        print(f"   - Libellé: {libelle_col}")
        print(f"   - Débit: {debit_col}")
        print(f"   - Crédit: {credit_col}")
        print(f"   - N° Pièce: {piece_col}")
        print(f"   - Type Journal: {type_journal_col}")
        
        # La date est maintenant optionnelle (utilisera 31/12/2025 si absente)
        if not all([compte_col, debit_col, credit_col]):
            # Si compte_col est absent, on peut quand même continuer si libelle_col est là (on extraira le compte du libellé)
            if not libelle_col or not all([debit_col, credit_col]):
                raise ValueError("Colonnes obligatoires manquantes pour Journal (Compte/Intitulé, Débit, Crédit)")
        
        # ÉTAPE 1: Construire toutes les lignes avec leurs données brutes
        lignes_raw = []
        last_valid_date = None  # Pour propager les dates manquantes
        
        for idx, row in df.iterrows():
            # 1. Extraction du compte (colonne dédiée ou fallback libellé)
            numero_compte = ""
            if compte_col:
                numero_compte_raw = str(row.get(compte_col, '')).strip()
                if numero_compte_raw and numero_compte_raw != 'nan' and numero_compte_raw != '0':
                    try:
                        num_val = float(numero_compte_raw)
                        numero_compte = str(int(num_val)) if num_val == int(num_val) else numero_compte_raw
                    except (ValueError, TypeError):
                        numero_compte = numero_compte_raw
            
            # Fallback libellé
            libelle_raw = str(row.get(libelle_col, '')) if libelle_col else ''
            if not numero_compte or numero_compte == 'nan':
                import re
                match = re.match(r'^(\d{2,8})', libelle_raw.strip())
                if match:
                    numero_compte = match.group(1)
            
            if not numero_compte or numero_compte == 'nan':
                continue

            # 2. Débit / Crédit
            debit = float(row.get(debit_col, 0) or 0)
            credit = float(row.get(credit_col, 0) or 0)
            
            # 3. Libellé et Nettoyage
            libelle = libelle_raw.strip()
            if numero_compte and libelle.startswith(numero_compte):
                libelle = libelle[len(numero_compte):].strip().lstrip('-').lstrip(':').strip()
            
            # Filtre TOTAL et Lignes vides
            if 'TOTAL' in libelle.upper() or 'TOTAL' in numero_compte.upper():
                continue
            if debit == 0 and credit == 0:
                continue
            if libelle.lower().startswith(('opération', 'operation')):
                libelle = ''
            if not libelle:
                pcg_label = get_pcg_label(numero_compte)
                if pcg_label and pcg_label != "-":
                    libelle = pcg_label

            # 4. Numéro de Pièce
            numero_piece_raw = str(row.get(piece_col, '')) if piece_col else ''
            numero_piece = numero_piece_raw.strip() if numero_piece_raw and numero_piece_raw != 'nan' else ''
            
            # 5. Type Journal (si présent)
            type_journal_excel = str(row.get(type_journal_col, '')) if type_journal_col else ''
            type_journal_excel = type_journal_excel.strip().upper() if type_journal_excel and type_journal_excel != 'nan' else None

            # 6. Date (avec propagation et conversion en objet date)
            date_raw = row.get(date_col, '') if date_col else ''
            res_date = None
            if date_raw and str(date_raw) != 'nan':
                try:
                    # Détecter si c'est déjà au format ISO YYYY-MM-DD ou DD-MM-YYYY
                    date_val_str = str(date_raw)
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_val_str):
                        date_obj = pd.to_datetime(date_raw)
                    elif re.match(r'^\d{2}[-/]\d{2}[-/]\d{4}$', date_val_str):
                        date_obj = pd.to_datetime(date_raw, dayfirst=True)
                    else:
                        date_obj = pd.to_datetime(date_raw, errors='coerce', dayfirst=True)
                        
                    if pd.notna(date_obj):
                        res_date = date_obj.date()
                        last_valid_date = res_date
                    else:
                        res_date = None
                except:
                    res_date = None
            
            if res_date is None:
                res_date = last_valid_date if last_valid_date else date(2025, 12, 31)

            lignes_raw.append({
                "numero_compte": numero_compte,
                "libelle": libelle,
                "debit": debit,
                "credit": credit,
                "date": res_date,
                "numero_piece": numero_piece,
                "type_journal_excel": type_journal_excel
            })

        
        # ÉTAPE 2: Grouper par numéro de pièce et déterminer le type de journal PAR PIÈCE
        pieces = {}
        for ligne in lignes_raw:
            piece_key = ligne['numero_piece'] if ligne['numero_piece'] else f"_NOPIECE_{len(pieces)}"
            if piece_key not in pieces:
                pieces[piece_key] = []
            pieces[piece_key].append(ligne)
        
        print(f"\n[INFO] {len(pieces)} pièce(s) détectée(s)")
        
        # ÉTAPE 3: Déterminer le type de journal pour chaque pièce
        lignes_final = []
        
        for piece_key, piece_lignes in pieces.items():
            # Si le type est fourni dans Excel, l'utiliser
            type_from_excel = next((l['type_journal_excel'] for l in piece_lignes if l['type_journal_excel']), None)
            
            if type_from_excel:
                type_journal = type_from_excel
            else:
                # Sinon, détecter intelligemment en analysant TOUTES les lignes de la pièce
                type_journal = self._detect_journal_type_for_piece(piece_lignes)
            
            # Appliquer le même type à toutes les lignes de cette pièce
            for ligne in piece_lignes:
                lignes_final.append({
                    "numero_compte": ligne["numero_compte"],
                    "libelle": ligne["libelle"],
                    "debit": ligne["debit"],
                    "credit": ligne["credit"],
                    "date": ligne["date"],
                    "numero_piece": ligne["numero_piece"],
                    "type_journal": type_journal
                })
        
        # ÉTAPE 4: Regrouper par année (demande utilisateur: comme Bilan/CDR)
        donnees_par_annee = {}
        all_years = set()
        
        for ligne in lignes_final:
            try:
                # Extraire l'année de "YYYY-MM-DD"
                annee = ligne['date'].split('-')[0]
                if not annee.isdigit() or len(annee) != 4:
                    annee = "Inconnu"
            except:
                annee = "Inconnu"
                
            if annee not in donnees_par_annee:
                donnees_par_annee[annee] = []
            donnees_par_annee[annee].append(ligne)
            if annee != "Inconnu":
                all_years.add(annee)
        
        sorted_years = sorted(list(all_years), reverse=True)
        if "Inconnu" in donnees_par_annee:
            sorted_years.append("Inconnu")

        print(f"[INFO] {len(lignes_final)} lignes structurées sur {len(sorted_years)} année(s)")
        print("=" * 80)
        
        return {
            "type_document": "JOURNAL",
            "annees": sorted_years,
            "lignes": lignes_final, # Flat list for backward compatibility with frontend/views
            "donnees_par_annee": donnees_par_annee # Grouped data for the UI
        }
    
    def _detect_journal_type_for_piece(self, piece_lignes: List[Dict]) -> str:
        """
        Détecte le type de journal pour une PIÈCE COMPLÈTE (ensemble de lignes).
        
        Logique:
        - Analyser les comptes et libellés de TOUTES les lignes
        - Prioriser les indices les plus forts
        
        Returns:
            Type de journal: ACHAT, VENTE, BANQUE, CAISSE, OD, AN
        """
        # Collecter tous les comptes et libellés
        comptes = [l['numero_compte'] for l in piece_lignes]
        libelles = ' '.join([l['libelle'].lower() for l in piece_lignes])
        
        # PRIORITÉ 1: Détection par analyse des comptes (plus fiable)
        has_fournisseur = any(c.startswith('40') for c in comptes)
        has_client = any(c.startswith('41') for c in comptes)
        has_banque = any(c.startswith('512') for c in comptes)
        has_caisse = any(c.startswith('53') for c in comptes)
        has_capital = any(c.startswith('10') for c in comptes)  # Classe 1 = Capitaux propres
        has_immobilisation = any(c.startswith('2') for c in comptes)  # Classe 2 = Immobilisations
        
        # Cas spéciaux : Opérations avec la banque
        if has_banque:
            # Si banque + capital = apport de capital → OD
            if has_capital:
                return 'OD'
            # Si banque + immobilisation = achat d'immobilisation → OD
            elif has_immobilisation:
                return 'OD'
            # Si banque + fournisseur = paiement fournisseur → BANQUE
            elif has_fournisseur:
                return 'BANQUE'
            # Si banque + client = encaissement client → BANQUE
            elif has_client:
                return 'BANQUE'
        
        # Autres cas par compte
        if has_fournisseur:
            return 'ACHAT'
        elif has_client:
            return 'VENTE'
        elif has_caisse:
            return 'CAISSE'
        
        # PRIORITÉ 2: Détection par mots-clés dans les libellés (moins fiable)
        if any(keyword in libelles for keyword in ['achat', 'fournisseur', 'achats', 'fourniture']):
            return 'ACHAT'
        elif any(keyword in libelles for keyword in ['vente', 'client', 'ventes', 'facture']):
            return 'VENTE'
        elif any(keyword in libelles for keyword in ['salaire', 'paie', 'paye', 'personnel']):
            return 'PAIE'
        elif any(keyword in libelles for keyword in ['à nouveau', 'a nouveau', 'report', 'ouverture', 'capital', 'apport']):
            return 'OD'
        
        # Par défaut: Opérations Diverses
        return 'OD'
    
    def _detect_journal_type(self, libelle: str, numero_compte: str) -> str:
        """
        Détecte automatiquement le type de journal à partir du libellé et du numéro de compte.
        
        Returns:
            Type de journal: ACHAT, VENTE, BANQUE, CAISSE, OD, AN
        """
        libelle_lower = str(libelle).lower()
        
        # Détection par mots-clés dans le libellé
        if any(keyword in libelle_lower for keyword in ['achat', 'fournisseur', 'achats', 'fourniture']):
            return 'ACHAT'
        elif any(keyword in libelle_lower for keyword in ['vente', 'client', 'ventes', 'facture']):
            return 'VENTE'
        elif any(keyword in libelle_lower for keyword in ['banque', 'bank', 'virement', 'cheque', 'chèque']):
            return 'BANQUE'
        elif any(keyword in libelle_lower for keyword in ['caisse', 'espece', 'espèce', 'cash']):
            return 'CAISSE'
        elif any(keyword in libelle_lower for keyword in ['salaire', 'paie', 'paye', 'personnel']):
            return 'PAIE'
        elif any(keyword in libelle_lower for keyword in ['à nouveau', 'a nouveau', 'report', 'ouverture']):
            return 'AN'
        
        # Détection par numéro de compte
        if numero_compte:
            first_digit = numero_compte[0] if len(numero_compte) > 0 else ''
            if first_digit == '4' and len(numero_compte) >= 2:
                second_digit = numero_compte[1]
                if second_digit == '0':  # 40x = Fournisseurs
                    return 'ACHAT'
                elif second_digit == '1':  # 41x = Clients
                    return 'VENTE'
            elif first_digit == '5':  # Comptes financiers
                if numero_compte.startswith('512'):  # Banque
                    return 'BANQUE'
                elif numero_compte.startswith('53'):  # Caisse
                    return 'CAISSE'
        
        # Par défaut: Opérations Diverses
        return 'OD'


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def clean_numeric_value(value: Any) -> float:
    """
    Nettoie une valeur numérique.
    
    Args:
        value: Valeur à nettoyer
        
    Returns:
        float: Valeur nettoyée
    """
    if pd.isna(value):
        return 0.0
    
    if isinstance(value, (int, float)):
        return float(value)
    
    # Essayer de convertir une chaîne
    try:
        # Supprimer les espaces et remplacer les virgules par des points
        clean_str = str(value).strip().replace(' ', '').replace(',', '.')
        return float(clean_str)
    except (ValueError, TypeError):
        return 0.0


def detect_account_class(numero_compte: str) -> Optional[int]:
    """
    Détermine la classe comptable d'un poste.
    
    Args:
        numero_compte: Numéro de compte
        
    Returns:
        int: Classe (1-7) ou None si invalide
    """
    if not numero_compte:
        return None
    
    clean_account = re.sub(r'[^0-9]', '', str(numero_compte))
    
    if not clean_account:
        return None
    
    first_digit = clean_account[0]
    
    if first_digit in '1234567':
        return int(first_digit)
    
    return None
