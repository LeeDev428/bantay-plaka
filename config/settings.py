import environ
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, True))
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-this-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # project apps
    'apps.accounts',
    'apps.residents',
    'apps.visitors',
    'apps.logs',
    'apps.detection',
    'apps.reports',
    # channels
    'channels',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': env('DB_NAME', default='bantay_plaka'),
        'USER': env('DB_USER', default='root'),
        'PASSWORD': env('DB_PASSWORD', default=''),
        'HOST': env('DB_HOST', default='127.0.0.1'),
        'PORT': env('DB_PORT', default='3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='grafrafraftorres28@gmail.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='czar eklw lofi lann').replace(' ', '')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)

# API key used by the ANPR engine script to authenticate POSTs to /detection/ingest/
# Set this in .env as ANPR_API_KEY=<your-secret-key>
ANPR_API_KEY = env('ANPR_API_KEY', default='')

# Optional direct live preview streams for dashboard camera panels
ENTRY_CAMERA_RTSP = env('ENTRY_CAMERA_RTSP', default='')
EXIT_CAMERA_RTSP = env('EXIT_CAMERA_RTSP', default='')

# Camera feed tuning (guard dashboard live panels)
CAMERA_FEED_MAX_WIDTH = max(480, min(1280, env.int('CAMERA_FEED_MAX_WIDTH', default=720)))
CAMERA_FEED_JPEG_QUALITY = max(50, min(95, env.int('CAMERA_FEED_JPEG_QUALITY', default=72)))
CAMERA_WORKER_FPS = max(8, min(30, env.int('CAMERA_WORKER_FPS', default=18)))
CAMERA_FRAME_FRESH_SECONDS = max(0.5, min(5.0, env.float('CAMERA_FRAME_FRESH_SECONDS', default=1.2)))

# Django Channels — in-memory for development
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'
