"""Checks real model storage and the isolated Django bootstrap boundary."""

import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest


if os.environ.get("CORPO_VIEWER_TEST_MODE") == "isolated-model":
    from django.test import TestCase as ModelTestCase
else:
    ModelTestCase = unittest.TestCase


@unittest.skipUnless(
    os.environ.get("CORPO_VIEWER_TEST_MODE") == "isolated-model",
    "Requires the explicit isolated model bootstrap.",
)
class ViewerModelDatabaseTests(ModelTestCase):
    def test_models_use_disposable_sqlite_storage(self):
        from django.db import connection
        from corpoch.dbot.models import Guilds

        directory = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"])
        self.assertEqual(connection.vendor, "sqlite")
        self.assertEqual(Path(connection.settings_dict["NAME"]).parent, directory)
        guild = Guilds.objects.create(id=912345, name="Isolated test guild")
        self.assertEqual(Guilds.objects.get(pk=guild.pk).name, "Isolated test guild")
        guild.delete()


@unittest.skipUnless(
    os.environ.get("CORPO_VIEWER_TEST_MODE") == "isolated-model",
    "Requires the explicit isolated model bootstrap.",
)
class ViewerModelBootstrapBoundaryTests(unittest.TestCase):
    def test_django_registers_real_models_without_service_modules(self):
        from django.apps import apps
        from corpoch.models import Match

        self.assertTrue(apps.ready)
        self.assertIs(apps.get_model("corpoch", "Match"), Match)
        for name in ("corpoch.providers", "corpoch.tasks", "corpoch.dbot.tasks"):
            self.assertNotIn(name, sys.modules)

    def test_direct_and_django_settings_remain_isolated(self):
        from django.conf import settings
        from corpoch import settings as direct_settings

        self.assertEqual(settings.SECRET_KEY, direct_settings.SECRET_KEY)
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(settings.CELERY_BROKER_URL, "memory://")
        self.assertNotIn("MYSQL_HOST", os.environ)

    def test_services_and_native_database_imports_remain_blocked(self):
        for name in (
            "MySQLdb",
            "pymysql",
            "corpoch.providers",
            "corpoch.tasks",
            "corpoch.dbot.tasks",
        ):
            with self.subTest(module=name):
                with self.assertRaisesRegex(RuntimeError, "Blocked application/service import"):
                    importlib.import_module(name)

    def test_network_process_and_deployment_file_access_remain_blocked(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(RuntimeError, "socket.connect"):
                connection.connect(("127.0.0.1", 9))
        with self.assertRaisesRegex(RuntimeError, "subprocess.Popen"):
            subprocess.run([sys.executable, "-c", "raise SystemExit(0)"], check=True)
        directory = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"])
        with self.assertRaisesRegex(RuntimeError, "dotenv reads"):
            (directory / ".env").read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "outside test storage"):
            (Path.cwd() / "forbidden-model-test.txt").write_text("blocked", encoding="utf-8")
