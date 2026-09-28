"""Runs contract tests without deployment settings or external I/O."""

import importlib
import importlib.abc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class BlockedTestOperation(RuntimeError):
    """Reports an operation outside the isolated test boundary."""


class ViewerImportGuard(importlib.abc.MetaPathFinder):
    """Keeps foundation tests independent of the application and native clients."""

    def __init__(self):
        self.blocked_modules = (
            "MySQLdb",
            "pymysql",
            "psycopg",
            "psycopg2",
            "corpoch",
        )

    def find_spec(self, fullname, path=None, target=None):
        """
        Blocks imports that could bypass the Python network guards

        :param str fullname: Requested module name
        :param object path: Import search path
        :param object target: Existing module during reload
        """
        if any(
            fullname == name or fullname.startswith(f"{name}.")
            for name in self.blocked_modules
        ):
            raise BlockedTestOperation(f"Blocked application/service import: {fullname}")
        return None


class ViewerTestEnvironment:
    """Owns temporary storage and guards for one fresh test process."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.active = False
        self.import_guard = ViewerImportGuard()
        self.environment_patch = None
        self.previous_bytecode_setting = sys.dont_write_bytecode
        self.settings_module = None

    def __enter__(self):
        loaded_modules = [
            name
            for name in sys.modules
            if name == "corpoch"
            or name.startswith("corpoch.")
            or any(
                name == blocked or name.startswith(f"{blocked}.")
                for blocked in self.import_guard.blocked_modules
            )
        ]
        if loaded_modules:
            raise BlockedTestOperation("Start the bootstrap in a fresh process.")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR"}
        }
        environment.update(
            {
                "CORPO_VIEWER_TEST_MODE": "isolated",
                "CORPO_VIEWER_TEST_DIRECTORY": str(self.directory),
                "DJANGO_SETTINGS_MODULE": "tests.viewer_test_settings",
                "PYTHON_DOTENV_DISABLED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TEMP": str(self.directory),
                "TMP": str(self.directory),
                "TMPDIR": str(self.directory),
            },
        )
        self.environment_patch = patch.dict(os.environ, environment, clear=True)
        self.environment_patch.start()
        sys.dont_write_bytecode = True
        self.active = True
        sys.addaudithook(self.check_operation)
        sys.meta_path.insert(0, self.import_guard)
        try:
            self.settings_module = importlib.import_module(
                "tests.viewer_test_settings",
            )
            # Legacy models import this module directly, bypassing Django's
            # DJANGO_SETTINGS_MODULE. Supply the same isolated settings there.
            sys.modules["corpoch.settings"] = self.settings_module
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.active = False
        if self.import_guard in sys.meta_path:
            sys.meta_path.remove(self.import_guard)
        for name in ("corpoch.settings", "tests.viewer_test_settings"):
            sys.modules.pop(name, None)
        sys.dont_write_bytecode = self.previous_bytecode_setting
        if self.environment_patch is not None:
            self.environment_patch.stop()

    def validate_storage_path(self, value):
        """
        Requires file mutations to remain in temporary test storage

        :param object value: File path from a Python audit event
        """
        if isinstance(value, int) or value is None:
            return
        resolved = Path(os.fsdecode(value)).resolve()
        if resolved != self.directory and self.directory not in resolved.parents:
            raise BlockedTestOperation("File mutation outside test storage.")

    def check_operation(self, event, arguments):
        """
        Rejects network, process, deployment-file and unsafe database operations

        :param str event: Python audit event name
        :param tuple arguments: Event arguments
        """
        if not self.active:
            return
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.startfile",
            "os.startfile/2",
        }:
            raise BlockedTestOperation(f"Blocked external operation: {event}")
        if event == "sqlite3.connect":
            database = os.fsdecode(arguments[0])
            if database != ":memory:":
                if database.startswith("file:"):
                    raise BlockedTestOperation("SQLite URI connections are blocked.")
                self.validate_storage_path(database)
        elif event == "open":
            path, mode, flags = arguments
            if not isinstance(path, int):
                if Path(os.fsdecode(path)).name.lower().startswith(".env"):
                    raise BlockedTestOperation("Deployment dotenv reads are blocked.")
                write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                if (flags or 0) & write_flags or (
                    isinstance(mode, str) and any(value in mode for value in "wax+")
                ):
                    self.validate_storage_path(path)
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"}:
            self.validate_storage_path(arguments[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.validate_storage_path(arguments[0])
            self.validate_storage_path(arguments[1])


def run_tests():
    """
    Runs the isolated unittest suite in a fresh process

    :return: Process exit status
    """
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="corpo-viewer-tests-") as directory:
        with ViewerTestEnvironment(directory):
            suite = unittest.defaultTestLoader.discover(
                str(repository / "tests"),
                pattern="test_*.py",
                top_level_dir=str(repository),
            )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(run_tests())
