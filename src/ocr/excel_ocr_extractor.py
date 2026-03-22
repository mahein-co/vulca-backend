"""
Module d'extraction OCR pour fichiers Excel utilisant OpenAI Vision API.
Convertit les feuilles Excel en images et extrait les données structurées.
"""

import io
import base64
import json
import re
import pandas as pd
import openpyxl
import matplotlib
import matplotlib.pyplot as plt # type: ignore (non-interactif)
# suppression de l'import direct pour utiliser l'API objet
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


from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

def convert_excel_sheet_to_image(df: pd.DataFrame, sheet_name: str) -> Image.Image:
    """
    Convertit un DataFrame (feuille Excel) en image PNG de manière thread-safe.
    
    Args:
        df: DataFrame pandas représentant la feuille Excel
        sheet_name: Nom de la feuille (pour le titre)
    
    Returns:
        PIL.Image: Image de la feuille Excel
    """
    # Limiter le nombre de lignes pour éviter des images trop grandes
    max_rows = 500
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
    
    # --- UTILISATION DE L'API OBJET (THREAD-SAFE) ---
    # On évite plt.subplots() car il utilise un état global non thread-safe
    fig = Figure(figsize=(fig_width, fig_height))
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    
    ax.axis('tight')
    ax.axis('off')
    
    # Ajouter un titre
    title = f"Feuille: {sheet_name}"
    if truncated:
        title += f" (Affichage des {max_rows} premières lignes sur {len(df)})"
    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    
    # Créer le tableau
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
    
    # Largeurs de colonnes
    if num_cols > 5:
        col_widths = [0.12] * num_cols
        col_widths[0] = 0.20
        if num_cols > 1: col_widths[1] = 0.10
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
    
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 2)
    
    for i in range(num_cols):
        cell = table[(0, i)]
        cell.set_facecolor('#4472C4')
        cell.set_text_props(weight='bold', color='white')
    
    # Sauvegarder en mémoire via le canvas directement
    buf = BytesIO()
    try:
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
        buf.seek(0)
        
        # Vérification critique : le buffer est-il valide ?
        if buf.getbuffer().nbytes == 0:
            raise ValueError("Le buffer de l'image est vide après savefig.")
            
        image = Image.open(buf)
        # Forcer le chargement pour vérifier que l'image est valide immédiatement
        image.load()
        return image
    except Exception as e:
        print(f"[ERROR] Erreur lors de la génération de l'image pour {sheet_name}: {e}")
        # Retenter une fois avec une taille plus petite si c'est un problème de mémoire ? 
        # Pour l'instant on lève l'exception pour avoir le log exact.
        raise e


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
    sheet_name: str,
    df_text: str = ""
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

INSTRUCTIONS IMPORTANTES POUR L'EXTRACTION:
1. **DÉTECTION DES COMPTES** :
   - Cherche les codes comptables (ex: 101, 201, 401, 601, 701...).
   - **ATTENTION** : Ne confond pas les indices de lignes (ex: "1 -", "2 -", "3 -", "1.", "2.") avec des numéros de compte. Si un chiffre est suivi d'un tiret ou semble être un numéro de chapitre (1, 2, 3), il appartient au LIBELLÉ, pas à la colonne de compte.
   - Si une ligne n'a pas de numéro de compte explicite, laisse `null`.

2. **DÉTECTION DES LIBELLÉS** :
   - Capture le texte exact du poste ou libellé (ex: "Capital social", "ACHATS CONSOMMES", etc.).
   - Inclut les préfixes d'indices dans le libellé (ex: "1 - PRODUCTION DE L'EXERCICE").

3. **JOURNAUX ET PIÈCES (IMPORTANT)** :
   - Si tu vois des colonnes "N° Pièce", "Pièce", "Réf", "Date", "Débit", "Crédit", c'est un JOURNAL ou GRAND LIVRE.
   - **EXTRAIS SYSTÉMATIQUEMENT** le numéro de pièce (ex: PJ001, PI001) et la date exacte pour chaque ligne.
   - Si une ligne n'a pas de numéro de pièce mais que la ligne précédente en a un pour la même opération, propage-le.

4. **DÉTECTION DES MONTANTS (CRUCIAL)** :
   - Les montants sont souvent à droite. Cherche les colonnes "BRUT", "AMORTISSEMENTS", "NET", "SOLDE", "VALEUR", "DÉBIT", "CRÉDIT".
   - **EXTRAIS TOUTES LES COLONNES NUMÉRIQUES**. Si tu vois des chiffres pour chaque ligne, crée une colonne correspondante.
   - Si une colonne de montants correspond à une année, utilise l'année comme titre (ex: "2024").

