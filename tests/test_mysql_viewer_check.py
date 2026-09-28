"""Checks MySQL opt-in, configuration and ownership without importing a driver."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.mysql_viewer_check import (
    DisposableMysqlDatabase, MysqlCheckConfiguration, MysqlCheckEnvironment,
    MysqlCheckError, main, read_configuration,
)


class FakeMysqlConnection:
    """Records schema ownership operations without network or database access."""

    def __init__(self, exists=False, fail_create=False, fail_drop=False):
        self.exists = exists
        self.fail_create = fail_create
        self.fail_drop = fail_drop
        self.statements = []
        self.closed = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def execute(self, sql, parameters=None):
        self.statements.append((sql, parameters))
        if (self.fail_create and sql.startswith("CREATE")) or (self.fail_drop and sql.startswith("DROP")):
            raise RuntimeError("fixture-driver-error-secret")

    def fetchone(self):
        return ("existing",) if self.exists else None

    def close(self):
        self.closed = True


class MysqlViewerCheckTests(unittest.TestCase):
    def setUp(self):
        self.configuration = MysqlCheckConfiguration("127.0.0.1", 3306, "fixture-user", "fixture-password")

    def create_database(self, **options):
        connection = FakeMysqlConnection(**options)
        arguments = []

        def connect(**values):
            arguments.append(values)
            return connection

        return DisposableMysqlDatabase(self.configuration, connect=connect), connection, arguments

    def test_no_creation_flag_refuses_before_credentials_or_driver_are_read(self):
        with patch("tests.mysql_viewer_check.read_configuration") as read, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main([])
        self.assertEqual(error.exception.code, 2)
        read.assert_not_called()

    def test_only_explicit_test_credentials_are_used_and_repr_omits_them(self):
        configuration = read_configuration({
            "MYSQL_TEST_USER": "test-user", "MYSQL_TEST_PASSWORD": "test-password",
            "MYSQL_HOST": "deployment.invalid", "MYSQL_USER": "deployment-user",
            "MYSQL_PW": "deployment-password", "MYSQL_TEST_DATABASE": "production",
        })
        self.assertEqual((configuration.host, configuration.port), ("127.0.0.1", 3306))
        self.assertNotIn("test-user", repr(configuration))
        self.assertNotIn("test-password", repr(configuration))
        self.assertNotIn("database", configuration.connection_arguments())
        self.assertNotIn("read_default_file", configuration.connection_arguments())

    def test_configuration_rejects_remote_hosts_and_missing_values(self):
        for update in (
            {"MYSQL_TEST_HOST": "server.invalid"}, {"MYSQL_TEST_HOST": "localhost"},
            {"MYSQL_TEST_HOST": "192.168.1.1"}, {"MYSQL_TEST_PORT": "0"},
            {"MYSQL_TEST_PORT": "65536"}, {"MYSQL_TEST_PORT": "abc"},
            {"MYSQL_TEST_USER": ""}, {"MYSQL_TEST_PASSWORD": None},
        ):
            with self.subTest(update=update), self.assertRaises(MysqlCheckError):
                read_configuration({"MYSQL_TEST_USER": "fixture", "MYSQL_TEST_PASSWORD": "", **update})
        self.assertEqual(read_configuration({
            "MYSQL_TEST_HOST": "::1", "MYSQL_TEST_USER": "fixture", "MYSQL_TEST_PASSWORD": "",
        }).host, "::1")

    def test_fresh_schema_is_owned_only_after_create_and_dropped_once(self):
        database, connection, arguments = self.create_database()
        with database:
            self.assertEqual(database.owned_name, database.name)
            self.assertRegex(database.name, r"^corpo_viewer_validation_[0-9a-f]{32}$")
        statements = [sql for sql, parameters in connection.statements]
        self.assertEqual(len(statements), 3)
        self.assertTrue(statements[1].startswith(f"CREATE DATABASE `{database.name}`"))
        self.assertEqual(statements[2], f"DROP DATABASE `{database.name}`")
        self.assertNotIn("IF NOT EXISTS", " ".join(statements))
        self.assertIsNone(database.owned_name)
        self.assertTrue(connection.closed)
        self.assertEqual(arguments, [self.configuration.connection_arguments()])

    def test_existing_schema_is_neither_reused_nor_removed(self):
        database, connection, arguments = self.create_database(exists=True)
        with self.assertRaisesRegex(MysqlCheckError, "already exists"):
            with database:
                self.fail("Existing schema must be rejected")
        self.assertIsNone(database.owned_name)
        self.assertEqual(len(connection.statements), 1)
        self.assertTrue(connection.closed)

    def test_failed_create_never_authorizes_drop(self):
        database, connection, arguments = self.create_database(fail_create=True)
        with self.assertRaises(RuntimeError):
            with database:
                self.fail("Failed CREATE cannot enter the test run")
        self.assertIsNone(database.owned_name)
        self.assertFalse(any(sql.startswith("DROP") for sql, parameters in connection.statements))
        self.assertTrue(connection.closed)

    def test_test_failure_still_removes_only_the_owned_schema(self):
        database, connection, arguments = self.create_database()
        with self.assertRaisesRegex(ValueError, "fixture failure"):
            with database:
                database.name = "operator_supplied_name"
                raise ValueError("fixture failure")
        statement = connection.statements[-1][0]
        self.assertTrue(statement.startswith("DROP DATABASE `corpo_viewer_validation_"))
        self.assertNotIn("operator_supplied_name", statement)

    def test_cleanup_failure_reports_owned_name_without_driver_error(self):
        database, connection, arguments = self.create_database(fail_drop=True)
        with self.assertRaises(MysqlCheckError) as error:
            with database:
                pass
        self.assertIn(database.name, str(error.exception))
        self.assertNotIn("fixture-driver-error-secret", str(error.exception))
        self.assertTrue(connection.closed)

    def test_running_worker_prevents_schema_removal(self):
        database, connection, arguments = self.create_database()
        joins = []
        worker = SimpleNamespace(name="corpo-mysql-check-stuck", is_alive=lambda: True, join=lambda timeout: joins.append(timeout))
        with patch("tests.mysql_viewer_check.threading.enumerate", return_value=[worker]):
            with self.assertRaisesRegex(MysqlCheckError, "Database retained"):
                with database:
                    pass
        self.assertEqual(joins, [20])
        self.assertFalse(any(sql.startswith("DROP") for sql, parameters in connection.statements))

    def test_guard_keeps_service_and_other_database_imports_blocked(self):
        environment = MysqlCheckEnvironment(".", self.configuration, "unused-test-name")
        self.assertNotIn("MySQLdb", environment.import_guard.blocked_modules)
        for name in ("pymysql", "psycopg", "corpoch.providers", "corpoch.tasks", "corpoch.dbot.tasks"):
            self.assertIn(name, environment.import_guard.blocked_modules)

    def test_native_suite_rejects_direct_loading_before_application_imports(self):
        path = Path(__file__).with_name("mysql_test_match_viewer.py")
        source = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        imported = []
        normal_import = __import__

        def capture_import(name, *arguments, **keywords):
            imported.append(name)
            if name.startswith(("django", "corpoch")):
                self.fail("Native suite attempted application startup before explicit opt-in.")
            return normal_import(name, *arguments, **keywords)

        with (
            patch.dict(os.environ, {"CORPO_MYSQL_VIEWER_CHECK": ""}),
            patch("builtins.__import__", side_effect=capture_import),
        ):
            with self.assertRaisesRegex(RuntimeError, "explicit disposable-database opt-in"):
                exec(source, {"__name__": "isolated_mysql_import_check"})
        self.assertFalse(any(name.startswith(("django", "corpoch")) for name in imported))

    def test_test_settings_target_only_generated_database_with_gates_off(self):
        environment = MysqlCheckEnvironment(".", self.configuration, "corpo_viewer_validation_" + "a" * 32)
        environment.settings_module = SimpleNamespace()
        with (
            patch("tests.viewer_test_bootstrap.ViewerTestEnvironment.__enter__", return_value=environment),
            patch.dict(os.environ, {}, clear=False),
        ):
            environment.__enter__()
            database = environment.settings_module.DATABASES["default"]
            self.assertEqual(database["NAME"], environment.database_name)
            self.assertEqual(database["TEST"]["NAME"], environment.database_name)
            self.assertEqual(database["HOST"], self.configuration.host)
            self.assertFalse(environment.settings_module.MATCH_VIEWER_ENABLED)
            self.assertFalse(environment.settings_module.MATCH_VIEWER_POLLING_ENABLED)
            self.assertFalse(environment.settings_module.MATCH_VIEWER_MYSQL_VERIFIED)

    def test_startup_failure_does_not_print_native_driver_credentials(self):
        output = io.StringIO()
        with (
            patch("tests.mysql_viewer_check.read_configuration", side_effect=RuntimeError("fixture-password")),
            redirect_stdout(output), redirect_stderr(output),
        ):
            result = main(["--allow-create-test-database"])
        self.assertEqual(result, 2)
        self.assertNotIn("fixture-password", output.getvalue())
