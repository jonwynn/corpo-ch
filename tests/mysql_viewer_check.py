"""Runs opt-in viewer concurrency checks in one fresh local MySQL database."""

import argparse
from dataclasses import dataclass, field
import importlib
import ipaddress
import os
import re
import sys
import tempfile
import threading
from unittest.mock import patch
import uuid

from tests.viewer_test_bootstrap import BlockedTestOperation, ViewerTestEnvironment


class MysqlCheckError(RuntimeError):
    """A safe local-check failure that contains no connection credentials."""


@dataclass(frozen=True)
class MysqlCheckConfiguration:
    """Explicit local test connection values, never deployment configuration."""

    host: str
    port: int
    user: str = field(repr=False)
    password: str = field(repr=False)

    def connection_arguments(self):
        """Builds native-driver arguments without an option file or database name."""
        return {
            "host": self.host, "port": self.port, "user": self.user,
            "passwd": self.password, "charset": "utf8mb4", "autocommit": True,
            "connect_timeout": 10, "read_timeout": 15, "write_timeout": 15,
        }


def read_configuration(environment):
    """Validates only the explicit MYSQL_TEST connection variables.

    :param dict environment: Caller-supplied environment values"""
    host = environment.get("MYSQL_TEST_HOST", "127.0.0.1")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise MysqlCheckError("MYSQL_TEST_HOST must be a literal loopback IP address.") from error
    if not address.is_loopback:
        raise MysqlCheckError("MYSQL_TEST_HOST must be a literal loopback IP address.")
    try:
        port = int(environment.get("MYSQL_TEST_PORT", "3306"))
    except (TypeError, ValueError) as error:
        raise MysqlCheckError("MYSQL_TEST_PORT must be an integer from 1 to 65535.") from error
    if not 1 <= port <= 65535:
        raise MysqlCheckError("MYSQL_TEST_PORT must be an integer from 1 to 65535.")
    user = environment.get("MYSQL_TEST_USER")
    password = environment.get("MYSQL_TEST_PASSWORD")
    if not isinstance(user, str) or not user.strip() or not isinstance(password, str):
        raise MysqlCheckError("Set MYSQL_TEST_USER and MYSQL_TEST_PASSWORD for the local disposable database.")
    return MysqlCheckConfiguration(str(address), port, user, password)


class MysqlCheckEnvironment(ViewerTestEnvironment):
    """Retains service and deployment-file guards while allowing one local driver."""

    def __init__(self, directory, configuration, database_name):
        super().__init__(directory, allow_models=True)
        self.configuration = configuration
        self.database_name = database_name
        self.import_guard.blocked_modules = tuple(
            name for name in self.import_guard.blocked_modules if name != "MySQLdb"
        )

    def __enter__(self):
        super().__enter__()
        configuration = self.configuration
        self.settings_module.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql", "NAME": self.database_name,
                "HOST": configuration.host, "PORT": configuration.port,
                "USER": configuration.user, "PASSWORD": configuration.password,
                "CONN_MAX_AGE": 0,
                "OPTIONS": {
                    "charset": "utf8mb4", "isolation_level": "read committed",
                    "connect_timeout": 10, "read_timeout": 15, "write_timeout": 15,
                    "init_command": "SET SESSION innodb_lock_wait_timeout=5",
                },
                "TEST": {"NAME": self.database_name},
            },
        }
        self.settings_module.MATCH_VIEWER_ENABLED = False
        self.settings_module.MATCH_VIEWER_POLLING_ENABLED = False
        self.settings_module.MATCH_VIEWER_MYSQL_VERIFIED = False
        os.environ["CORPO_MYSQL_VIEWER_CHECK"] = self.database_name
        return self

    def check_operation(self, event, arguments):
        """Allows Python socket calls only for the selected loopback endpoint.

        The native MySQL client is additionally restricted by its explicit
        connection arguments; no deployment option files are supplied.

        :param str event: Python audit event
        :param tuple arguments: Audit event arguments"""
        if self.active and event in {"socket.connect", "socket.getaddrinfo"}:
            endpoint = arguments[1] if event == "socket.connect" else arguments[:2]
            if (
                isinstance(endpoint, tuple) and len(endpoint) >= 2
                and endpoint[0] == self.configuration.host
                and endpoint[1] == self.configuration.port
            ):
                return
            raise BlockedTestOperation("MySQL validation permits only its configured loopback endpoint.")
        super().check_operation(event, arguments)