5. **EXTRACTION EXHAUSTIVE (CRUCIAL)** :
   - Extraits TOUTES les lignes du document, du début à la fin.
   - **NE FILTRE PAS PAR ANNÉE** : Même si la feuille s'appelle "{sheet_name}", tu dois extraire TOUTES les dates présentes (1900, 2020, 2024, etc.).
   - Ne saute aucune ligne sous prétexte qu'elle appartient à une ancienne période.
   - Si le document fait 500 lignes, tu dois extraire les 500 lignes.

VOICI LES DONNÉES DE LA FEUILLE (format CSV brut pour référence si l'image est floue):
---
{df_text}
---

6. **FORMAT DE RÉPONSE** :
Retourne UNIQUEMENT un JSON valide avec cette structure:
{{
  "company_metadata": {{
    "nom_entreprise": "...",
    "nif": "...", "stat": "...", "adresse": "...", "periode": "..."
  }},
  "columns": ["Date", "N° Pièce", "Libellé", "Compte", "Débit", "Crédit", ...],
  "rows": [
    ["2026-01-01", "PJ001", "Banque", "512", "5000000", "0"],
    ["2026-01-01", "PJ001", "Capital", "101", "0", "5000000"],
    ...
  ],
  "detected_type": "BILAN", "COMPTE_RESULTAT", "JOURNAL" ou "UNKNOWN",
  "confidence": 0.0 à 1.0
}}

RÈGLES DE DÉTECTION DU TYPE:
- BILAN: Actif/Passif, Immobilisations (Classe 1-5).
- COMPTE_RESULTAT: Charges/Produits, Chiffre d'Affaires (Classe 6-7).
- JOURNAL: Colonnes Date, Pièce, Libellé, Débit, Crédit.

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
        
        # --- GESTION DES REFUS OPENAI (SAFETY REFUSAL) ---
        if "I'm sorry" in content or "m'excuse" in content or "peux pas" in content or len(content) < 50:
            print(f"   [WARNING] Refus Vision pour {sheet_name} (ou réponse non-JSON). Tentative de fallback texte uniquement...")
            
            # Nouveau prompt simplifié pour le mode texte uniquement
            fallback_prompt = f"""Analyse ces données CSV issues de la feuille Excel "{sheet_name}" et extrais les données structurées.
            
            DONNÉES (CSV):
            {df_text}
            
            {prompt.split('INSTRUCTIONS IMPORTANTES')[1] if 'INSTRUCTIONS IMPORTANTES' in prompt else prompt}
            """
            
            fallback_response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": fallback_prompt}],
                max_tokens=8000,
                temperature=0
            )
            content = fallback_response.choices[0].message.content.strip()
            print(f"   [INFO] Reponse fallback recue (longueur: {len(content)})")

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
            
            # Tentative de réparation du JSON tronqué
            try:
                print(f"   [FIX] Tentative de reparation du JSON tronque...")
                content_fixed = content.strip()
                
                # 0. Supprimer les caractères tronqués à la fin (non-structuraux)
                while content_fixed and content_fixed[-1] not in ('}', ']', ',', '"'):
                    content_fixed = content_fixed[:-1].strip()
                
                if content_fixed.endswith(','):
                    content_fixed = content_fixed[:-1].strip()
                
                # 1. Fermer les chaînes de caractères si nécessaire
                if content_fixed.count('"') % 2 != 0:
                    content_fixed += '"'
                
                # 2. Fermer les structures [ ] et { }
                open_braces = content_fixed.count('{') - content_fixed.count('}')
                open_brackets = content_fixed.count('[') - content_fixed.count(']')
                
                for _ in range(open_brackets):
                    content_fixed += ']'
                for _ in range(open_braces):
                    content_fixed += '}'
                
                content_fixed = re.sub(r',\s*}', '}', content_fixed)
                content_fixed = re.sub(r',\s*]', ']', content_fixed)
                
                result = json.loads(content_fixed)
                print(f"   [SUCCESS] JSON repare avec succes !")
                if "metadata" not in result: result["metadata"] = {}
                result["metadata"]["is_truncated"] = True
            except Exception as repair_err:
                print(f"   [ERROR] Echec de la reparation: {repair_err}")
                raise ValueError(
                    f"Impossible de parser la reponse de OpenAI pour '{sheet_name}'. "
                    f"Contenu: {content[:100]}..."
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
    
    # Tenter de convertir les colonnes numériques ou dates
    for col in df.columns:
        col_lower = str(col).lower()
        try:
            # Si le nom de la colonne suggère une date, tenter le parsing date d'abord
            if 'date' in col_lower or 'period' in col_lower or 'période' in col_lower:
                df[col] = pd.to_datetime(df[col], errors='ignore', dayfirst=True)
            
            # Sinon essayer numérique
            if not pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors='ignore')
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
    
    # Charger le classeur avec openpyxl pour vérifier la visibilité
    try:
        wb = openpyxl.load_workbook(BytesIO(file_content), read_only=True)
        hidden_sheets = [s.title for s in wb.worksheets if s.sheet_state != 'visible']
    except Exception as e:
        print(f"   [WARNING] Impossible de lire la visibilite des feuilles avec openpyxl: {e}")
        hidden_sheets = []

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
        # Sauter les feuilles exclues ou masquées
        if sheet_name in EXCLUDED_SHEET_NAMES or sheet_name.upper() in EXCLUDED_SHEET_NAMES:
            print(f"   [INFO] Feuille '{sheet_name}' ignoree (exclue)")
            continue
            
        if sheet_name in hidden_sheets:
            print(f"   [INFO] Feuille '{sheet_name}' ignoree (masquee)")
            continue

        # Limite de sécurité augmentée pour permettre l'accès aux feuilles cachées/lointaines
        if len(result['sheets']) >= 20:
            print(f"   [WARNING] Limite de 20 feuilles atteinte. Saut des feuilles restantes pour eviter le timeout.")
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
        
        print(f"   [INFO] Extraction OCR avec OpenAI Vision (avec fallback texte)...")
        # Préparer une version texte pour aider l'IA (fallback si l'image est refusée)
        df_text = df_original.to_csv(index=False, sep=';')
        
        # Extraire avec Vision API
        try:
            vision_data = extract_sheet_data_with_vision(image, client, model, sheet_name, df_text=df_text)
            
            # Convertir en DataFrame
            df_extracted = parse_vision_response_to_dataframe(vision_data)
            
            # --- VÉRIFICATION DE LA COMPLÉTUDE (ANTI-TRONCATURE) ---
            original_row_count = len(df_original)
            extracted_row_count = len(df_extracted)
            
            # Si l'IA a extrait moins de 80% des lignes pour un fichier significatif (> 50 lignes)
            # OU si le DataFrame est vide, on bascule sur les données originales
            if (original_row_count > 50 and extracted_row_count < (original_row_count * 0.8)) or df_extracted.empty:
                print(f"   [WARNING] Extraction potentiellement incomplete ({extracted_row_count}/{original_row_count} lignes).")
                print(f"   [INFO] Bascule sur les donnees Excel originales pour garantir l'exhaustivite.")
                df_extracted = df_original
                # On garde quand même le type détecté par l'IA si possible
                detected_type = vision_data.get('detected_type', 'UNKNOWN')
                confidence = vision_data.get('confidence', 0.5)
            else:
                detected_type = vision_data.get('detected_type', 'UNKNOWN')
                confidence = vision_data.get('confidence', 0.5)
                print(f"   [INFO] Extraction complete consideree valide ({extracted_row_count} lignes)")
        except Exception as e:
            print(f"   [ERROR] Echec critique extraction pour '{sheet_name}': {e}")
            print(f"   [INFO] Utilisation des donnees originales par défaut pour cette feuille")
            df_extracted = df_original
            vision_data = {} # Pour éviter les erreurs plus bas
            detected_type = 'UNKNOWN'
            confidence = 0.0
        
        # 🧹 NETTOYAGE AUTOMATIQUE DES DONNÉES
        print(f"   [INFO] Nettoyage automatique des donnees (feuille: {sheet_name})...")
        df_extracted = clean_dataframe(
            df_extracted, 
            context='financial',
            remove_totals=True,  # Demande utilisateur : supprimer les totaux ("Total : actifs courants")
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
            structured_data = structurer.process_dataframe(
                df_extracted, 
                columns_mapping, 
                company_metadata,
                pre_detected_type=detected_type,
                sheet_name=sheet_name
            )
            print(f"   [SUCCESS] Structuration reussie")
            
            # Mettre à jour detected_type si le structureur a trouvé quelque chose de mieux
            if structured_data and structured_data.get('type_document') and structured_data.get('type_document') != 'UNKNOWN':
                if detected_type == 'UNKNOWN':
                    print(f"   [INFO] Mise à jour du type: {detected_type} -> {structured_data.get('type_document')} (via structuration)")
                    detected_type = structured_data.get('type_document')
        except Exception as e:
            print(f"   [WARNING] Erreur structuration: {e}")
            structured_data = None
        
        # FILTRAGE FINAL: Seuls les types financiers pertinents sont conservés
        if detected_type not in ['BILAN', 'COMPTE_RESULTAT', 'JOURNAL']:
            print(f"   [INFO] Feuille '{sheet_name}' ignoree car type detecte ({detected_type}) non financier")
            continue

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
            'structured_data': structured_data,  # Données structurées
            'rows': df_extracted.to_dict(orient='records') # Full data for save_view
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
