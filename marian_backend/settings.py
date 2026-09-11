"""
Django settings for marian_backend project.
"""

import os
from pathlib import Path
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Auto-load .env configuration if present (without overwriting real environment variables)
env_path = BASE_DIR / '.env'
if env_path.exists():
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

from django.core.exceptions import ImproperlyConfigured

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-key-xc(hxu)1kad%7jrb!9s4_xq79cxq-@6nr@#35^i0z09n4h4p6%'
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY environment variable is required in production.")

# SECURITY: Dev bypass login controls (Strictly fail-closed; disabled unless DEBUG and ENABLE_DEV_BYPASS are explicitly set to true)
ENABLE_DEV_BYPASS = (
    DEBUG and
    os.environ.get("ENABLE_DEV_BYPASS", "False").lower() == "true"
)

if DEBUG:
    ALLOWED_HOSTS = [
        h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
        if h.strip()
    ]
else:
    _allowed_hosts = os.environ.get('DJANGO_ALLOWED_HOSTS')
    if not _allowed_hosts:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS environment variable is required in production.")
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(',') if h.strip()]
    if '*' in ALLOWED_HOSTS:
        raise ImproperlyConfigured("Wildcard '*' in DJANGO_ALLOWED_HOSTS is forbidden in production.")



# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'rest_framework',
    'corsheaders',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    
    # Local apps
    'users',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # CorsMiddleware must be top-level
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'users.middleware.Round1VerificationMiddleware',
]

ROOT_URLCONF = 'marian_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'marian_backend.wsgi.application'

# Database Setup (PostgreSQL strictly enforced in production; SQLite allowed in dev)
DB_ENGINE = os.environ.get('DATABASE_ENGINE', 'django.db.backends.sqlite3')

if not DEBUG and DB_ENGINE in ('django.db.backends.sqlite3', 'sqlite'):
    raise ImproperlyConfigured(
        "SQLite is strictly prohibited in production. "
        "Set DATABASE_ENGINE=django.db.backends.postgresql and provide DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD, and DATABASE_HOST."
    )

if DB_ENGINE in ('django.db.backends.sqlite3', 'sqlite'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    db_name = os.environ.get('DATABASE_NAME')
    db_user = os.environ.get('DATABASE_USER')
    db_password = os.environ.get('DATABASE_PASSWORD')
    db_host = os.environ.get('DATABASE_HOST', 'localhost')
    db_port = os.environ.get('DATABASE_PORT', '5432')
    db_conn_max_age = int(os.environ.get('DATABASE_CONN_MAX_AGE', '300'))

    if not DEBUG and (not db_password or not db_name or not db_user):
        raise ImproperlyConfigured(
            "Production database configuration incomplete. "
            "DATABASE_NAME, DATABASE_USER, and DATABASE_PASSWORD environment variables are strictly required in production."
        )

    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': db_name or 'marian_best_class',
            'USER': db_user or 'postgres',
            'PASSWORD': db_password,
            'HOST': db_host,
            'PORT': db_port,
            'CONN_MAX_AGE': db_conn_max_age,
        }
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
PRIVATE_MEDIA_ROOT = os.path.join(BASE_DIR, 'private_media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS Settings (Restricted to authorized origins)
_cors_origins = os.environ.get('CORS_ALLOWED_ORIGINS')
if not DEBUG and not _cors_origins:
    raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS environment variable is required in production.")
CORS_ALLOWED_ORIGINS = [
    origin.strip() for origin in (_cors_origins or 'http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173').split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-role-context',
]

# CSRF Trusted Origins (Required for HTTPS forms & admin in Django 4+)
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS')
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in _csrf_origins.split(',') if origin.strip()]
elif not DEBUG:
    # Safe derivation: mirror valid origins from CORS_ALLOWED_ORIGINS
    CSRF_TRUSTED_ORIGINS = [
        origin for origin in CORS_ALLOWED_ORIGINS
        if origin.startswith('http://') or origin.startswith('https://')
    ]
else:
    CSRF_TRUSTED_ORIGINS = [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:5173',
        'http://localhost:8000',
        'http://127.0.0.1:8000',
    ]

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLING_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLING_RATES': {
        'anon': '200/day',
        'user': '2000/day',
        'login': '100/minute' if DEBUG else '10/minute',
    }
}

# SimpleJWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60), # Short-lived access token for production security
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True, # Refresh token rotation enabled
    'BLACKLIST_AFTER_ROTATION': True, # Blacklist old refresh token upon rotation
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUDIENCE': None,
    'ISSUER': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# Google OAuth Client ID loaded from environment variable
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
if not DEBUG and not GOOGLE_CLIENT_ID:
    raise ImproperlyConfigured("GOOGLE_CLIENT_ID environment variable is required in production.")

# Production Security Defaults (Fail-Safe HTTPS, HSTS, Secure Cookies, Security Headers)
if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() == 'true'
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', 31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Logging Configuration with Sensitive Data Sanitization
DJANGO_LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'sensitive_data_filter': {
            '()': 'users.audit.SensitiveDataFilter',
        },
    },
    'formatters': {
        'standard': {
            'format': '[%(asctime)s] %(levelname)s %(name)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['sensitive_data_filter'],
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': DJANGO_LOG_LEVEL,
    },
}

# Optional Sentry Error Monitoring Integration
SENTRY_DSN = os.environ.get('SENTRY_DSN')
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
            send_default_pii=False,
        )
    except ImportError:
        pass