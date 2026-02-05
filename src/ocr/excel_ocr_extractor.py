"""
Module d'extraction OCR pour fichiers Excel utilisant OpenAI Vision API.
Convertit les feuilles Excel en images et extrait les données structurées.
"""

import io
import base64
import json
import re
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
from PIL import Image
from typing import Dict, List, Tuple, Optional
from io import BytesIO

# Utiliser un backend non-interactif pour matplotlib
matplotlib.use('Agg')

# Import du module de structuration financière
from ocr.financial_data_structurer import FinancialDataStructurer

# Import du module de nettoyage de données
from ocr.data_cleaner import clean_dataframe

# Import des constantes
from ocr.constants import EXCLUDED_SHEET_NAMES


def convert_excel_sheet_to_image(df: pd.DataFrame, sheet_name: str) -> Image.Image:
    """
    Convertit un DataFrame (feuille Excel) en image PNG.
    
    Args:
        df: DataFrame pandas représentant la feuille Excel
        sheet_name: Nom de la feuille (pour le titre)
    
    Returns:
        PIL.Image: Image de la feuille Excel
    """
    # Limiter le nombre de lignes pour éviter des images trop grandes
    max_rows = 50
    if len(df) > max_rows:
        df_display = df.head(max_rows)
        truncated = True
    else:
        df_display = df
        truncated = False
    
    # Calculer la taille de la figure en fonction du nombre de colonnes/lignes
    num_cols = len(df_display.columns)
    num_rows = len(df_display)
    
    fig_width = min(20, max(12, num_cols * 2))
    fig_height = min(30, max(8, num_rows * 0.5 + 2))
    
    # Créer la figure
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('tight')
    ax.axis('off')
    
    # Ajouter un titre
    title = f"Feuille: {sheet_name}"
    if truncated:
        title += f" (Affichage des {max_rows} premières lignes sur {len(df)})"
    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    
    # Créer le tableau
    # Convertir les valeurs en strings et gérer les NaN
    cell_text = []
    for _, row in df_display.iterrows():
        row_text = []
        for val in row:
            if pd.isna(val):
                row_text.append('')
            elif isinstance(val, float):
                row_text.append(f'{val:.2f}')
            else:
                row_text.append(str(val))
        cell_text.append(row_text)
    
    # Calculer les largeurs de colonnes (donner plus de place à la première colonne de libellés)
    # Si on détecte bcp de colonnes, on réduit les largeurs
    if num_cols > 5:
        col_widths = [0.12] * num_cols
        col_widths[0] = 0.20 # Description plus large
        if num_cols > 1: col_widths[1] = 0.10 # Code compte souvent après le libellé ou avant
    else:
        col_widths = [0.25] + [0.12] * (num_cols - 1)
    
    if num_cols == 1: col_widths = [0.9]

    table = ax.table(
        cellText=cell_text,
        colLabels=df_display.columns,
        loc='center',
        cellLoc='left',
        colWidths=col_widths
    )
    
    # Styliser le tableau
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 2)
    
    # Colorer l'en-tête
    for i in range(num_cols):
        cell = table[(0, i)]
        cell.set_facecolor('#4472C4')
        cell.set_text_props(weight='bold', color='white')
    
    # Sauvegarder en mémoire
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    
    buf.seek(0)
    image = Image.open(buf)
    
    return image


