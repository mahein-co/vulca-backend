FROM python:3.11-slim

# Installer les dépendances PostgreSQL et Tesseract
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirement.txt

CMD ["gunicorn", "vulca_backend.wsgi:application", "-w", "2", "-k", "gthread", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:8000"]