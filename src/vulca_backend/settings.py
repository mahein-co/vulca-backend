import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
from datetime import timedelta
import smtplib 

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

# === FRONTEND URL ===
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
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
    # "pgvector",
    'app.apps.AppConfig',  # ← APPLICATION ACCOUNT USER
    'compta.apps.ComptaConfig',  # APPLICATION COMPTA
    "ocr.apps.OcrConfig",  # APPLICATION OCR
    # "chatbot.apps.ChatbotConfig",
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
    'keepalives': 1,
    'keepalives_idle': 30,
    'keepalives_interval': 10,
    'keepalives_count': 5,
}

# Augmenter aussi le timeout des sessions
DATABASES['default']['CONN_MAX_AGE'] = 600
# Modèle User personnalisé
AUTH_USER_MODEL = 'app.CustomUser'
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']

# Configuration REST Framework avec JWT
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'app.authentication.JWTAuthenticationFromCookie',
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

# === CORS & CSRF Configuration ===
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# Allow specific origins (for production, you can add regex patterns)
CORS_ALLOW_ALL_ORIGINS = False

# Regex patterns for allowed origins (for subdomains)
# This allows https://www.lexaiq.com, https://api.lexaiq.com, and https://lexaiq.com
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://(\w+\.)?lexaiq\.com$",  # Allows all subdomains of lexaiq.com
]

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# Explicit allowed origins
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",  # Local development
    "https://www.lexaiq.com",  # Production frontend
    "https://lexaiq.com",  # Production frontend (without www)
    "https://api.lexaiq.com",  # Production backend API
    'http://localhost:8000',  # Local backend
    'http://127.0.0.1:8000'  # Local backend alternative
]

# CSRF trusted origins
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",  # Local development
    "https://www.lexaiq.com",  # Production frontend
    "https://lexaiq.com",  # Production frontend (without www)
    "https://api.lexaiq.com",  # Production backend API
    'http://localhost:8000',  # Local backend
    'http://127.0.0.1:8000'  # Local backend alternative
]

# Security settings for cookies (important for production)
CSRF_COOKIE_SECURE = not DEBUG  # True in production, False in development
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG  # True in production, False in development
SESSION_COOKIE_SAMESITE = "None" if not DEBUG else "Lax"  # "None" for cross-origin in production

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
# STATICFILES_DIRS = [
#     BASE_DIR / 'static',
# ]

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# OPENAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o')

# Configuration des fichiers médias (Uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'



# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Email Configuration
if ENVIRONMENT == "production":
    EMAIL_BACKEND = "sendgrid_backend.SendgridBackend"
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
else:
    # Gmail SMTP for development
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER_GMAIL")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD_GMAIL")

DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "VulcaIA <yurihoussen@gmail.com>"
)


# SMTP

'''
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND")

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
# EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ["true", "1", "yes"]


EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL")

'''