def encode_image_to_base64(image: Image.Image) -> str:
    """
    Encode une image PIL en base64 pour l'API OpenAI Vision.
    
    Args:
        image: Image PIL
    
    Returns:
        str: Image encodée en base64
    """
    buffered = BytesIO()
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(buffered, format="PNG", quality=95)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def extract_sheet_data_with_vision(
    image: Image.Image, 
    client, 
    model: str,
    sheet_name: str
) -> Dict:
    """
    Extrait les données d'une feuille Excel via OpenAI Vision API.
    
    Args:
        image: Image de la feuille Excel
        client: Client OpenAI
        model: Modèle à utiliser (ex: "gpt-4o")
        sheet_name: Nom de la feuille
    
    Returns:
        dict: {
            'columns': List[str],
            'rows': List[List[str]],
            'metadata': dict
        }
    """
    base64_image = encode_image_to_base64(image)
    
    # Ajouter des indices contextuels basés sur le nom
    name_hint = ""
    sn_lower = sheet_name.lower()
    if any(k in sn_lower for k in ['actif', 'passif', 'bilan']):
        name_hint = "Cette feuille est probablement un BILAN (Actif ou Passif). Cherche bien les numéros de compte et les montants."
    elif any(k in sn_lower for k in ['cdr', 'resultat', 'nat']):
        name_hint = "Cette feuille est probablement un COMPTE DE RÉSULTAT (CDR NAT). Cherche bien les charges et produits."

    prompt = f"""Analyse cette feuille Excel nommée "{sheet_name}" et extrait TOUTES les données dans un format structuré.
{name_hint}

INSTRUCTIONS IMPORTANTES:

1. **MÉTADONNÉES D'EN-TÊTE** (si présentes en haut du document, avant le tableau principal):
   - Nom de l'entreprise (peut être en gras ou en haut)
   - NIF (Numéro d'Identification Fiscale) - cherche "NIF:" ou "NIF :"
   - STAT (Numéro statistique) - cherche "STAT:" ou "STAT :"
   - Adresse (peut être sur une ou plusieurs lignes)
   
   Si ces informations ne sont PAS présentes, laisse les champs vides ou null.

2. Identifie les colonnes (en-têtes) du tableau. PORTE UNE ATTENTION PARTICULIÈRE à :
   - La colonne des LIBELLÉS (Postes, Rubriques)
   - La colonne des NUMÉROS DE COMPTE (Codes comptables comme 101, 201, 601, etc.)
3. Extrait TOUTES les lignes de données visibles. NE MANQUE PAS la colonne descriptive et les codes de compte.
4. Préserve les valeurs exactes (nombres, textes, dates).
5. Si une cellule est vide, utilise null.
6. Identifie si possible le type de données (Bilan, Compte de Résultat, ou Autre).

Retourne UNIQUEMENT un JSON valide avec cette structure:
{{
  "company_metadata": {{
    "nom_entreprise": "...",
    "nif": "...",
    "stat": "...",
    "adresse": "..."
  }},
  "columns": ["Colonne1", "Colonne2", ...],
  "rows": [
    ["valeur1", "valeur2", ...],
    ["valeur1", "valeur2", ...],
    ...
  ],
  "detected_type": "BILAN" ou "COMPTE_RESULTAT" ou "UNKNOWN",
  "confidence": 0.0 à 1.0
}}

RÈGLES DE DÉTECTION:
- BILAN: Contient des comptes classe 1-5, mots-clés: Actif, Passif, Patrimoine
- COMPTE_RESULTAT: Contient des comptes classe 6-7, mots-clés: Charges, Produits, Résultat
- UNKNOWN: Si incertain

NE RETOURNE QUE LE JSON, AUCUN TEXTE SUPPLÉMENTAIRE."""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=8000,
            temperature=0
        )
        
        content = response.choices[0].message.content.strip()
        
        # Nettoyer le JSON avec plusieurs stratégies
        original_content = content  # Garder pour le debug
        
        # Stratégie 1: Enlever les markdown code blocks
        if content.startswith('```'):
            # Extraire le contenu entre les backticks
            parts = content.split('```')
            if len(parts) >= 2:
                content = parts[1]
                # Enlever le label de langage (json, JSON, etc.)
                if content.strip().lower().startswith('json'):
                    content = content[4:]
        
        content = content.strip()
        
        # Stratégie 2: Chercher le JSON dans le texte (entre { et })
        if not content.startswith('{'):
            # Trouver le premier { et le dernier }
            start_idx = content.find('{')
            end_idx = content.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                content = content[start_idx:end_idx + 1]
        
        # Stratégie 3: Parser le JSON
        try:
            result = json.loads(content)
        except json.JSONDecodeError as json_err:
            # Log détaillé pour debug
            print(f"   [ERROR] Erreur parsing JSON pour {sheet_name}")
            print(f"   Position erreur: ligne {json_err.lineno}, colonne {json_err.colno}")
            print(f"   Message: {json_err.msg}")
            print(f"   Contenu nettoye (premiers 500 chars):")
            print(f"   {content[:500]}")
            print(f"   Contenu original (premiers 500 chars):")
            print(f"   {original_content[:500]}")
            
            # Tentative de réparation du JSON tronqué
            try:
                print(f"   [FIX] Tentative de reparation du JSON tronque...")
                content_fixed = content.strip()
                
                # 0. Supprimer les caractères tronqués à la fin (non-structuraux)
                # On recule jusqu'à trouver un caractère structural JSON : } ] , "
                while content_fixed and content_fixed[-1] not in ('}', ']', ',', '"'):
                    content_fixed = content_fixed[:-1].strip()
                
                # Si ça finit par une virgule, on l'enlève car elle attend une suite tronquée
                if content_fixed.endswith(','):
                    content_fixed = content_fixed[:-1].strip()
                
                # 1. Fermer les chaînes de caractères si nécessaire
                if content_fixed.count('"') % 2 != 0:
                    content_fixed += '"'
                
                # 2. Fermer les structures [ ] et { }
                open_braces = content_fixed.count('{') - content_fixed.count('}')
                open_brackets = content_fixed.count('[') - content_fixed.count(']')
                
                # Fermer d'abord les éléments internes
                for _ in range(open_brackets):
                    content_fixed += ']'
                for _ in range(open_braces):
                    content_fixed += '}'
                
                # Nettoyer les virgules orphelines finales (cas particulier après ajout de clôtures)
                content_fixed = re.sub(r',\s*}', '}', content_fixed)
                content_fixed = re.sub(r',\s*]', ']', content_fixed)
                
                result = json.loads(content_fixed)
                print(f"   [SUCCESS] JSON repare avec succes !")
                # Ajouter une métadonnée pour indiquer la troncature
                if "metadata" not in result: result["metadata"] = {}
                result["metadata"]["is_truncated"] = True
                result["metadata"]["repair_warning"] = "La reponse a ete tronquee et reparee. Certaines donnees peuvent manquer."
            except Exception as repair_err:
                print(f"   [ERROR] Echec de la reparation: {repair_err}")
                # Si la réparation sophistiquée échoue, tenter une réparation basique de virgules
                try:
                    content_fixed = re.sub(r',\s*}', '}', content)
                    content_fixed = re.sub(r',\s*]', ']', content_fixed)
                    result = json.loads(content_fixed)
                    print(f"   [SUCCESS] JSON repare (virgules uniquement)")
                except Exception:
                    # Si tout échoue, lever l'erreur originale avec plus de contexte
                    raise ValueError(
                        f"Impossible de parser la reponse JSON de OpenAI pour la feuille '{sheet_name}'. "
                        f"Erreur: {json_err.msg} a la position ligne {json_err.lineno}, colonne {json_err.colno}. "
                        f"Contenu (premiers 500 chars): {content[:500]}"
                    ) from json_err
        
        return result
        
    except Exception as e:
        print(f"[ERROR] Erreur extraction Vision pour {sheet_name}: {e}")
        raise e


