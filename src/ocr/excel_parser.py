"""
Module de parsing et d'analyse de fichiers Excel pour l'importation de données financières.
Utilise OpenAI Vision pour une reconnaissance intelligente des structures de tableaux.
"""

import pandas as pd
import openpyxl
from io import BytesIO
import re
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import base64
from PIL import Image
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

# Import du module de structuration financière
from ocr.financial_data_structurer import FinancialDataStructurer

# Import du module de nettoyage de données
from ocr.data_cleaner import clean_dataframe

# Import des constantes
from ocr.constants import EXCLUDED_SHEET_NAMES


class ExcelParser:
    """Parser Excel avec détection intelligente via OpenAI Vision"""
    
    def __init__(self, openai_client, model="gpt-4o"):
        self.client = openai_client
        self.model = model
        self.structurer = FinancialDataStructurer()  # Initialiser le structureur
        
    def parse_excel_file(self, file) -> Dict:
        """
        Parse un fichier Excel et retourne la structure complète avec toutes les feuilles.
        
        Args:
            file: Fichier Excel (FileStorage ou bytes)
            
        Returns:
            dict: {
                'file_name': str,
                'sheets': [
                    {
                        'sheet_name': str,
                        'detected_type': 'BILAN' | 'COMPTE_RESULTAT' | 'UNKNOWN',
                        'confidence': float,
                        'columns_mapping': dict,
                        'data_preview': list,
                        'data': DataFrame,
                        'unmapped_rows': list,
                        'total_rows': int
                    }
                ],
                'total_rows': int
            }
        """
        # Lire le fichier Excel
        if hasattr(file, 'read'):
            file_content = file.read()
            file.seek(0)
        else:
            file_content = file
            
        excel_file = BytesIO(file_content)
        
        # Charger toutes les feuilles
        xl_file = pd.ExcelFile(excel_file)
        sheet_names = xl_file.sheet_names
        
        result = {
            'file_name': getattr(file, 'name', 'fichier_excel.xlsx'),
            'sheets': [],
            'total_rows': 0
        }
        
        
        for sheet_name in sheet_names:
            # Sauter les feuilles exclues
            if sheet_name in EXCLUDED_SHEET_NAMES or sheet_name.upper() in EXCLUDED_SHEET_NAMES:
                print(f"    Feuille '{sheet_name}' ignore (exclue)")
                continue

            try:
                print(f"\n[INFO] Analyse de la feuille: {sheet_name}")
                
                # Lire la feuille
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                print(f"    Lecture Excel russie - Shape: {df.shape}")
                
                # Nettoyer les noms de colonnes (convertir les années de float à int)
                df = self._clean_column_names(df)
                print(f"    Noms de colonnes nettoys")
                
                # Nettoyer les données
                df = self._clean_dataframe(df, sheet_name=sheet_name)
                print(f"    Nettoyage russi - Shape aprs nettoyage: {df.shape}")
                
                if df.empty:
                    print(f"   [WARNING] Feuille vide, ignoree")
                    continue
                
                # Détecter le type de feuille avec OpenAI Vision
                print(f"    Dtection du type de feuille...")
                sheet_type, confidence = self._detect_sheet_type_with_vision(
                    excel_file, sheet_name, df
                )
                print(f"    Type dtect: {sheet_type} (confiance: {confidence:.2%})")
                
                # Extraire le mapping des colonnes
                print(f"   [INFO] Extraction du mapping des colonnes...")
                columns_mapping = self._extract_columns_mapping(df)
                print(f"    Mapping extrait: {columns_mapping}")
                
                # Identifier les lignes non mappées
                print(f"    Identification des lignes non mappes...")
                unmapped_rows = self._identify_unmapped_rows(df, columns_mapping)
                print(f"    Lignes  mapper: {len(unmapped_rows)}")
                
                # Nettoyer le DataFrame avant la prévisualisation
                # Supprimer les lignes totalement vides
                df = df.dropna(how="all")
                
                # Remplacer toutes les cellules vides par 0
                df = df.fillna(0)
                
                # Prévisualisation (20 premières lignes)
                print(f"    Cration de la prvisualisation...")
                data_preview = self._create_data_preview(df, columns_mapping)
                print(f"    Prvisualisation cre")
                
                # Structuration financière automatique
                print(f"   [INFO] Structuration financiere...")
                try:
                    structured_data = self.structurer.process_dataframe(df, columns_mapping)
                    print(f"    Structuration russie")
                except Exception as e:
                    print(f"   [WARNING] Erreur structuration: {e}")
                    structured_data = None
                
                sheet_info = {
                    'sheet_name': sheet_name,
                    'detected_type': sheet_type,
                    'confidence': confidence,
                    'columns_mapping': columns_mapping,
                    'data_preview': data_preview,
                    'data': df,  # DataFrame complet pour traitement ultérieur
                    'unmapped_rows': unmapped_rows,
                    'total_rows': len(df),
                    'structured_data': structured_data  # Données structurées
                }
                
                result['sheets'].append(sheet_info)
                result['total_rows'] += len(df)
                
                print(f"   [SUCCESS] Feuille traitee avec succes")
                print(f"    Lignes totales: {len(df)}")
                
            except Exception as e:
                print(f"   [ERROR] ERREUR lors du traitement de la feuille '{sheet_name}': {type(e).__name__}: {str(e)}")
                import traceback
                print(f"    Traceback complet:")
                traceback.print_exc()
                raise  # Re-lever l'erreur pour qu'elle soit capturée par la vue
        
        return result
    
    def _clean_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Nettoie les noms de colonnes en convertissant les valeurs numériques (années)
        de float à int pour éviter l'affichage de décimales (2021.0 -> 2021).
        """
        new_columns = []
        for col in df.columns:
            # Si la colonne est un nombre (float ou int)
            if isinstance(col, (int, float)):
                # Si c'est un float qui représente un entier (ex: 2021.0)
                if isinstance(col, float) and col == int(col):
                    new_columns.append(int(col))
                else:
                    new_columns.append(col)
            # Si c'est une chaîne qui ressemble à un nombre avec .0
            elif isinstance(col, str):
                try:
                    # Essayer de convertir en float puis en int si c'est un entier
                    num_val = float(col)
                    if num_val == int(num_val):
                        new_columns.append(int(num_val))
                    else:
                        new_columns.append(num_val)
                except (ValueError, TypeError):
                    # Garder tel quel si ce n'est pas un nombre
                    new_columns.append(col)
            else:
                new_columns.append(col)
        
        df.columns = new_columns
        return df
    
    def _clean_dataframe(self, df: pd.DataFrame, sheet_name: str = "") -> pd.DataFrame:
        """
        Nettoie le DataFrame en utilisant le module DataCleaner.
        Remplace l'ancienne logique simplifiée de suppression des lignes/colonnes vides.
        """
        # Utiliser le nettoyeur centralisé
        return clean_dataframe(df, context='financial', remove_totals=True, sheet_name=sheet_name)
    
    def _detect_sheet_type_with_vision(
        self, excel_file: BytesIO, sheet_name: str, df: pd.DataFrame
    ) -> Tuple[str, float]:
        """
        Détecte le type de feuille en utilisant OpenAI Vision.
        Combine l'analyse visuelle avec l'analyse des données.
        
        Returns:
            tuple: (type, confidence) où type est 'BILAN', 'COMPTE_RESULTAT' ou 'UNKNOWN'
        """
        # Méthode 1: Analyse heuristique des numéros de compte
        heuristic_type, heuristic_confidence = self._detect_type_by_accounts(df)
        
        # Méthode 2: Analyse par mots-clés dans le nom de la feuille
        keyword_type, keyword_confidence = self._detect_type_by_keywords(sheet_name, df)
        
        # Combiner les résultats
        if heuristic_confidence > 0.8:
            return heuristic_type, heuristic_confidence
        elif keyword_confidence > 0.7:
            return keyword_type, keyword_confidence
        else:
            # Si incertain, utiliser OpenAI Vision pour analyse visuelle
            return self._detect_type_with_ai_vision(df, sheet_name)
    
    def _detect_type_by_accounts(self, df: pd.DataFrame) -> Tuple[str, float]:
        """
        Détecte le type en analysant les numéros de compte.
        Classe 1-5 = Bilan, Classe 6-7 = Compte de Résultat
        """
        account_column = self._find_account_column(df)
        
        if account_column is None:
            return 'UNKNOWN', 0.0
        
        accounts = df[account_column].dropna().astype(str)
        
        # Extraire les premiers chiffres des comptes
        bilan_count = 0
        cr_count = 0
        
        for account in accounts:
            # Nettoyer et extraire le premier chiffre
            clean_account = re.sub(r'[^0-9]', '', str(account))
            if clean_account:
                first_digit = int(clean_account[0])
                
                if 1 <= first_digit <= 5:
                    bilan_count += 1
                elif 6 <= first_digit <= 7:
                    cr_count += 1
        
        total = bilan_count + cr_count
        
        if total == 0:
            return 'UNKNOWN', 0.0
        
        bilan_ratio = bilan_count / total
        cr_ratio = cr_count / total
        
        if bilan_ratio > 0.7:
            return 'BILAN', bilan_ratio
        elif cr_ratio > 0.7:
            return 'COMPTE_RESULTAT', cr_ratio
        else:
            return 'UNKNOWN', max(bilan_ratio, cr_ratio)
    
    def _detect_type_by_keywords(
        self, sheet_name: str, df: pd.DataFrame
    ) -> Tuple[str, float]:
        """Détecte le type par analyse de mots-clés et de nom de feuille"""
        sheet_name_lower = sheet_name.lower().strip()
        
        # 1. Vérification de correspondance exacte (Haute priorité)
        if any(kw in sheet_name_lower for kw in ['actif', 'passif', 'bilan']):
            return 'BILAN', 0.95
        if any(kw in sheet_name_lower for kw in ['cdr', 'resultat', 'résultat', 'produit', 'charge']):
            # Vérifier spécifiquement pour CDR NAT
            if 'cdr' in sheet_name_lower or 'nat' in sheet_name_lower:
                return 'COMPTE_RESULTAT', 0.95
            return 'COMPTE_RESULTAT', 0.90

        # 2. Analyse par mots-clés dans les colonnes et le nom
        text = f"{sheet_name} {' '.join(df.columns.astype(str))}"
        text_lower = text.lower()
        
        bilan_keywords = [
            'bilan', 'actif', 'passif', 'patrimoine', 'balance sheet',
            'assets', 'liabilities', 'equity', 'capitaux propres', 'bil_act', 'bil_pas'
        ]
        
        cr_keywords = [
            'compte de résultat', 'résultat', 'charges', 'produits',
            'income statement', 'profit', 'loss', 'revenue', 'expenses',
            'chiffre d\'affaires', 'ca', 'ebitda', 'cdr', 'cdr nat', 'nat'
        ]
        
        bilan_score = sum(1 for kw in bilan_keywords if kw in text_lower)
        cr_score = sum(1 for kw in cr_keywords if kw in text_lower)
        
        total_score = bilan_score + cr_score
        
        if total_score == 0:
            return 'UNKNOWN', 0.0
        
        if bilan_score > cr_score:
            return 'BILAN', min(0.9, bilan_score / (total_score + 2))
        elif cr_score > bilan_score:
            return 'COMPTE_RESULTAT', min(0.9, cr_score / (total_score + 2))
        else:
            return 'UNKNOWN', 0.5
    
    def _detect_type_with_ai_vision(
        self, df: pd.DataFrame, sheet_name: str
    ) -> Tuple[str, float]:
        """
        Utilise OpenAI Vision pour analyser visuellement la structure du tableau.
        Convertit le DataFrame en image pour l'analyse.
        """
        try:
            # Créer une représentation textuelle structurée pour l'IA
            preview_text = f"Nom de la feuille: {sheet_name}\n\n"
            preview_text += "Colonnes: " + ", ".join(df.columns.astype(str)) + "\n\n"
            preview_text += "Aperçu des données (10 premières lignes):\n"
            preview_text += df.head(10).to_string()
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """Tu es un expert comptable. Analyse ce tableau Excel et détermine s'il s'agit d'un BILAN ou d'un COMPTE DE RÉSULTAT.

