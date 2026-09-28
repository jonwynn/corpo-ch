import os

from dotenv import load_dotenv
from celery.schedules import crontab
from pathlib import Path

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
BASE_URL = os.getenv("BASE_URL")
ALLOWED_HOSTS = [ BASE_URL ]

PROJECT_HOME = os.getenv("PROJECT_HOME")

CSRF_TRUSTED_ORIGINS=[f"https://{BASE_URL}"]
SALT_KEY = os.getenv("DB_CRYPT_KEY")
SECRET_KEY = os.getenv("BOT_SECRET")
AUTH_URL_DISCORD = os.getenv("AUTH_URL_DISCORD")

#LOGIN_URL = [ 'views:auth' ]

DEBUG = os.getenv("DEBUG", False)

# Each rollout gate requires an explicit opt-in.
MATCH_VIEWER_ENABLED = os.getenv("MATCH_VIEWER_ENABLED", "false") == "true"
MATCH_VIEWER_POLLING_ENABLED = os.getenv("MATCH_VIEWER_POLLING_ENABLED", "false") == "true"
MATCH_VIEWER_MYSQL_VERIFIED = os.getenv("MATCH_VIEWER_MYSQL_VERIFIED", "false") == "true"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")

# Application definition

INSTALLED_APPS = [
	'daphne',
	'django.contrib.admin',
	'django.contrib.auth',
	'polymorphic',
	'django.contrib.contenttypes',
	'django.contrib.sessions',
	'django.contrib.messages',
	'django.contrib.staticfiles',
	'django_celery_beat',
	'django_jsonform',
	'django_admin_logs',
	'rest_framework',
	'drf_yasg',
	'solo',
	'encrypted_fields',
	'redis',
	'corpoch',
	'corpoch.api',
	'corpoch.dbot',
	'corpoch.chdedi',
	'adminsortable2'
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

ROOT_URLCONF = 'corpoch.urls'

TEMPLATES = [
	{
		'BACKEND': 'django.template.backends.django.DjangoTemplates',
		'DIRS': [],
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

ASGI_APPLICATION = 'corpoch.asgi.application'

DATABASES = {
	"default": {
		"ENGINE": "django.db.backends.mysql",
		"NAME" : os.getenv("MYSQL_DB"),
		"USER" : os.getenv("MYSQL_USER"),
		"PASSWORD" : os.getenv("MYSQL_PW"),
		"HOST" : os.getenv("MYSQL_HOST"),
		"CONN_MAX_AGE" : None
	}
}

# settings.py
AUTH_USER_MODEL = 'corpoch.DiscordUser'

AUTHENTICATION_BACKENDS = (
		'corpoch.auth.DiscordBackend',
)

AUTH_PASSWORD_VALIDATORS = [
	{ 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
	{ 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
	{ 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
	{ 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

REST_FRAMEWORK = {
	'DEFAULT_AUTHENTICATION_CLASSES': [],
	'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticatedOrReadOnly'],
	'COERCE_BIGINT_TO_STRING': True,
}

# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/
LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = os.getenv("STATIC_URL")
STATIC_ROOT = os.getenv("STATIC_ROOT")
MEDIA_ROOT = os.getenv("MEDIA_ROOT")
MEDIA_URL = os.getenv("MEDIA_URL")

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Corpo Specific Settings

BOT_ID = os.getenv('BOT_ID')
BOT_SECRET = os.getenv("BOT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
CHOPT_PATH = os.getenv("CHOPT_PATH")
CHOPT_OUTPUT = os.getenv("CHOPT_OUTPUT")
CHSTEG_PATH = os.getenv("CHSTEG_PATH")
CHOPT_URL = os.getenv("CHOPT_URL")
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800 #50 MB
