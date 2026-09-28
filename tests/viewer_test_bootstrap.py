"""Runs contract tests without deployment settings or external I/O."""

import importlib
import importlib.abc
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


class BlockedTestOperation(RuntimeError):
    """Reports an operation outside the isolated test boundary."""


class BlockedTestImport(BlockedTestOperation, ImportError):
    """Allows optional dependency probes to recognize an unavailable import."""


class ViewerImportGuard(importlib.abc.MetaPathFinder):
    """Blocks service clients and requires explicit opt-in for application models."""

    def __init__(self, allow_models=False):
        self.blocked_modules = (
            "MySQLdb",
            "pymysql",
            "psycopg",
            "psycopg2",
        )
        if allow_models:
            self.blocked_modules += (
                "corpoch.providers",
                "corpoch.tasks",
                "corpoch.dbot.tasks",
            )
        else:
            self.blocked_modules += ("corpoch",)

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
            raise BlockedTestImport(f"Blocked application/service import: {fullname}")
        return None


class ViewerTestEnvironment:
    """Owns temporary storage and guards for one fresh test process."""

    def __init__(self, directory, allow_models=False):
        self.directory = Path(directory).resolve()
        self.active = False
        self.import_guard = ViewerImportGuard(allow_models=allow_models)
        self.mode = "isolated-model" if allow_models else "isolated"
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
                "CORPO_VIEWER_TEST_MODE": self.mode,
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

    def resolve_descriptor_path(self, descriptor, require_directory=False):
        """Resolves an open Linux descriptor without trusting a stale path.

        :param int descriptor: Open file or directory descriptor
        :param bool require_directory: Whether a directory descriptor is required
        :return: Current resolved filesystem path"""
        if sys.platform != "linux" or not isinstance(descriptor, int) or isinstance(descriptor, bool):
            raise BlockedTestOperation("Unsupported test storage descriptor.")
        try:
            descriptor_stat = os.fstat(descriptor)
            if require_directory and not stat.S_ISDIR(descriptor_stat.st_mode):
                raise BlockedTestOperation("Test storage descriptor is not a directory.")
            target = os.readlink(f"/proc/self/fd/{descriptor}")
            if target.endswith(" (deleted)") or not Path(target).is_absolute():
                raise BlockedTestOperation("Test storage descriptor has no current path.")
            resolved = Path(target).resolve(strict=True)
            path_stat = resolved.stat()
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                raise BlockedTestOperation("Test storage descriptor changed during inspection.")
        except (OSError, ValueError) as error:
            raise BlockedTestOperation("Invalid test storage descriptor.") from error
        self.validate_storage_path(resolved)
        return resolved

    def validate_storage_path(self, value, directory_descriptor=None):
        """
        Requires file mutations to remain in temporary test storage

        :param object value: File path from a Python audit event
        :param int directory_descriptor: Optional base for a relative path
        :return: Resolved path within temporary storage
        """
        if isinstance(value, int):
            return self.resolve_descriptor_path(value)
        path = Path(os.fsdecode(value))
        if not path.is_absolute() and directory_descriptor not in (None, -1):
            directory = self.resolve_descriptor_path(directory_descriptor, require_directory=True)
            path = directory / path
        resolved = path.resolve()
        if resolved != self.directory and self.directory not in resolved.parents:
            raise BlockedTestOperation("File mutation outside test storage.")
        return resolved

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
        elif event in {"os.remove", "os.rmdir"}:
            self.validate_storage_path(arguments[0], arguments[1])
        elif event in {"os.mkdir", "os.chmod"}:
            self.validate_storage_path(arguments[0], arguments[2])
        elif event == "os.utime":
            self.validate_storage_path(arguments[0], arguments[3])
        elif event in {"os.rename", "os.link"}:
            self.validate_storage_path(arguments[0], arguments[2])
            self.validate_storage_path(arguments[1], arguments[3])
        elif event == "os.symlink":
            destination = self.validate_storage_path(arguments[1], arguments[2])
            source = Path(os.fsdecode(arguments[0]))
            self.validate_storage_path(source if source.is_absolute() else destination.parent / source)


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
