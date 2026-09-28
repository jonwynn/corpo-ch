"""Builds independent settings without importing the deployment configuration."""

from pathlib import Path
from urllib.parse import urlencode


def build_web_settings(configuration):
    """
    Builds the fixed web-only configuration for the local staging launcher

    :param WebConfiguration configuration: Validated private configuration
    :return: Django settings mapping"""
    repository = Path(__file__).resolve().parents[1]
    origin = f"http://127.0.0.1:{configuration.browser_port}"
    redirect_uri = f"{origin}/auth"
    return {
        "BASE_DIR": repository,
        "BASE_URL": f"127.0.0.1:{configuration.browser_port}",
        "PROJECT_HOME": str(repository),
        "SECRET_KEY": configuration.secret_key,
        "SALT_KEY": configuration.salt_key,
        "DEBUG": False,
        "ALLOWED_HOSTS": ["127.0.0.1"],
        "CSRF_TRUSTED_ORIGINS": [origin],
        "SESSION_ENGINE": "django.contrib.sessions.backends.db",
        "SESSION_COOKIE_NAME": "corpo_staging_session",
        "CSRF_COOKIE_NAME": "corpo_staging_csrf",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": False,
        "CSRF_COOKIE_SECURE": False,
        "USE_TZ": True,
        "TIME_ZONE": "UTC",
        "LANGUAGE_CODE": "en-us",
        "DEFAULT_AUTO_FIELD": "django.db.models.BigAutoField",
        "AUTH_USER_MODEL": "corpoch.DiscordUser",
        "AUTHENTICATION_BACKENDS": ["corpoch.auth.DiscordBackend"],
        "INSTALLED_APPS": [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "polymorphic", "corpoch", "corpoch.dbot",
        ],
        "MIDDLEWARE": [
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        "ROOT_URLCONF": "staging.urls",
        "TEMPLATES": [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [repository / "staging" / "templates"],
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ]},
        }],
        "DATABASES": {"default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": configuration.database_name,
            "USER": configuration.database_user,
            "PASSWORD": configuration.database_password,
            "HOST": "127.0.0.1",
            "PORT": configuration.database_port,
            "CONN_MAX_AGE": 0,
            "OPTIONS": {"charset": "utf8mb4", "connect_timeout": 5, "read_timeout": 15, "write_timeout": 15},
        }},
        "CACHES": {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
        "STATIC_URL": "/static/",
        "STATIC_ROOT": configuration.runtime_root / "static",
        "MEDIA_URL": "/unserved-private-media/",
        "MEDIA_ROOT": configuration.runtime_root / "media",
        "BOT_ID": configuration.bot_id,
        "BOT_SECRET": configuration.bot_secret,
        "REDIRECT_URI": redirect_uri,
        "AUTH_URL_DISCORD": "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": configuration.bot_id, "response_type": "code",
            "redirect_uri": redirect_uri, "scope": "identify guilds",
        }),
        "DISCORD_PROFILE_SYNC_ENABLED": False,
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "CELERY_TASK_ALWAYS_EAGER": False,
        "CELERY_TASK_IGNORE_RESULT": True,
        "CELERY_BEAT_SCHEDULE": {},
        "MATCH_VIEWER_ENABLED": False,
        "MATCH_VIEWER_POLLING_ENABLED": False,
        "MATCH_VIEWER_MYSQL_VERIFIED": False,
        "LOGGING": {
            "version": 1, "disable_existing_loggers": True,
            "handlers": {"discard": {"class": "logging.NullHandler"}},
            "root": {"handlers": ["discard"], "level": "CRITICAL"},
            "loggers": {
                "django": {"handlers": ["discard"], "propagate": False},
                "django.server": {"handlers": ["discard"], "propagate": False},
                "django.request": {"handlers": ["discard"], "propagate": False},
                "django.security": {"handlers": ["discard"], "propagate": False},
            },
        },
    }