def parse_vision_response_to_dataframe(vision_data: Dict) -> pd.DataFrame:
    """
    Convertit la réponse de Vision API en DataFrame pandas.
    
    Args:
        vision_data: Données extraites par Vision API
    
    Returns:
        pd.DataFrame: DataFrame structuré
    """
    columns = vision_data.get('columns', [])
    rows = vision_data.get('rows', [])

    if not columns or not rows:
        return pd.DataFrame()

    # Normaliser les lignes : s'assurer que chaque ligne a exactement len(columns) éléments
    normalized_rows = []
    col_count = len(columns)
    mismatch_count = 0

    for i, row in enumerate(rows):
        # Forcer en liste - gérer les Series pandas aussi
        if isinstance(row, pd.Series):
            row = row.tolist()
        elif not isinstance(row, (list, tuple)):
            row = [row]
        else:
            row = list(row)

        # Maintenant row est garantie d'être une liste
        row_len = len(row)
        if row_len < col_count:
            # padding des valeurs manquantes
            row = row + [None] * (col_count - row_len)
            mismatch_count += 1
        elif row_len > col_count:
            # tronquer les valeurs excédentaires
            row = row[:col_count]
            mismatch_count += 1

        normalized_rows.append(row)

    if mismatch_count > 0:
        print(f"[WARNING] Vision -> DataFrame: {mismatch_count} ligne(s) avec nombre de colonnes different du header ({col_count}). Les lignes ont ete pad/troncees.")

    # Créer le DataFrame en toute sécurité
    try:
        df = pd.DataFrame(normalized_rows, columns=columns)
    except Exception as e:
        print(f"   [ERROR] Echec creation DataFrame a partir de la reponse Vision: {e}")
        # En dernier recours, retourner un DataFrame vide
        return pd.DataFrame()
    
    # Nettoyer les données
    # Remplacer 'null' strings par NaN
    df = df.replace('null', pd.NA)
    df = df.replace('', pd.NA)
    
    # Tenter de convertir les colonnes numériques
    for col in df.columns:
        try:
            # Essayer de convertir en numérique
            df[col] = pd.to_numeric(df[col])
        except Exception:
            pass
    
    return df


