"""Test-only Django configuration, loaded by the guarded bootstrap."""

import os
from pathlib import Path


if os.environ.get("CORPO_VIEWER_TEST_MODE") != "isolated":
    raise RuntimeError("Use python -m tests.viewer_test_bootstrap.")

# Django requires these public setting names.
BASE_DIR = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"])
BASE_URL = "viewer.invalid"
ALLOWED_HOSTS = ["testserver", "viewer.invalid"]
SECRET_KEY = "isolated-viewer-test-key-not-for-deployment"
SALT_KEY = "isolated-viewer-test-salt-not-for-deployment"
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    },
}
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "polymorphic",
    "corpoch",
    "corpoch.dbot",
]
MIDDLEWARE = []
AUTH_USER_MODEL = "corpoch.DiscordUser"
ROOT_URLCONF = "tests.viewer_test_urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/test-media/"
STATIC_ROOT = BASE_DIR / "static"
STATIC_URL = "/test-static/"
PROJECT_HOME = str(BASE_DIR)
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_IGNORE_RESULT = True
BOT_ID = "0"
BOT_SECRET = "isolated-viewer-test-value"
AUTH_URL_DISCORD = "https://viewer.invalid/disabled"
REDIRECT_URI = "https://viewer.invalid/disabled"
CHOPT_PATH = str(BASE_DIR / "disabled-chopt")
CHOPT_OUTPUT = str(BASE_DIR / "disabled-output")
CHSTEG_PATH = str(BASE_DIR / "disabled-steg")
CHOPT_URL = "https://viewer.invalid/disabled"
