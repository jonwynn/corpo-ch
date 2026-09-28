"""Verifies the test boundary using operations that must fail before I/O."""

import importlib
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tests.viewer_test_bootstrap import BlockedTestOperation, ViewerTestEnvironment


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

    def test_nested_temporary_directory_cleanup_stays_within_storage(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "fixture.txt").write_text("fixture", encoding="utf-8")
        self.assertFalse(root.exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor audit regression.")
    def test_directory_relative_mutations_and_cleanup_are_allowed_inside_storage(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.mkdir("nested", dir_fd=descriptor)
                (root / "fixture.txt").write_text("fixture", encoding="utf-8")
                os.chmod("fixture.txt", 0o600, dir_fd=descriptor)
                os.utime("fixture.txt", None, dir_fd=descriptor)
                os.rename("fixture.txt", "renamed.txt", src_dir_fd=descriptor, dst_dir_fd=descriptor)
                os.link("renamed.txt", "linked.txt", src_dir_fd=descriptor, dst_dir_fd=descriptor)
                os.symlink("renamed.txt", "symbolic.txt", dir_fd=descriptor)
                self.assertEqual((root / "symbolic.txt").read_text(encoding="utf-8"), "fixture")
                for name in ("symbolic.txt", "linked.txt", "renamed.txt"):
                    os.unlink(name, dir_fd=descriptor)
                os.rmdir("nested", dir_fd=descriptor)
            finally:
                os.close(descriptor)

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor audit regression.")
    def test_directory_relative_source_destination_and_parent_escapes_are_blocked(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            (root / "fixture.txt").write_text("fixture", encoding="utf-8")
            inside = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            outside = os.open(Path.cwd(), os.O_RDONLY | os.O_DIRECTORY)
            guard = ViewerTestEnvironment(root)
            try:
                for descriptor, value in ((outside, "forbidden.txt"), (inside, "../forbidden.txt")):
                    with self.subTest(descriptor=descriptor, value=value):
                        with self.assertRaisesRegex(BlockedTestOperation, "outside test storage"):
                            guard.validate_storage_path(value, descriptor)
                for operation in (os.rename, os.link):
                    with self.subTest(operation=operation.__name__, destination="outside"):
                        with self.assertRaisesRegex(RuntimeError, "outside test storage"):
                            operation("fixture.txt", "forbidden.txt", src_dir_fd=inside, dst_dir_fd=outside)
                    with self.subTest(operation=operation.__name__, source="outside"):
                        with self.assertRaisesRegex(RuntimeError, "outside test storage"):
                            operation("forbidden.txt", "destination.txt", src_dir_fd=outside, dst_dir_fd=inside)
                with self.assertRaisesRegex(RuntimeError, "outside test storage"):
                    os.symlink(str(Path.cwd() / "forbidden.txt"), "escape", dir_fd=inside)
                self.assertTrue((root / "fixture.txt").is_file())
                self.assertFalse((root / "escape").exists())
            finally:
                os.close(inside)
                os.close(outside)

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor audit regression.")
    def test_invalid_deleted_and_non_directory_descriptors_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            file = root / "fixture.txt"
            file.write_text("fixture", encoding="utf-8")
            directory = root / "removed"
            directory.mkdir()
            file_descriptor = os.open(file, os.O_RDONLY)
            directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            guard = ViewerTestEnvironment(root)
            try:
                with self.assertRaisesRegex(BlockedTestOperation, "not a directory"):
                    guard.validate_storage_path("child", file_descriptor)
                directory.rmdir()
                with self.assertRaisesRegex(BlockedTestOperation, "no current path"):
                    guard.validate_storage_path("child", directory_descriptor)
                with self.assertRaisesRegex(BlockedTestOperation, "Invalid test storage descriptor"):
                    guard.validate_storage_path("child", -2)
                self.assertEqual(guard.validate_storage_path(file, -2), file)
            finally:
                os.close(file_descriptor)
                os.close(directory_descriptor)

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor audit regression.")
    def test_open_file_descriptor_metadata_updates_remain_confined(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            file = root / "fixture.txt"
            file.write_text("fixture", encoding="utf-8")
            descriptor = os.open(file, os.O_RDONLY)
            outside = os.open(Path(__file__), os.O_RDONLY)
            try:
                os.fchmod(descriptor, 0o600)
                os.utime(descriptor, None)
                guard = ViewerTestEnvironment(root)
                with self.assertRaisesRegex(BlockedTestOperation, "outside test storage"):
                    guard.validate_storage_path(outside)
            finally:
                os.close(descriptor)
                os.close(outside)

    @unittest.skipUnless(sys.platform == "linux", "Linux descriptor audit regression.")
    def test_symlink_and_moved_directory_descriptors_cannot_escape_storage(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            inside = root / "inside"
            sibling = root / "sibling"
            inside.mkdir()
            sibling.mkdir()
            (inside / "escape").symlink_to(sibling, target_is_directory=True)
            child = inside / "child"
            child.mkdir()
            guard = ViewerTestEnvironment(inside)
            inside_descriptor = os.open(inside, os.O_RDONLY | os.O_DIRECTORY)
            child_descriptor = os.open(child, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaisesRegex(BlockedTestOperation, "outside test storage"):
                    guard.validate_storage_path("escape/fixture.txt", inside_descriptor)
                child.rename(sibling / "moved")
                with self.assertRaisesRegex(BlockedTestOperation, "outside test storage"):
                    guard.validate_storage_path("fixture.txt", child_descriptor)
            finally:
                os.close(inside_descriptor)
                os.close(child_descriptor)
