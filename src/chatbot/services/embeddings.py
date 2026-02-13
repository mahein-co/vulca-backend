import pytesseract
import os
import shutil
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from django.utils.text import slugify
from django.conf import settings

from django.conf import settings
MEDIA_ROOT = settings.MEDIA_ROOT
# MODEL -------------------------------------------
from chatbot.models import DocumentPage
from dotenv import load_dotenv

load_dotenv()
# OPENAI -------------------------------------------
from openai import OpenAI

# OPENAI -------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

def chunk_text(text, max_length=1000, overlap=100):
    """
    Découpe un texte en chunks avec chevauchement.
    max_length = taille d'un chunk
    overlap = nombre de caractères qui se chevauchent entre deux chunks
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_length
        chunks.append(text[start:end])
        start += max_length - overlap
    return chunks


def generate_embedding(text:str) -> list:
    if not text or not text.strip():
        raise ValueError("Le texte pour l'embedding ne peut pas être vide")
    embedding_response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text.strip()
    )
    return embedding_response.data[0].embedding


def process_pdf(document):
    """
    Convertit un PDF en images PNG et les sauvegarde dans MEDIA_ROOT/pdf_pages/<slugified_pdf_name>
    """
    document_folder = slugify(document.title)
    folder_path = os.path.join(MEDIA_ROOT, "pages", document_folder)
    os.makedirs(folder_path, exist_ok=True)
    
    try:
        # Convert PDF into images
        images = convert_from_path(document.file_path.path)
        for i, img in enumerate(images, start=1):
            img_filename = f"page_{i}.png"
            img_path = os.path.join(folder_path, img_filename)
            img.save(img_path, "PNG")

            # OCR by Pytesseract
            text_ocred = pytesseract.image_to_string(img, lang="fra")
            
            # Chunking processing
            chunks = chunk_text(text=text_ocred)

            for chunk in chunks:
                # Embedding processing
                embedding = generate_embedding(text=chunk)

                # Save page
                DocumentPage.objects.create(
                    document=document,
                    page_number=i,
                    content=chunk, 
                    embedding=embedding
                )
    
    finally:
        # Remove temporary folder
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
    

def extract_text_from_pdf(document):
    document_folder = slugify(document.title)
    folder_path = os.path.join(MEDIA_ROOT, "pages", document_folder)
    os.makedirs(folder_path, exist_ok=True)

    pdf_file = document.file_path.path

    try:
        with open(pdf_file, 'rb') as pdf:
            reader = PdfReader(pdf)

            for i, page in enumerate(reader.pages, start=1):
                content = page.extract_text() or ""

                if not content.strip():
                    continue  # saute les pages vides

                # Découpage du texte en chunks
                chunks = chunk_text(text=content)

                for chunk in chunks:
                    # Génération d'embedding
                    embedding = generate_embedding(text=chunk)

                    # Sauvegarde dans la base
                    DocumentPage.objects.create(
                        document=document,
                        page_number=i,
                        content=chunk,
                        embedding=embedding
                    )

        print(f"Extraction terminée pour '{document.title}' ({len(reader.pages)} pages traitées)")

    except Exception as e:
        print(f"Erreur lors de l'extraction du PDF '{document.title}': {e}")

    
        