def extract_excel_with_ocr(file, client, model: str) -> Dict:
    """
    Fonction principale d'extraction OCR pour fichiers Excel.
    
    Args:
        file: Fichier Excel (FileStorage ou bytes)
        client: Client OpenAI
        model: Modèle OpenAI à utiliser
    
    Returns:
        dict: Structure similaire à ExcelParser.parse_excel_file()
        {
            'file_name': str,
            'sheets': [
                {
                    'sheet_name': str,
                    'detected_type': str,
                    'confidence': float,
                    'columns_mapping': dict,
                    'data_preview': list,
                    'data': DataFrame,
                    'unmapped_rows': list,
                    'total_rows': int,
                    'extraction_method': 'OCR'
                }
            ],
            'total_rows': int,
            'extraction_method': 'OCR'
        }
    """
    print("\n[INFO] DEBUT EXTRACTION OCR EXCEL")
    print("=" * 80)
    
    # Lire le fichier Excel
    if hasattr(file, 'read'):
        file_content = file.read()
        file.seek(0)
        file_name = getattr(file, 'name', 'fichier_excel.xlsx')
    else:
        file_content = file
        file_name = 'fichier_excel.xlsx'
    
    excel_file = BytesIO(file_content)
    
    # Charger toutes les feuilles avec pandas (pour obtenir les noms)
    xl_file = pd.ExcelFile(excel_file)
    sheet_names = xl_file.sheet_names
    
    result = {
        'file_name': file_name,
        'sheets': [],
        'total_rows': 0,
        'extraction_method': 'OCR'
    }
    
    for sheet_name in sheet_names:
        # Sauter les feuilles exclues
        if sheet_name in EXCLUDED_SHEET_NAMES or sheet_name.upper() in EXCLUDED_SHEET_NAMES:
            print(f"   [INFO] Feuille '{sheet_name}' ignoree (exclue)")
            continue

        # Limite de sécurité pour éviter les timeouts extrêmes
        if len(result['sheets']) >= 5:
            print(f"   [WARNING] Limite de 5 feuilles atteinte. Saut des feuilles restantes pour eviter le timeout.")
            break

        print(f"\n[INFO] Traitement feuille: {sheet_name}")
        
        # Lire la feuille avec pandas (pour avoir la structure de base)
        df_original = pd.read_excel(excel_file, sheet_name=sheet_name)
        
        # Nettoyer le DataFrame
        df_original = df_original.dropna(axis=1, how='all')
        df_original = df_original.dropna(axis=0, how='all')
        df_original = df_original.reset_index(drop=True)
        
        if df_original.empty:
            print(f"   [WARNING] Feuille vide, ignoree")
            continue
        
        print(f"   [INFO] Conversion en image...")
        # Convertir en image
        image = convert_excel_sheet_to_image(df_original, sheet_name)
        
        print(f"   [INFO] Extraction OCR avec OpenAI Vision...")
        # Extraire avec Vision API
        vision_data = extract_sheet_data_with_vision(image, client, model, sheet_name)
        
        # Convertir en DataFrame
        df_extracted = parse_vision_response_to_dataframe(vision_data)
        
        if df_extracted.empty:
            print(f"   [WARNING] Extraction vide, utilisation des donnees originales")
            df_extracted = df_original
            detected_type = 'UNKNOWN'
            confidence = 0.0
        else:
            detected_type = vision_data.get('detected_type', 'UNKNOWN')
            confidence = vision_data.get('confidence', 0.5)
        
        # 🧹 NETTOYAGE AUTOMATIQUE DES DONNÉES
        print(f"   [INFO] Nettoyage automatique des donnees (feuille: {sheet_name})...")
        df_extracted = clean_dataframe(
            df_extracted, 
            context='financial',
            remove_totals=True,  # Supprimer les totaux suite à la demande utilisateur
            sheet_name=sheet_name
        )
        
        # Utiliser le parser existant pour extraire les métadonnées
        from ocr.excel_parser import ExcelParser
        parser = ExcelParser(client, model)
        
        # Extraire le mapping des colonnes
        columns_mapping = parser._extract_columns_mapping(df_extracted)
        
        # Identifier les lignes non mappées
        unmapped_rows = parser._identify_unmapped_rows(df_extracted, columns_mapping)
        
        # Nettoyer le DataFrame avant la prévisualisation
        # Supprimer les lignes totalement vides
        df_extracted = df_extracted.dropna(how="all")
        
        # Remplacer toutes les cellules vides par 0
        df_extracted = df_extracted.fillna(0)
        
        # Créer la prévisualisation
        data_preview = parser._create_data_preview(df_extracted, columns_mapping)
        
        # Extraire les métadonnées d'entreprise de la réponse Vision
        company_metadata = vision_data.get('company_metadata', {})
        if company_metadata:
            print(f"   [INFO] Metadonnees extraites: {company_metadata.get('nom_entreprise', 'N/A')}")
        
        # Structuration financière automatique
        print(f"   [INFO] Structuration financiere...")
        try:
            structurer = FinancialDataStructurer()
            structured_data = structurer.process_dataframe(df_extracted, columns_mapping, company_metadata)
            print(f"   [SUCCESS] Structuration reussie")
        except Exception as e:
            print(f"   [WARNING] Erreur structuration: {e}")
            structured_data = None
        
        sheet_info = {
            'sheet_name': sheet_name,
            'detected_type': detected_type,
            'confidence': confidence,
            'columns_mapping': columns_mapping,
            'data_preview': data_preview,
            'data': df_extracted,
            'unmapped_rows': unmapped_rows,
            'total_rows': len(df_extracted),
            'extraction_method': 'OCR',
            'structured_data': structured_data  # Données structurées
        }
        
        result['sheets'].append(sheet_info)
        result['total_rows'] += len(df_extracted)
        
        print(f"   [SUCCESS] Type detecte: {detected_type} (confiance: {confidence:.2%})")
        print(f"   [INFO] Lignes extraites: {len(df_extracted)}")
        print(f"   [WARNING] Lignes a mapper: {len(unmapped_rows)}")
    
    print("\n[SUCCESS] EXTRACTION OCR TERMINEE")
    print(f"   [INFO] Total feuilles: {len(result['sheets'])}")
    print(f"   [INFO] Total lignes: {result['total_rows']}")
    print("=" * 80 + "\n")
    
    return result
