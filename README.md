# REKAPY - Backend (Django)

REKAPY est le moteur de gestion comptable automatisée du projet REKAPY. Il utilise l'intelligence artificielle (OpenAI GPT-4o) et l'OCR (Tesseract) pour transformer des documents financiers en écritures comptables conformes au plan comptable malgache (PCG 2005).

## 🚀 Fonctionnalités Principales

- **Pipeline OCR & IA** : Extraction intelligente de données depuis PDF et images.
- **Moteur Comptable** : Génération automatique des journaux, grand livre, balance et états financiers (Bilan, Compte de Résultat).
- **Automates de Propagation** : Propagation en temps réel des écritures via des signaux Django.
- **Analyse Financière** : Calcul automatique des KPIs (CA, EBE, BFR, CAF, ROE, ROA).

## 🛠 Technologies

- **Framework** : Django 4.2+, Django REST Framework 3.14+
- **Base de Données** : PostgreSQL 13+
- **IA/OCR** : OpenAI (GPT-4o-mini), Tesseract OCR 5.0+, Poppler-utils
- **Traitement de fichiers** : PyPDF2, pdf2image, pandas

## ⚙️ Installation

### Prérequis
- Python 3.11+
- Tesseract OCR installé et dans le PATH
- Poppler-utils installé

### Étapes d'installation

1. **Naviguer vers le module backend** :
   ```bash
   cd vulca-backend/src
   ```

2. **Créer et activer un environnement virtuel** :
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/Mac:
   source .venv/bin/activate
   ```

3. **Installer les dépendances** :
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurer l'environnement** :
   Créer un fichier `.env` dans `vulca-backend/` (ou `vulca-backend/src/` selon votre configuration `settings.py`) :
   ```env
   DATABASE_URL=postgresql://user:password@localhost:5432/vulca_db
   OPENAI_API_KEY=votre-cle-openai
   SECRET_KEY=votre-cle-django
   DEBUG=True
   ```

5. **Appliquer les migrations** :
   ```bash
   python manage.py migrate
   ```

6. **Lancer le serveur** :
   ```bash
   python manage.py runserver
   ```

## 📡 API Endpoints (Extraits)

- `POST /api/files/` : Upload et traitement OCR.
- `GET /api/journals/` : Liste des écritures comptables.
- `GET /api/dashboard/indicators/` : KPIs financiers.
- `GET /api/bilans/` : États du bilan.

---
© 2026 VULCA Project