class DisposableMysqlDatabase:
    """Owns only a database whose CREATE succeeded in this process."""

    def __init__(self, configuration, connect=None):
        self.configuration = configuration
        self.connect = connect
        self.name = f"corpo_viewer_validation_{uuid.uuid4().hex}"
        self.connection = None
        self.owned_name = None

    def __enter__(self):
        if re.fullmatch(r"corpo_viewer_validation_[0-9a-f]{32}", self.name) is None:
            raise MysqlCheckError("Invalid generated validation database name.")
        try:
            connect = self.connect or importlib.import_module("MySQLdb").connect
            self.connection = connect(**self.configuration.connection_arguments())
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s", [self.name])
                if cursor.fetchone() is not None:
                    raise MysqlCheckError("The generated database already exists; it will not be reused or removed.")
                cursor.execute(f"CREATE DATABASE `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                self.owned_name = self.name
        except BaseException:
            if self.owned_name is not None:
                self.__exit__(*sys.exc_info())
            elif self.connection is not None:
                self.connection.close()
            raise
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            if self.owned_name is not None:
                workers = [
                    worker for worker in threading.enumerate()
                    if worker.name.startswith("corpo-mysql-check-") and worker.is_alive()
                ]
                for worker in workers:
                    worker.join(timeout=20)
                if any(worker.is_alive() for worker in workers):
                    raise MysqlCheckError(f"A worker has not stopped. Database retained for local review: {self.owned_name}")
                try:
                    with self.connection.cursor() as cursor:
                        cursor.execute(f"DROP DATABASE `{self.owned_name}`")
                except Exception as error:
                    raise MysqlCheckError(f"Cleanup failed. Remove only this validation database after review: {self.owned_name}") from error
                self.owned_name = None
        finally:
            if self.connection is not None:
                self.connection.close()


def run_database_tests():
    """Migrates the owned schema and runs the fixed local concurrency suite."""
    import django
    from django.core.management import call_command
    from django.db import connections
    from django.test.runner import DiscoverRunner

    class OwnedDatabaseRunner(DiscoverRunner):
        """Uses only the database already created and owned by this command."""

        def setup_databases(self, **kwargs):
            call_command("migrate", database="default", interactive=False, verbosity=0)
            return None

        def teardown_databases(self, old_config, **kwargs):
            connections.close_all()

    try:
        if sys.platform == "win32":
            with patch("platform.system", return_value="Windows"):
                django.setup()
        else:
            django.setup()
        runner = OwnedDatabaseRunner(verbosity=2, interactive=False, parallel=0)
        return runner.run_tests(["tests.mysql_test_match_viewer"])
    finally:
        connections.close_all()


def main(arguments=None):
    """Requires explicit creation consent before reading connection values.

    :param list arguments: Optional command-line argument list
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-create-test-database", action="store_true")
    options = parser.parse_args(arguments)
    if not options.allow_create_test_database:
        parser.error("Pass --allow-create-test-database to create and remove one disposable local database.")
    try:
        configuration = read_configuration(os.environ)
        database = DisposableMysqlDatabase(configuration)
        with tempfile.TemporaryDirectory(prefix="corpo-mysql-viewer-check-") as directory:
            with MysqlCheckEnvironment(directory, configuration, database.name):
                with database:
                    print(f"Running isolated MySQL checks in {database.name}.")
                    failures = run_database_tests()
        print("The disposable database was removed. Deployment settings and viewer gates were not changed.")
        return 1 if failures else 0
    except MysqlCheckError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Local validation interrupted; owned-database cleanup was attempted.", file=sys.stderr)
        return 130
    except Exception as error:
        # Native driver errors can contain account details. Do not print their
        # text, arguments, traceback or the connection configuration.
        print(f"Local MySQL validation could not finish ({type(error).__name__}). Check the installed driver, local server and test-account permissions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
