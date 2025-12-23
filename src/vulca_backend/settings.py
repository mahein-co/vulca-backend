import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
from datetime import timedelta

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ['*']
APPEND_SLASH = False

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'app',  # ← APPLICATION ACCOUNT USER
    'compta.apps.ComptaConfig',  # APPLICATION COMPTA
    "ocr.apps.OcrConfig",  # APPLICATION OCR
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'app.middleware.DisableCSRFMiddleware',  # ← CHANGÉ de 'projet.middleware' à 'app.middleware'
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'vulca_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # ← CORRIGÉ (utilisez Path au lieu de os.path.join)
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',  # ← AJOUTÉ
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'vulca_backend.wsgi.application'

# Configuration PostgreSQL Supabase
DATABASES = {
    # 'default': dj_database_url.config(
    #     default=os.getenv('DATABASE_URL'),
    #     conn_max_age=600,
    #     conn_health_checks=True,
    # ),

    # RENDER DATABASE CONNEXION
     "default": dj_database_url.parse(
        os.getenv("RENDER_DATABASE_URL"),
        conn_max_age=600,
    )
}
# DATABASES = {
    # 'default': {
        # 'ENGINE': 'django.db.backends.sqlite3',
        # 'NAME': BASE_DIR / "db.sqlite3",
    # }
# }

DATABASES['default']['OPTIONS'] = {
    'connect_timeout': 30,
    'sslmode': 'require',
}

# Augmenter aussi le timeout des sessions
DATABASES['default']['CONN_MAX_AGE'] = 600
# Modèle User personnalisé
AUTH_USER_MODEL = 'app.User'
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']

# Configuration REST Framework avec JWT
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

# Configuration JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
}

# CORS Configuration
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# CSRF Configuration
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://vulca-frontend.onrender.com",
    "https://vulca-backend.onrender.com",
    'http://localhost:8000', 
    'http://127.0.0.1:8000'
]

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
    "https://vulca-frontend.onrender.com",
    "https://vulca-backend.onrender.com",
    'http://localhost:8000', 
    'http://127.0.0.1:8000'
]

# Désactiver la redirection vers /login/
LOGIN_URL = None

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Indian/Antananarivo'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # ← Ajouter cette ligne

# Pour le développement uniquement
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# OPENAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o')

# Configuration des fichiers médias (Uploads)
# MEDIA_URL = '/media/'
# MEDIA_ROOT = BASE_DIR / 'media'



