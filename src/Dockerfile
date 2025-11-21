FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["gunicorn", "vulca_backend.wsgi:application", "-w", "2", "--threads", "4", "-k", "gthread", "--timeout", "120", "--bind", "0.0.0.0:${PORT}"]
