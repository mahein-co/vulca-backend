import PyPDF2
from PIL import Image
import pytesseract
import pandas as pd

# Extract content from various file types for OCR processing
def extract_content(file, file_type):
    text = ""
    if file_type == "pdf":
        reader = PyPDF2.PdfReader(file)
        for page in reader.pages:
            text += page.extract_text() + "\n"

    elif file_type in ["png", "jpg", "jpeg"]:
        image = Image.open(file)
        text = pytesseract.image_to_string(image)

    elif file_type in ["xls", "xlsx"]:
        df = pd.read_excel(file)
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

    elif file_type == "csv":
        df = pd.read_csv(file)
        text = df.astype(str).agg(' '.join, axis=1).str.cat(sep='\n')

    return text

# Detect file type based on extension
def detect_file_type(file_name):
    ext = file_name.split(".")[-1].lower()
    if ext in ["pdf"]:
        return ext
    elif ext in ["png", "jpg", "jpeg"]:
        return ext
    elif ext in ["xls", "xlsx"]:
        return ext
    elif ext in ["csv"]:
        return ext
    else:
        return "unknown"