RÈGLES:
- BILAN: Contient Actif/Passif, comptes classe 1-5, patrimoine de l'entreprise
- COMPTE DE RÉSULTAT: Contient Charges/Produits, comptes classe 6-7, résultat de l'exercice

Réponds UNIQUEMENT avec un JSON:
{
    "type": "BILAN" ou "COMPTE_RESULTAT" ou "UNKNOWN",
    "confidence": 0.0 à 1.0,
    "reasoning": "explication brève"
}"""
                    },
                    {
                        "role": "user",
                        "content": preview_text
                    }
                ],
                temperature=0
            )
            
            import json
            result = json.loads(response.choices[0].message.content.strip())
            
            return result.get('type', 'UNKNOWN'), result.get('confidence', 0.5)
            
        except Exception as e:
            print(f"   [WARNING] Erreur OpenAI Vision: {e}")
            return 'UNKNOWN', 0.0
    
    def _find_account_column(self, df: pd.DataFrame) -> Optional[str]:
        """Trouve la colonne contenant les numéros de compte"""
        account_keywords = ['compte', 'account', 'numero', 'n°', 'code']
        
        for col in df.columns:
            col_lower = str(col).lower()
            if any(kw in col_lower for kw in account_keywords):
                return col
        
        # Si pas trouvé par nom, chercher par contenu
        for col in df.columns:
            # Vérifier si la colonne contient des numéros de compte (format: 1-7 chiffres)
            sample = df[col].dropna().head(10).astype(str)
            if sample.str.match(r'^[1-7]\d{0,6}$').sum() > 5:
                return col
        
        return None
    
    def _extract_columns_mapping(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        Identifie et mappe les colonnes importantes.
        
        Returns:
            dict: {'compte': 'Numéro de compte', 'libelle': 'Libellé', 'montant': 'Montant', ...}
        """
        mapping = {}
        
        # Patterns de recherche pour chaque type de colonne
        patterns = {
            'compte': ['compte', 'account', 'numero', 'n°', 'code'],
            'libelle': ['libelle', 'libellé', 'label', 'description', 'intitulé'],
            'montant': ['montant', 'amount', 'valeur', 'value', 'solde', 'balance'],
            'debit': ['debit', 'débit', 'dr'],
            'credit': ['credit', 'crédit', 'cr'],
            'date': ['date', 'période', 'period']
        }
        
        for col in df.columns:
            col_lower = str(col).lower()
            
            for key, keywords in patterns.items():
                if key not in mapping:  # Éviter les doublons
                    if any(kw in col_lower for kw in keywords):
                        mapping[key] = col
                        break
        
        return mapping
    
    def _identify_unmapped_rows(
        self, df: pd.DataFrame, columns_mapping: Dict[str, str]
    ) -> List[Dict]:
        """
        Identifie les lignes qui nécessitent un mapping manuel.
        
        Returns:
            list: Liste de dictionnaires avec les lignes problématiques
        """
        unmapped = []
        
        account_col = columns_mapping.get('compte')
        libelle_col = columns_mapping.get('libelle')
        montant_col = columns_mapping.get('montant')
        
        for idx, row in df.iterrows():
            issues = []
            
            # Vérifier si le compte est manquant ou invalide
            if account_col:
                account = str(row.get(account_col, '')).strip()
                if not account or account == 'nan':
                    issues.append('compte_manquant')
                elif not self._is_valid_account_number(account):
                    issues.append('compte_invalide')
            else:
                issues.append('colonne_compte_non_detectee')
            
            # Vérifier si le montant est valide
            if montant_col:
                try:
                    montant = float(row.get(montant_col, 0))
                    if montant == 0:
                        issues.append('montant_zero')
                except (ValueError, TypeError):
                    issues.append('montant_invalide')
            
            if issues:
                # Convertir row.to_dict() en format JSON-serializable
                row_dict = {}
                for col, val in row.to_dict().items():
                    # Convertir les clés en strings
                    col_str = str(col)
                    # Convertir les valeurs en types Python natifs
                    if pd.isna(val):
                        row_dict[col_str] = None
                    elif isinstance(val, (int, float)):
                        row_dict[col_str] = float(val)
                    elif isinstance(val, bool):
                        row_dict[col_str] = bool(val)
                    else:
                        row_dict[col_str] = str(val)
                
                unmapped.append({
                    'row_index': int(idx),
                    'data': row_dict,
                    'issues': issues,
                    'libelle': str(row.get(libelle_col, '')) if libelle_col else '',
                    'compte_detecte': str(row.get(account_col, '')) if account_col else ''
                })
        
        return unmapped

    
    def _is_valid_account_number(self, account: str) -> bool:
        """Valide un numéro de compte selon le PCG"""
        clean_account = re.sub(r'[^0-9]', '', str(account))
        
        if not clean_account:
            return False
        
        # Vérifier que le compte commence par 1-7
        if clean_account[0] not in '1234567':
            return False
        
        # Vérifier la longueur (généralement 2-8 chiffres)
        if len(clean_account) < 2 or len(clean_account) > 8:
            return False
        
        return True
    
    def _create_data_preview(
        self, df: pd.DataFrame, columns_mapping: Dict[str, str]
    ) -> List[Dict]:
        """
        Crée une prévisualisation des données pour l'affichage frontend.
        
        Returns:
            list: Liste de dictionnaires représentant les lignes
        """
        preview = []
        
        # Limiter à 20 lignes pour la prévisualisation
        preview_df = df.head(20)
        
        for idx, row in preview_df.iterrows():
            row_data = {
                'index': int(idx),
                'values': {}
            }
            
            # Ajouter toutes les colonnes
            for col in df.columns:
                value = row[col]
                
                # Formater les valeurs avec gestion robuste des Series
                if isinstance(value, pd.Series):
                    # Si value est une Series (cas rare mais possible)
                    if value.isna().all():
                        row_data['values'][str(col)] = 0
                    else:
                        # Prendre la première valeur non-NaN ou 0
                        cleaned = value.fillna(0)
                        first_val = cleaned.iloc[0] if len(cleaned) > 0 else 0
                        # Essayer de convertir en float, sinon garder comme string
                        try:
                            row_data['values'][str(col)] = float(first_val)
                        except (ValueError, TypeError):
                            row_data['values'][str(col)] = str(first_val)
                else:
                    # Cas normal : value est un scalaire
                    if pd.isna(value):
                        row_data['values'][str(col)] = 0
                    elif isinstance(value, (int, float)):
                        # Convertir numpy types en types Python natifs
                        num_value = float(value)
                        
                        # Si c'est un nombre entier (pas de décimales), afficher comme int
                        # Cela inclut les années (2022, 2023, etc.) et les montants entiers
                        if num_value == int(num_value):
                            row_data['values'][str(col)] = int(num_value)
                        else:
                            row_data['values'][str(col)] = num_value
                    elif isinstance(value, bool):
                        row_data['values'][str(col)] = bool(value)
                    else:
                        # Convertir tout le reste en string
                        row_data['values'][str(col)] = str(value)
            
            # Ajouter des indicateurs de validation
            account_col = columns_mapping.get('compte')
            if account_col and str(account_col) in row_data['values']:
                account = str(row_data['values'][str(account_col)])
                row_data['is_valid_account'] = self._is_valid_account_number(account)
            
            preview.append(row_data)
        
        return preview


    
    def validate_and_normalize_data(
        self, df: pd.DataFrame, columns_mapping: Dict[str, str], sheet_type: str
    ) -> pd.DataFrame:
        """
        Valide et normalise les données pour l'enregistrement en base.
        
        Args:
            df: DataFrame source
            columns_mapping: Mapping des colonnes
            sheet_type: 'BILAN' ou 'COMPTE_RESULTAT'
            
        Returns:
            DataFrame normalisé prêt pour l'enregistrement
        """
        # Défensive: s'assurer que 'df' est bien un DataFrame
        if not isinstance(df, pd.DataFrame):
            try:
                df = pd.DataFrame(df)
                print("[INFO] validate_and_normalize_data: entree convertie en DataFrame")
            except Exception as e:
                print(f"[ERROR] Impossible de convertir l'entree en DataFrame: {e}")
                raise

        normalized = pd.DataFrame()

        try:
            # Extraire les colonnes mappées de façon robuste
            if 'compte' in columns_mapping and columns_mapping['compte'] in df.columns:
                normalized['numero_compte'] = df[columns_mapping['compte']].astype(str).str.strip()

            if 'libelle' in columns_mapping and columns_mapping['libelle'] in df.columns:
                normalized['libelle'] = df[columns_mapping['libelle']].astype(str).str.strip()

            if 'montant' in columns_mapping and columns_mapping['montant'] in df.columns:
                normalized['montant_ar'] = pd.to_numeric(
                    df[columns_mapping['montant']], errors='coerce'
                ).fillna(0)
            else:
                # garantir que la colonne existe pour les filtres suivants
                normalized['montant_ar'] = 0

            if 'date' in columns_mapping and columns_mapping['date'] in df.columns:
                normalized['date'] = pd.to_datetime(
                    df[columns_mapping['date']], errors='coerce'
                )

            # Ajouter des champs spécifiques selon le type
            if sheet_type == 'BILAN':
                # Déterminer type_bilan et catégorie selon le numéro de compte
                if 'numero_compte' in normalized.columns:
                    normalized['type_bilan'] = normalized['numero_compte'].apply(
                        self._determine_bilan_type
                    )
                    normalized['categorie'] = normalized['numero_compte'].apply(
                        self._determine_bilan_category
                    )

            elif sheet_type == 'COMPTE_RESULTAT':
                if 'numero_compte' in normalized.columns:
                    normalized['nature'] = normalized['numero_compte'].apply(
                        self._determine_cr_nature
                    )

            # Supprimer les lignes invalides de façon sûre
            if 'numero_compte' in normalized.columns:
                # Filtrer les lignes où numero_compte n'est pas NaN
                mask = normalized['numero_compte'].notna()
                normalized = normalized[mask]

            # S'assurer que 'montant_ar' est une Series avant la comparaison
            if 'montant_ar' in normalized.columns:
                # Filtrer les lignes où montant_ar n'est pas égal à 0
                mask = normalized['montant_ar'] != 0
                normalized = normalized[mask]

            return normalized

        except Exception as e:
            # Log détaillé pour debug: types et colonnes
            try:
                cols = list(df.columns)
            except Exception:
                cols = str(df)
            print(f"[ERROR] Erreur validate_and_normalize_data: {e}")
            print(f"   df.columns: {cols}")
            raise
    
    def _determine_bilan_type(self, compte) -> str:
        """Détermine si un compte est ACTIF ou PASSIF"""
        # Gérer différents types d'entrées (scalar, Series, ndarray)
        try:
            # Si c'est une Series pandas, extraire la première valeur
            if isinstance(compte, pd.Series):
                if compte.empty:
                    return 'ACTIF'
                compte = compte.iloc[0]
            # Si c'est un ndarray, extraire le premier élément
            elif hasattr(compte, '__len__') and not isinstance(compte, str):
                if len(compte) == 0:
                    return 'ACTIF'
                compte = compte[0]
        except Exception:
            pass

        if pd.isna(compte):
            return 'ACTIF'

        compte = str(compte).strip()
        if not compte or compte == 'nan':
            return 'ACTIF'

        first_digit = compte[0]
        
        # Classe 1-3 = PASSIF, Classe 4-5 = ACTIF (simplifié)
        if first_digit in '123':
            return 'PASSIF'
        else:
            return 'ACTIF'
    
    def _determine_bilan_category(self, compte) -> str:
        """Détermine la catégorie du bilan"""
        try:
            # Si c'est une Series pandas, extraire la première valeur
            if isinstance(compte, pd.Series):
                if compte.empty:
                    return 'ACTIF_COURANTS'
                compte = compte.iloc[0]
            # Si c'est un ndarray, extraire le premier élément
            elif hasattr(compte, '__len__') and not isinstance(compte, str):
                if len(compte) == 0:
                    return 'ACTIF_COURANTS'
                compte = compte[0]
        except Exception:
            pass

        if pd.isna(compte):
            return 'ACTIF_COURANTS'

        compte = str(compte).strip()
        if not compte or compte == 'nan':
            return 'ACTIF_COURANTS'

        first_digit = compte[0]
        
        mapping = {
            '1': 'CAPITAUX_PROPRES',
            '2': 'ACTIF_NON_COURANTS',
            '3': 'ACTIF_COURANTS',
            '4': 'PASSIFS_COURANTS',
            '5': 'ACTIF_COURANTS'
        }
        
        return mapping.get(first_digit, 'ACTIF_COURANTS')
    
    def _determine_cr_nature(self, compte) -> str:
        """Détermine si un compte est CHARGE ou PRODUIT"""
        try:
            # Si c'est une Series pandas, extraire la première valeur
            if isinstance(compte, pd.Series):
                if compte.empty:
                    return 'CHARGE'
                compte = compte.iloc[0]
            # Si c'est un ndarray, extraire le premier élément
            elif hasattr(compte, '__len__') and not isinstance(compte, str):
                if len(compte) == 0:
                    return 'CHARGE'
                compte = compte[0]
        except Exception:
            pass

        if pd.isna(compte):
            return 'CHARGE'

        compte = str(compte).strip()
        if not compte or compte == 'nan':
            return 'CHARGE'

        first_digit = compte[0]
        
        # Classe 6 = CHARGE, Classe 7 = PRODUIT
        if first_digit == '6':
            return 'CHARGE'
        elif first_digit == '7':
            return 'PRODUIT'
        else:
            return 'CHARGE'  # Par défaut
