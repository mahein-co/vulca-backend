"""
Module de nettoyage et structuration des données Excel/CSV extraites par OCR.
Transforme les données brutes en tableaux structurés et exploitables.
"""

import pandas as pd
import numpy as np
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Import du module PCG pour l'enrichissement automatique
from ocr.pcg_loader import get_account_suggestions


class DataCleaner:
    """
    Classe pour nettoyer et structurer les données extraites par OCR.
    """
    
    def __init__(self):
        """Initialise le nettoyeur de données."""
        self.cleaning_stats = {
            'unnamed_columns_removed': 0,
            'invalid_values_cleaned': 0,
            'types_corrected': 0,
            'columns_renamed': 0,
            'empty_rows_removed': 0,
            'empty_columns_removed': 0
        }
    
    def remove_unnamed_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Supprime toutes les colonnes 'Unnamed: X' ou sans nom significatif.
        
        Args:
            df: DataFrame à nettoyer
            
        Returns:
            DataFrame sans colonnes unnamed
        """
        if df.empty:
            return df
        
        # Identifier les colonnes à supprimer
        columns_to_drop = []
        for col in df.columns:
            col_str = str(col).strip()
            # Supprimer si commence par "Unnamed" ou est vide
            if col_str.startswith('Unnamed') or col_str == '' or col_str == 'nan':
                # EXCEPTION: Si la colonne contient du texte (non numérique), on la garde potentiellement
                # car c'est souvent la colonne de libellés qui n'avait pas d'en-tête
                non_numeric_count = 0
                total_count = 0
                for val in df[col].dropna():
                    total_count += 1
                    if isinstance(val, str) and not re.match(r'^-?\d+[.,]?\d*$', val.strip()):
                        non_numeric_count += 1
                
                # Si plus de 30% de texte, on la garde
                if total_count > 0 and (non_numeric_count / total_count) > 0.3:
                    continue
                    
                columns_to_drop.append(col)
        
        if columns_to_drop:
            df = df.drop(columns=columns_to_drop)
            self.cleaning_stats['unnamed_columns_removed'] += len(columns_to_drop)
            print(f"   [INFO] {len(columns_to_drop)} colonne(s) 'Unnamed' reellement vides supprimee(s)")
        
        return df
    
    def clean_invalid_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Nettoie les valeurs invalides (0 non pertinents, NaN, cellules vides).
        
        Args:
            df: DataFrame à nettoyer
            
        Returns:
            DataFrame avec valeurs nettoyées
        """
        if df.empty:
            return df
        
        initial_nulls = df.isna().sum().sum()
        
        # Remplacer les strings vides par NaN
        df = df.replace('', np.nan)
        df = df.replace('null', np.nan)
        df = df.replace('NULL', np.nan)
        
        # Pour chaque colonne numérique, remplacer les 0 isolés par NaN
        # (sauf si c'est une colonne d'années ou de codes)
        for col in df.columns:
            col_str = str(col).lower()
            # Ne pas toucher aux colonnes d'années, codes, ou identifiants
            if any(keyword in col_str for keyword in ['année', 'year', 'code', 'id', 'numero']):
                continue
            
            # Si la colonne contient principalement des nombres
            if pd.api.types.is_numeric_dtype(df[col]):
                # Remplacer les 0.00 par NaN seulement si c'est une colonne de montants
                if df[col].abs().max() > 100:  # Heuristique: montants > 100
                    df[col] = df[col].replace(0, np.nan)
        
        final_nulls = df.isna().sum().sum()
        self.cleaning_stats['invalid_values_cleaned'] += (final_nulls - initial_nulls)
        
        return df
    
    def fix_data_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Corrige les types de données (années, montants, dates, textes).
        
        Args:
            df: DataFrame à corriger
            
        Returns:
            DataFrame avec types corrects
        """
        if df.empty:
            return df
        
        for col in df.columns:
            col_str = str(col).lower()
            
            # Détecter les colonnes d'années (2020.00 → 2020)
            if any(keyword in col_str for keyword in ['année', 'year', 'exercice']):
                try:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    df[col] = df[col].fillna(0).astype(int)
                    # Remplacer les 0 par NaN
                    df[col] = df[col].replace(0, np.nan)
                    self.cleaning_stats['types_corrected'] += 1
                except Exception:
                    pass
            
            # Détecter les colonnes de dates
            elif any(keyword in col_str for keyword in ['date', 'période', 'period']):
                try:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
                    self.cleaning_stats['types_corrected'] += 1
                except Exception:
                    pass
            
            # Détecter les colonnes de montants
            elif any(keyword in col_str for keyword in ['montant', 'amount', 'valeur', 'value', 'total']):
                try:
                    col_data = df[col]
                    # Nettoyer les séparateurs de milliers et convertir
                    if hasattr(col_data, 'dtype') and col_data.dtype == 'object':
                        df[col] = col_data.astype(str).str.replace(' ', '').str.replace(',', '.')
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    self.cleaning_stats['types_corrected'] += 1
                except Exception:
                    pass
            
            # Pour les autres colonnes, essayer de détecter automatiquement
            else:
                try:
                    # Si la colonne contient des nombres sous forme de strings
                    col_data = df[col]
                    if hasattr(col_data, 'dtype') and col_data.dtype == 'object':
                        # Tenter conversion numérique
                        numeric_col = pd.to_numeric(col_data, errors='coerce')
                        # Si plus de 70% des valeurs sont numériques, convertir
                        if numeric_col.notna().sum() / len(df) > 0.7:
                            df[col] = numeric_col
                            self.cleaning_stats['types_corrected'] += 1
                except Exception:
                    pass
        
        return df
    
    def standardize_formats(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Uniformise les formats (dates, montants, libellés).
        
        Args:
            df: DataFrame à uniformiser
            
        Returns:
            DataFrame avec formats standardisés
        """
        if df.empty:
            return df
        
        for col in df.columns:
            # Standardiser les dates au format ISO
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].dt.strftime('%Y-%m-%d')
            
            # Standardiser les montants (2 décimales)
            elif pd.api.types.is_numeric_dtype(df[col]):
                col_str = str(col).lower()
                if any(keyword in col_str for keyword in ['montant', 'amount', 'valeur', 'value']):
                    df[col] = df[col].round(2)
            
            # Nettoyer les libellés textuels
            else:
                try:
                    col_data = df[col]
                    if hasattr(col_data, 'dtype') and col_data.dtype == 'object':
                        # Supprimer les espaces multiples
                        df[col] = col_data.astype(str).str.replace(r'\s+', ' ', regex=True)
                        # Supprimer les espaces en début/fin
                        df[col] = df[col].str.strip()
                        # Capitaliser la première lettre
                        df[col] = df[col].str.capitalize()
                except Exception:
                    pass
        
        return df
    
    def rename_columns_intelligently(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Renomme les colonnes de manière claire et cohérente.
        
        Args:
            df: DataFrame à renommer
            
        Returns:
            DataFrame avec colonnes renommées
        """
        if df.empty:
            return df
        
        new_columns = {}
        label_col_found = False
        compte_col_found = False
        
        # 1. Identifier d'abord les colonnes existantes bien nommées
        for col in df.columns:
            col_str = str(col).lower()
            # Prioriser les colonnes qui contiennent "compte" mais PAS "libelle" ou "intitule"
            if any(kw in col_str for kw in ['compte', 'numéro de compte', 'code_compte', 'account']) and \
               not any(kw in col_str for kw in ['libelle', 'libellé', 'intitule', 'intitulé']) and not compte_col_found:
                new_columns[col] = 'COMPTE'
                compte_col_found = True
            elif any(kw in col_str for kw in ['libelle', 'libellé', 'description', 'poste', 'rubrique', 'intitule', 'intitulé']) and not label_col_found:
                new_columns[col] = 'DESCRIPTION'
                label_col_found = True
        
        # 2. Renommer les années et autres colonnes
        for col in df.columns:
            if col in new_columns: continue
            
            col_str = str(col).strip()
            
            # Si la colonne contient des années (2021.00, 2022.00, etc.)
            if re.match(r'^\d{4}\.?\d*$', col_str):
                year = int(float(col_str))
                new_columns[col] = str(year)
                self.cleaning_stats['columns_renamed'] += 1
            
            # Si c'est une colonne "Unnamed" qu'on a gardée
            elif 'Unnamed' in col_str:
                # Si toutes les valeurs sont des nombres courts (3-6 chiffres), c'est probablement COMPTE
                is_likely_compte = False
                non_na_vals = df[col].dropna()
                if not non_na_vals.empty:
                    short_numeric_count = 0
                    for val in non_na_vals:
                        val_s = str(val).strip()
                        if re.match(r'^\d{3,6}$', val_s):
                            short_numeric_count += 1
                    if short_numeric_count / len(non_na_vals) > 0.6:
                        is_likely_compte = True

                if is_likely_compte and not compte_col_found:
                    new_columns[col] = 'COMPTE'
                    compte_col_found = True
                    self.cleaning_stats['columns_renamed'] += 1
                elif not label_col_found:
                    new_columns[col] = 'DESCRIPTION'
                    label_col_found = True
                    self.cleaning_stats['columns_renamed'] += 1
            
            # Si la colonne est déjà bien nommée, la garder
            elif len(col_str) > 1 and not col_str.startswith('Unnamed'):
                # Nettoyer le nom (enlever caractères spéciaux)
                clean_name = re.sub(r'[^\w\s-]', '', col_str)
                clean_name = clean_name.strip().replace(' ', '_')
                if clean_name != col_str:
                    new_columns[col] = clean_name
                    self.cleaning_stats['columns_renamed'] += 1
        
        # 3. Si toujours pas de DESCRIPTION, prendre la première colonne restante
        if not label_col_found and not df.empty:
            for col in df.columns:
                if col not in new_columns:
                    new_columns[col] = 'DESCRIPTION'
                    label_col_found = True
                    break

        if new_columns:
            df = df.rename(columns=new_columns)
            print(f"   [INFO] {len(new_columns)} colonne(s) renommee(s) ou uniformisee(s)")
        
        return df
    
        return df
    
    def remove_null_and_zero_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Supprime les lignes contenant uniquement des valeurs null ou 0.
        
        Args:
            df: DataFrame à nettoyer
            
        Returns:
            DataFrame sans lignes null/0
        """
        if df.empty:
            return df
        
        initial_rows = len(df)
        
        # Identifier les colonnes numériques (excluant les colonnes de texte/libellés)
        numeric_cols = []
        for col in df.columns:
            col_str = str(col).lower()
            # Exclure les colonnes de libellés/descriptions
            if any(keyword in col_str for keyword in ['libelle', 'libellé', 'description', 'nom', 'name']):
                continue
            # Inclure seulement les colonnes numériques
            if pd.api.types.is_numeric_dtype(df[col]):
                numeric_cols.append(col)
        
        if not numeric_cols:
            # Si pas de colonnes numériques, ne rien faire
            return df
        
        # Créer un masque pour identifier les lignes à garder
        # Une ligne est gardée si elle contient au moins une valeur non-null et non-zéro
        mask = pd.Series([False] * len(df), index=df.index)
        
        for col in numeric_cols:
            # Marquer True si la valeur n'est ni null ni 0
            # On vérifie aussi les versions string des zéros si les types ne sont pas encore parfaits
            val_is_significant = (df[col].notna() & (df[col] != 0))
            
            # Gérer les colonnes object qui pourraient contenir "0" ou "0.00"
            col_data = df[col]
            if hasattr(col_data, 'dtype') and col_data.dtype == 'object':
                val_as_str = col_data.astype(str).str.strip().str.replace(',', '.')
                val_is_zero_str = val_as_str.isin(['0', '0.0', '0.00', 'nan', 'null', 'None', ''])
                val_is_significant &= (~val_is_zero_str)

            mask |= val_is_significant
        
        # Filtrer le DataFrame
        df = df[mask].reset_index(drop=True)
        
        rows_removed = initial_rows - len(df)
        
        if rows_removed > 0:
            self.cleaning_stats['empty_rows_removed'] += rows_removed
            print(f"   [INFO] {rows_removed} ligne(s) avec valeurs null/0 supprimee(s)")
        
        return df
    
    def remove_empty_rows_and_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Supprime les lignes et colonnes complètement vides.
        
        Args:
            df: DataFrame à nettoyer
            
        Returns:
            DataFrame sans lignes/colonnes vides
        """
        if df.empty:
            return df
        
        # Compter avant
        initial_rows = len(df)
        initial_cols = len(df.columns)
        
        # Supprimer les colonnes entièrement vides
        df = df.dropna(axis=1, how='all')
        
        # Supprimer les lignes entièrement vides
        df = df.dropna(axis=0, how='all')
        
        # Réinitialiser l'index
        df = df.reset_index(drop=True)
        
        # Compter après
        final_rows = len(df)
        final_cols = len(df.columns)
        
        rows_removed = initial_rows - final_rows
        cols_removed = initial_cols - final_cols
        
        if rows_removed > 0:
            print(f"   [INFO] {rows_removed} ligne(s) completement vides supprimee(s)")
        
        if cols_removed > 0:
            self.cleaning_stats['empty_columns_removed'] += cols_removed
            print(f"   [INFO] {cols_removed} colonne(s) vide(s) supprimee(s)")
        
        return df
    
    def detect_and_mark_totals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Identifie et marque les lignes de totaux/sous-totaux.
        
        Args:
            df: DataFrame à analyser
            
        Returns:
            DataFrame avec colonne 'is_total' ajoutée
        """
        if df.empty:
            return df
        
        # Créer une colonne pour marquer les totaux
        df['is_total'] = False
        
        # Chercher les lignes contenant des mots-clés de totaux
        total_keywords = [
            'total', 'sous-total', 'subtotal', 'somme', 'sum',
            'solde', 'balance', 'cumul', 'total :',
            'tresorerie', 'trésorerie', 'créances et assimilés', 'creances et assimiles', 'créances et assimiles',
            "chiffre d'affaires", "chiffre d affaires", "chiffres d'affaires",
            'valeur ajoutee', 'valeur ajoutée', 'excedent brut', 'excédent brut',
            'production de l\'exercice', 'consommation de l\'exercice',
        ]
        
        import re as _re
        # Pattern pour les lignes numérotées de type "1 - label" ou "4 - label" (sous-totaux CDR)
        # IMPORTANT: Restreindre à 1-2 chiffres SEULEMENT pour éviter de filtrer "601 - Achats"
        _numbered_total_pattern = _re.compile(r'^\s*\d{1,2}\s*[-\u2013]\s*\w+', _re.IGNORECASE)
        
        for idx, row in df.iterrows():
            # Convertir la ligne en string pour chercher les mots-clés
            row_str = ' '.join(str(val).lower() for val in row.values)
            
            # Détection intelligente des totaux pour éviter les faux positifs (ex: "somme" dans "consommés")
            is_total_match = False
            for kw in total_keywords:
                if len(kw) <= 2: # Ignorer les mots trop courts
                    continue
                
                # Pour les mots uniques sans caractères spéciaux, on exige une frontière de mot (\b)
                if ' ' not in kw and "'" not in kw and ":" not in kw and "-" not in kw:
                    if _re.search(fr'\b{_re.escape(kw.lower())}\b', row_str, _re.IGNORECASE):
                        is_total_match = True
                        break
                else:
                    # Pour les phrases ou mots avec ponctuation, on accepte le substring simple
                    if kw.lower() in row_str:
                        is_total_match = True
                        break
            
            if is_total_match:
                df.at[idx, 'is_total'] = True
                continue
            
            # Détecter les lignes numérotées du type "1 - production de l'exercice"
            # Seulement les colonnes description/libelle (pas les colonnes numéro de compte)
            for val in row.values:
                val_str = str(val).strip()
                # Uniquement si la valeur est textuelle (pas juste un nombre)
                if _numbered_total_pattern.match(val_str) and not val_str.replace(' ', '').isdigit():
                    df.at[idx, 'is_total'] = True
                    break
        
        return df

    
    def enrich_with_pcg_accounts(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Enrichit le DataFrame avec les numéros de compte PCG suggérés.
        Si une colonne DESCRIPTION existe mais pas de colonne COMPTE (ou si elle est vide),
        on utilise le PCG pour trouver le meilleur compte correspondant.
        
        Args:
            df: DataFrame à enrichir
            
        Returns:
            DataFrame avec colonne COMPTE enrichie
        """
        if df.empty:
            return df
        
        # Vérifier si on a une colonne DESCRIPTION
        desc_col = None
        for col in df.columns:
            if str(col).upper() in ['DESCRIPTION', 'LIBELLE', 'LIBELLÉ', 'POSTE', 'RUBRIQUE']:
                desc_col = col
                break
        
        if desc_col is None:
            print("   [WARNING] Pas de colonne DESCRIPTION trouvee, enrichissement PCG ignore")
            return df
        
        # Vérifier si on a déjà une colonne COMPTE
        compte_col = None
        for col in df.columns:
            if str(col).upper() == 'COMPTE':
                compte_col = col
                break
        
        # Si pas de colonne COMPTE, la créer
        if compte_col is None:
            df['COMPTE'] = None
            compte_col = 'COMPTE'
            print("    Colonne COMPTE cre pour l'enrichissement PCG")
        
        # Enrichir chaque ligne
        enriched_count = 0
        print(f"    [DEBUG] Enrichissement PCG sur {len(df)} lignes... (colonne: {compte_col})")
        for idx, row in df.iterrows():
            # Si le compte est déjà renseigné et valide, on le garde
            existing_compte_data = row.get(compte_col)
            
            # Gérer le cas où existing_compte_data est une Series (doublons de colonnes)
            if hasattr(existing_compte_data, 'any'):
                # C'est une Series, prendre le premier élément
                existing_compte = existing_compte_data.iloc[0] if not existing_compte_data.empty else None
            else:
                existing_compte = existing_compte_data
                
            # Vérification robuste (évite ValueError si existing_compte est toujours étrange)
            try:
                if pd.notna(existing_compte) and str(existing_compte).strip() not in ['', 'nan', 'None']:
                    continue
            except Exception:
                # Si l'erreur d'ambiguïté persiste, on essaie de forcer en string
                if str(existing_compte).strip() not in ['', 'nan', 'None']:
                    continue
            
            # Sinon, chercher dans le PCG
            description_data = row.get(desc_col, '')
            if hasattr(description_data, 'any'):
                description = str(description_data.iloc[0]).strip() if not description_data.empty else ''
            else:
                description = str(description_data).strip()
                
            if not description or description == 'nan' or description == 'None':
                continue
            
            # Obtenir les suggestions du PCG
            suggestions = get_account_suggestions(description, top_n=1)
            
            if suggestions and len(suggestions) > 0:
                best_match = suggestions[0]
                # Ne prendre que si le score est suffisant (au moins 1 mot-clé matché)
                if best_match.get('score', 0) >= 1:
                    df.at[idx, compte_col] = best_match['numero_compte']
                    enriched_count += 1
        
        if enriched_count > 0:
            print(f"    {enriched_count} numro(s) de compte suggr(s) par le PCG")
        
        return df
    
    def remove_metadata_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Supprime les lignes contenant des métadonnées non-financières.
        Exemples: noms de feuilles, NIF, noms d'entreprise, en-têtes de sections.
        """
        if df.empty:
            return df
        
        initial_rows = len(df)
        metadata_keywords = [
            'actif', 'passif', 'bilan au', 'tableau de flux', 'flux de trésorerie',
            'etat des capitaux', 'capitaux propres', 'tableau des amortissements',
            'etats financiers', 'états financiers', 'compte de résultat', 'cdr nat',
            'nif:', 'nif :', 'stat:', 'stat :', 'adresse :',
        ]
        
        rows_to_drop = []
        for idx, row in df.iterrows():
            row_text = ' '.join(str(val).lower() for val in row.values if pd.notna(val))
            
            # Compter les valeurs numériques dans la ligne
            numeric_count = sum(1 for val in row.values if pd.notna(val) and str(val).replace(',', '.').replace('.', '').replace('-', '').isdigit())
            
            # Si la ligne contient des montants, ce n'est PAS une métadonnée
            if numeric_count >= 2:  # Au moins 2 valeurs numériques (ex: compte + montant)
                continue
            
            # Vérifier les mots-clés de métadonnées
            is_metadata = any(keyword in row_text for keyword in metadata_keywords)
            
            # Vérifier si ligne avec uniquement du texte (pas de montants)
            if not is_metadata:
                text_count = sum(1 for val in row.values if pd.notna(val) and str(val).strip() and not str(val).replace(',', '.').replace('.', '').replace('-', '').isdigit())
                
                if text_count > 0 and numeric_count == 0 and text_count <= 2:
                    if any(word in row_text for word in ['boulangerie', 'restaurant', 'commerce', 'sarl', 'eurl', 's.a.']):
                        is_metadata = True
            
            if is_metadata:
                rows_to_drop.append(idx)
        
        if rows_to_drop:
            df = df.drop(index=rows_to_drop).reset_index(drop=True)
            print(f"   [INFO] {len(rows_to_drop)} ligne(s) de metadonnees supprimee(s)")
        
        return df
    
    def clean_dataframe(
        self, 
        df: pd.DataFrame, 
        context: str = 'financial',
        remove_totals: bool = False,
        sheet_name: str = ""
    ) -> pd.DataFrame:
        """
        Fonction principale orchestrant tout le nettoyage.
        
        Args:
            df: DataFrame à nettoyer
            context: Contexte des données ('financial', 'general')
            remove_totals: Si True, supprime les lignes de totaux
            
        Returns:
            DataFrame nettoyé et structuré
        """
        if df.empty:
            print("   [WARNING] DataFrame vide, aucun nettoyage necessaire")
            return df
        
        print(f"\n DBUT NETTOYAGE DES DONNES (contexte: {context})")
        print(f"   [INFO] Dimensions initiales: {df.shape[0]} lignes x {df.shape[1]} colonnes")
        
        # Réinitialiser les stats
        self.cleaning_stats = {k: 0 for k in self.cleaning_stats}
        
        # Étape 1: Supprimer les colonnes unnamed
        df = self.remove_unnamed_columns(df)
        
        # Étape 2: Supprimer les lignes/colonnes vides
        df = self.remove_empty_rows_and_columns(df)
        
        # Étape 3: Renommer les colonnes intelligemment
        df = self.rename_columns_intelligently(df)
        
        # Étape 4: Corriger les types de données
        df = self.fix_data_types(df)
        
        # Étape 5: Nettoyer les valeurs invalides
        df = self.clean_invalid_values(df)
        
        # Étape 6: Supprimer les lignes avec uniquement null/0
        # Toujours supprimer si toutes les valeurs numériques sont à 0 (demande utilisateur)
        df = self.remove_null_and_zero_rows(df)
        
        # Étape 7: Uniformiser les formats
        df = self.standardize_formats(df)
        
        # Étape 8: Supprimer les lignes de métadonnées (si contexte financier)
        if context == 'financial':
            df = self.remove_metadata_rows(df)
        
        # Étape 9: Enrichir avec les numéros de compte PCG (si contexte financier)
        if context == 'financial':
            print(f"    Enrichissement avec le PCG...")
            df = self.enrich_with_pcg_accounts(df)
            
        # Étape 10: Supprimer les doublons dans les lignes
        initial_len = len(df)
        df = df.drop_duplicates()
        if len(df) < initial_len:
            print(f"   [INFO] {initial_len - len(df)} ligne(s) en double supprimee(s)")
        
        # Étape 11: Détecter les totaux (optionnel)
        if context == 'financial':
            df = self.detect_and_mark_totals(df)
            
            # Supprimer les totaux si demandé
            if remove_totals and 'is_total' in df.columns:
                totals_count = df['is_total'].sum()
                df = df[df['is_total'] == False]
                if totals_count > 0:
                    print(f"   [INFO] {totals_count} ligne(s) de totaux supprimee(s)")
            
            # Toujours supprimer la colonne is_total du résultat final
            if 'is_total' in df.columns:
                df = df.drop(columns=['is_total'])
        
        print(f"\n[SUCCESS] NETTOYAGE TERMINE")
        print(f"   [INFO] Dimensions finales: {df.shape[0]} lignes x {df.shape[1]} colonnes")
        print(f"    Statistiques:")
        for key, value in self.cleaning_stats.items():
            if value > 0:
                print(f"      - {key}: {value}")
        
        return df
    
    def get_cleaning_report(self) -> Dict:
        """
        Retourne un rapport détaillé du nettoyage effectué.
        
        Returns:
            Dictionnaire avec les statistiques de nettoyage
        """
        return self.cleaning_stats.copy()


# Fonction utilitaire pour usage simple
def clean_dataframe(
    df: pd.DataFrame, 
    context: str = 'financial',
    remove_totals: bool = False,
    sheet_name: str = ""
) -> pd.DataFrame:
    """
    Fonction utilitaire pour nettoyer un DataFrame.
    
    Args:
        df: DataFrame à nettoyer
        context: Contexte des données ('financial', 'general')
        remove_totals: Si True, supprime les lignes de totaux
        sheet_name: Nom de la feuille pour ajuster le nettoyage
        
    Returns:
        DataFrame nettoyé
        
    Example:
        >>> from ocr.data_cleaner import clean_dataframe
        >>> df_clean = clean_dataframe(df_raw, context='financial', sheet_name='BILAN')
    """
    cleaner = DataCleaner()
    return cleaner.clean_dataframe(df, context=context, remove_totals=remove_totals, sheet_name=sheet_name)
