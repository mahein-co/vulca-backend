"""
Module OCR utilisant OpenAI Vision API
Remplace Tesseract pour une meilleure qualité d'extraction
"""

import io
import base64
import time
import pandas as pd
from PIL import Image
from PyPDF2 import PdfReader


def encode_image_to_base64(image: Image.Image) -> str:
    """
    Convertit une image PIL en base64 pour l'API OpenAI Vision
    """
    buffered = io.BytesIO()
    # Convertir en RGB si nécessaire
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(buffered, format="JPEG", quality=95)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def resize_image_if_needed(image: Image.Image, max_size: int = 2048) -> Image.Image:
    """
    Redimensionne l'image si elle dépasse la taille maximale
    pour optimiser les coûts API
    """
    width, height = image.size
    
    if width <= max_size and height <= max_size:
        return image
    
    # Calculer le ratio de redimensionnement
    ratio = min(max_size / width, max_size / height)
    new_width = int(width * ratio)
    new_height = int(height * ratio)
    
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def process_image_with_vision(file_bytes: bytes, client, model: str) -> str:
    """
    Traite une image avec OpenAI Vision API
    """
    try:
        # Charger l'image
        image = Image.open(io.BytesIO(file_bytes))
        print(f"[INFO] IMAGE MODE: {image.mode}, SIZE: {image.size}")
        
        # Redimensionner si nécessaire
        image = resize_image_if_needed(image)
        
        # Encoder en base64
        base64_image = encode_image_to_base64(image)
        
        # Appel à l'API Vision
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What text do you see in this image? Please provide all visible text, numbers, dates, and amounts exactly as they appear."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=2000,
            temperature=0
        )
        
        text = response.choices[0].message.content.strip()
        return text
        
    except Exception as e:
        print(f"[ERROR] OCR Vision API ERROR (Image): {e}")
        return ""


def process_pdf_with_vision(file_bytes: bytes, client, model: str) -> str:
    """
    Traite un PDF avec OpenAI Vision API
    Essaie d'abord l'extraction texte native, puis Vision si nécessaire
    """
    text = ""
    
    # ---- 1) Tentative extraction texte natif avec PyPDF2 ----
    try:
        file_stream = io.BytesIO(file_bytes)
        reader = PdfReader(file_stream)
        extracted_text = ""
        
        for page in reader.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    extracted_text += page_text + "\n"
            except:
                pass
        
        if extracted_text.strip():
            print("[SUCCESS] PDF texte natif extrait avec PyPDF2")
            return extracted_text
    except Exception as e:
        print(f"[WARNING] Extraction texte natif echouee: {e}")
    
    # ---- 2) Si pas de texte natif, utiliser Vision API ----
    print("[INFO] Utilisation de Vision API pour PDF scanne...")
    
    try:
        from pdf2image import convert_from_bytes
        import platform
        import os
        
        # Auto-détection Poppler pour Windows
        poppler_path = None
        if platform.system() == "Windows":
            base = os.path.expanduser("~")
            possible_poppler = os.path.join(base, "poppler", "Library", "bin")
            if os.path.isdir(possible_poppler):
                poppler_path = possible_poppler
        
        # Convertir PDF en images
        images = convert_from_bytes(file_bytes, poppler_path=poppler_path)
        
        # Traiter chaque page avec Vision API
        for i, img in enumerate(images):
            print(f"   [INFO] Traitement page {i+1}/{len(images)}...")
            
            # Redimensionner si nécessaire
            img = resize_image_if_needed(img)
            
            # Encoder en base64
            base64_image = encode_image_to_base64(img)
            
            # Appel Vision API
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "What text do you see in this document page? Please provide all visible text, numbers, dates, and amounts exactly as they appear."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=2000,
                temperature=0
            )
            
            page_text = response.choices[0].message.content.strip()
            text += page_text + "\n"
            
            # STOP intelligent (évite OCR inutile si déjà assez de texte)
            if len(text.strip()) > 1200:
                print("   [INFO] Arret anticipe (texte suffisant)")
                break
        
        return text
        
    except Exception as e:
        print(f"[ERROR] OCR Vision API ERROR (PDF): {e}")
        raise e


def extract_content_with_vision(file, file_type: str, client, model: str) -> str:
    """
    Fonction OCR principale utilisant OpenAI Vision API
    Remplace extract_content() de utils.py
    
    Args:
        file: Fichier uploadé (Django UploadedFile)
        file_type: Type de fichier (pdf, png, jpg, jpeg, xls, xlsx, csv)
        client: Instance OpenAI client
        model: Modèle OpenAI à utiliser (ex: "gpt-4o")
    
    Returns:
        str: Texte extrait du document
    """
    start_time = time.time()
    
    # Lire le fichier en bytes
    file_bytes = file.read()
    file.seek(0)  # Reset pour utilisation ultérieure si nécessaire
    
    text = ""
    
    # ---- PDF ----
    if file_type == "pdf":
        text = process_pdf_with_vision(file_bytes, client, model)
        elapsed = round(time.time() - start_time, 2)
        print(f" OCR Vision API (PDF): {elapsed} s")
        return clean_text_output(text)
    
    # ---- IMAGES ----
    elif file_type in ["png", "jpg", "jpeg", "webp"]:
        text = process_image_with_vision(file_bytes, client, model)
        elapsed = round(time.time() - start_time, 2)
        print(f" OCR Vision API (Image): {elapsed} s")
        return clean_text_output(text)
    
    # ---- EXCEL ----
    elif file_type in ["xls", "xlsx"]:
        try:
            df = pd.read_excel(io.BytesIO(file_bytes))
            text = df.to_csv(index=False, sep=';')
            elapsed = round(time.time() - start_time, 2)
            print(f" Excel extraction: {elapsed} s")
            return text
        except Exception as e:
            print(f"[ERROR] Excel read error: {e}")
            return ""
    
    # ---- CSV ----
    elif file_type == "csv":
        try:
            df = pd.read_csv(io.BytesIO(file_bytes))
            text = df.to_csv(index=False, sep=';')
            elapsed = round(time.time() - start_time, 2)
            print(f" CSV extraction: {elapsed} s")
            return text
        except Exception as e:
            print(f"[ERROR] CSV read error: {e}")
            return ""
    
    return clean_text_output(text)


def clean_text_output(text: str) -> str:
    """
    Nettoie le texte extrait (espaces multiples, lignes vides excessives)
    """
    if not text:
        return ""
    
    import re
    
    # Remplacer les espaces multiples par un seul
    text = re.sub(r'[ \t]+', ' ', text)
    # Limiter les sauts de ligne consécutifs à 2
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    return text.strip()
