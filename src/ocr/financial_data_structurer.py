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
        pass
    
    def detect_document_type(self, df: pd.DataFrame, columns_mapping: Dict[str, str]) -> Tuple[str, float]:
        """
        Détecte si le document est un BILAN ou un COMPTE_RESULTAT.
        
        Args:
            df: DataFrame contenant les données
            columns_mapping: Mapping des colonnes
            
        Returns:
            tuple: (type_document, confidence)
                - type_document: 'BILAN' ou 'COMPTE_RESULTAT'
                - confidence: Score de confiance (0.0 à 1.0)
        """
        account_col = columns_mapping.get('compte')
        
        if not account_col or account_col not in df.columns:
            return 'UNKNOWN', 0.0
        
        # Analyser les numéros de compte
        accounts = df[account_col].dropna().astype(str)
        
        bilan_count = 0  # Comptes classe 1-5
        cr_count = 0     # Comptes classe 6-7
        
        for account in accounts:
            # Extraire le premier chiffre
            clean_account = re.sub(r'[^0-9]', '', str(account))
            if clean_account:
                first_digit = clean_account[0]
                
                if first_digit in '12345':
                    bilan_count += 1
                elif first_digit in '67':
                    cr_count += 1
        
        total = bilan_count + cr_count
        
        if total == 0:
            return 'UNKNOWN', 0.0
        
        bilan_ratio = bilan_count / total
        cr_ratio = cr_count / total
        
        # Déterminer le type avec un seuil de confiance
        if bilan_ratio > 0.7:
            return 'BILAN', bilan_ratio
        elif cr_ratio > 0.7:
            return 'COMPTE_RESULTAT', cr_ratio
        else:
            # Si incertain, choisir le plus probable
            if bilan_ratio > cr_ratio:
                return 'BILAN', bilan_ratio
            else:
                return 'COMPTE_RESULTAT', cr_ratio
    
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
    
    def extract_year_columns(self, df: pd.DataFrame) -> List[int]:
        """
        Identifie les colonnes contenant des années.
        
        Args:
            df: DataFrame
            
        Returns:
            Liste des années détectées (triées)
        """
        years = []
        
        for col in df.columns:
            # Si c'est déjà un int et que c'est une année
            if isinstance(col, int) and 1900 <= col <= 2100:
                years.append(col)
                continue
            
            col_str = str(col)
            
            # Vérifier si c'est une année (format 20XX ou 19XX)
            if re.match(r'^(19|20)\d{2}$', col_str):
                years.append(int(col_str))
                continue
            
            # Vérifier si c'est un nombre qui ressemble à une année
            try:
                num_val = float(col_str)
                year_val = int(num_val)
                if 1900 <= year_val <= 2100 and num_val == year_val:
                    years.append(year_val)
            except (ValueError, TypeError):
                pass
        
        return sorted(list(set(years)))  # Supprimer les doublons et trier
    
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
        
        # Identifier les colonnes
        compte_col = columns_mapping.get('compte')
        libelle_col = columns_mapping.get('libelle')
        
        if not compte_col or compte_col not in df.columns:
            raise ValueError("Colonne 'compte' non trouvée dans le DataFrame")
        
        # Construire les lignes
        lignes = []
        
        for idx, row in df.iterrows():
            numero_compte = str(row.get(compte_col, '')).strip()
            
            # Ignorer les lignes vides
            if not numero_compte or numero_compte == 'nan':
                continue
            
            # Classifier le compte
            classification = self.classify_account(numero_compte)
            
            # Extraire le libellé
            if libelle_col and libelle_col in df.columns:
                poste = str(row.get(libelle_col, '')).strip()
            else:
                poste = classification.get('classe_libelle', '')
            
            # Extraire les valeurs pour chaque année
            valeurs = {}
            for year in years:
                if year in df.columns:
                    value = row.get(year)
                    
                    # Nettoyer la valeur
                    if pd.isna(value):
                        valeurs[str(year)] = 0
                    else:
                        try:
                            # Convertir en nombre
                            num_value = float(value)
                            # Si c'est un entier, garder comme int
                            if num_value == int(num_value):
                                valeurs[str(year)] = int(num_value)
                            else:
                                valeurs[str(year)] = num_value
                        except (ValueError, TypeError):
                            valeurs[str(year)] = 0
            
            # Ajouter la ligne seulement si elle a des valeurs non nulles
            if any(v != 0 for v in valeurs.values()):
                ligne = {
                    "poste": poste,
                    "numero_compte": numero_compte,
                    "classe": classification.get('classe'),
                    "classe_libelle": classification.get('classe_libelle'),
                    "valeurs": valeurs
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
        company_metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Traitement complet d'un DataFrame : détection, nettoyage, tri, structuration.
        
        Args:
            df: DataFrame source
            columns_mapping: Mapping des colonnes
            
        Returns:
            dict: Structure JSON complète
        """
        print("\n DBUT DU TRAITEMENT FINANCIER")
        print("=" * 80)
        
        # 1. Détecter le type de document
        print("[INFO] Etape 1: Detection du type de document...")
        document_type, confidence = self.detect_document_type(df, columns_mapping)
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
        print("\n tape 4: Structuration JSON...")
        structured_data = self.structure_to_json(df_sorted, columns_mapping, document_type, company_metadata)
        print(f"    {len(structured_data['lignes'])} lignes structures")
        print(f"    Annes: {structured_data['annees']}")
        
        print("\n[SUCCESS] TRAITEMENT TERMINE")
        print("=" * 80)
        
        return structured_data


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
