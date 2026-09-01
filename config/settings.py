import environ
import os
from pathlib import Path
import pymysql

pymysql.install_as_MySQLdb()

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, True))
environ.Env.read_env(BASE_DIR / '.env')


def _first_env(*names: str, default: str = '') -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != '':
            return str(value).strip()
    return default


def _as_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}

SECRET_KEY = env('SECRET_KEY', default='django-insecure-change-this-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['127.0.0.1', 'localhost', '.up.railway.app'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

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
    'whitenoise.middleware.WhiteNoiseMiddleware',
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

_db_url = _first_env('DATABASE_URL', 'MYSQL_URL', 'MYSQL_PRIVATE_URL', default='')
if _db_url:
    _db_default = environ.Env.db_url_config(_db_url)
    _db_default.setdefault('ENGINE', 'django.db.backends.mysql')
    _db_default.setdefault('OPTIONS', {})
    _db_default['OPTIONS'].setdefault('charset', 'utf8mb4')
    DATABASES = {'default': _db_default}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': _first_env('DB_NAME', 'MYSQLDATABASE', 'MYSQL_DATABASE', default='bantay_plaka'),
            'USER': _first_env('DB_USER', 'MYSQLUSER', 'MYSQL_USER', default='root'),
            'PASSWORD': _first_env('DB_PASSWORD', 'MYSQLPASSWORD', 'MYSQL_PASSWORD', default=''),
            'HOST': _first_env('DB_HOST', 'MYSQLHOST', 'MYSQL_HOST', default='127.0.0.1'),
            'PORT': _first_env('DB_PORT', 'MYSQLPORT', 'MYSQL_PORT', default='3306'),
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
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
(MEDIA_ROOT / 'resident_ids').mkdir(parents=True, exist_ok=True)
(MEDIA_ROOT / 'snapshots').mkdir(parents=True, exist_ok=True)

CLOUDINARY_CLOUD_NAME = env('CLOUDINARY_CLOUD_NAME', default='').strip()
CLOUDINARY_API_KEY = env('CLOUDINARY_API_KEY', default='').strip()
CLOUDINARY_API_SECRET = env('CLOUDINARY_API_SECRET', default='').strip()

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    try:
        import cloudinary

        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
        DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    except Exception:
        pass

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='').replace(' ', '')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)

# Cloud/runtime settings (safe defaults for local development)
ANPR_DEVICE = (env('ANPR_DEVICE', default='auto') or 'auto').strip().lower()
ANPR_FRAME_SKIP = env.int('ANPR_FRAME_SKIP', default=2)
ANPR_RTSP_DRAIN_GRABS = env.int('ANPR_RTSP_DRAIN_GRABS', default=2)
ANPR_STREAM_PROFILE = (env('ANPR_STREAM_PROFILE', default='sub') or 'sub').strip().lower()
ANPR_HEARTBEAT_SECONDS = env.float('ANPR_HEARTBEAT_SECONDS', default=1.0)

CAMERA_STREAM_MAX_WIDTH = env.int('CAMERA_STREAM_MAX_WIDTH', default=720)
CAMERA_STREAM_JPEG_QUALITY = env.int('CAMERA_STREAM_JPEG_QUALITY', default=70)
CAMERA_STREAM_POLL_SLEEP_SECONDS = env.float('CAMERA_STREAM_POLL_SLEEP_SECONDS', default=0.03)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    # Keep health endpoints accessible to platform probes even when HTTPS redirect is enabled.
    SECURE_REDIRECT_EXEMPT = [r'^healthz/?$', r'^login/?$']
    SECURE_SSL_REDIRECT = _as_bool_env('SECURE_SSL_REDIRECT', default=False)

# API key used by the ANPR engine script to authenticate POSTs to /detection/ingest/
# Set this in .env as ANPR_API_KEY=<your-secret-key>
ANPR_API_KEY = env('ANPR_API_KEY', default='')

# Optional direct live preview streams for dashboard camera panels
ENTRY_CAMERA_RTSP = env('ENTRY_CAMERA_RTSP', default='')
EXIT_CAMERA_RTSP = env('EXIT_CAMERA_RTSP', default='')
CAMERA_PREVIEW_ENABLED = env.bool('CAMERA_PREVIEW_ENABLED', default=DEBUG)
ANPR_INGEST_URL = env('ANPR_INGEST_URL', default='').strip()

# Django Channels: Redis in cloud if REDIS_URL is configured; in-memory for local dev.
REDIS_URL = _first_env('REDIS_URL', 'REDIS_PUBLIC_URL', 'RAILWAY_REDIS_URL', default='')
if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [REDIS_URL],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'
