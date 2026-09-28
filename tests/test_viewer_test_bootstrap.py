"""Verifies the test boundary using operations that must fail before I/O."""

import importlib
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest


class ViewerTestBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get("CORPO_VIEWER_TEST_MODE"), "isolated")
        self.directory = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"])

    def test_direct_and_django_settings_use_the_same_isolated_module(self):
        settings = importlib.import_module("tests.viewer_test_settings")
        self.assertIs(sys.modules["corpoch.settings"], settings)
        self.assertEqual(settings.DATABASES["default"]["NAME"], ":memory:")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(settings.MEDIA_ROOT.parent, self.directory)
        self.assertNotIn("corpoch", sys.modules)

    def test_deployment_environment_is_not_inherited(self):
        for name in ("MYSQL_HOST", "MYSQL_PW", "BOT_SECRET", "CELERY_BROKER_URL"):
            self.assertNotIn(name, os.environ)
        self.assertEqual(os.environ["PYTHON_DOTENV_DISABLED"], "1")

    def test_dotenv_file_cannot_be_opened(self):
        with self.assertRaisesRegex(RuntimeError, "dotenv reads"):
            (self.directory / ".env").read_text(encoding="utf-8")

    def test_socket_connection_is_blocked_before_connecting(self):
        with socket.socket() as connection:
            with self.assertRaisesRegex(RuntimeError, "socket.connect"):
                connection.connect(("127.0.0.1", 9))

    def test_name_resolution_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "socket.getaddrinfo"):
            socket.getaddrinfo("viewer.invalid", 443)

    def test_subprocess_is_blocked_before_launch(self):
        with self.assertRaisesRegex(RuntimeError, "subprocess.Popen"):
            subprocess.run([sys.executable, "-c", "raise SystemExit(0)"], check=True)

    def test_native_database_and_application_imports_are_blocked(self):
        for name in ("MySQLdb", "pymysql", "corpoch", "corpoch.providers", "corpoch.tasks"):
            with self.subTest(module=name):
                with self.assertRaisesRegex(RuntimeError, "Blocked application/service import"):
                    importlib.import_module(name)

    def test_sqlite_cannot_open_a_repository_database(self):
        with self.assertRaisesRegex(RuntimeError, "outside test storage"):
            sqlite3.connect(Path.cwd() / "forbidden-viewer-test.sqlite3")

    def test_sqlite_memory_database_is_available(self):
        with sqlite3.connect(":memory:") as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone(), (1,))

    def test_storage_is_confined_to_temporary_directory(self):
        allowed = self.directory / "allowed.txt"
        allowed.write_text("fixture", encoding="utf-8")
        self.assertEqual(allowed.read_text(encoding="utf-8"), "fixture")
        allowed.unlink()
        with self.assertRaisesRegex(RuntimeError, "outside test storage"):
            (Path.cwd() / "forbidden-viewer-test.txt").write_text("blocked", encoding="utf-8")
