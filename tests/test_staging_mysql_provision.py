"""Verifies private MySQL provisioning with fake processes and driver connections."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from staging import provision_mysql


class FakeBootstrapConnection:
    """Records SQL without opening a database connection."""

    def __init__(self):
        self.statements = []
        self.closed = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def execute(self, statement, parameters=None):
        if parameters is not None:
            # Exercise mysqlclient's percent-substitution contract without
            # retaining a rendered statement containing private values.
            statement % tuple(repr(value) for value in parameters)
        self.statements.append((statement, parameters))

    def close(self):
        self.closed = True


class StagingMysqlProvisionTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(dir=os.environ["CORPO_VIEWER_TEST_DIRECTORY"])
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name) / "mysql"
        self.bot_id = "1550000000000000001"

    def create_harness(self):
        """Replaces every process, socket and native-driver boundary."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(provision_mysql, "validate_destination", return_value=self.root))
        reservation = stack.enter_context(patch.object(provision_mysql.socket, "socket")).return_value.__enter__.return_value
        initialize = stack.enter_context(patch.object(provision_mysql.subprocess, "run"))
        process = stack.enter_context(patch.object(provision_mysql.subprocess, "Popen")).return_value
        process.poll.return_value = None
        connection = FakeBootstrapConnection()
        connect = stack.enter_context(patch.object(provision_mysql, "connect_bootstrap", return_value=connection))
        return reservation, initialize, process, connection, connect

    def test_success_has_scoped_grants_private_files_and_stopped_owned_process(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        provision_mysql.provision_mysql(str(self.root), self.bot_id)
        reservation.bind.assert_called_once_with(("127.0.0.1", 3308))
        command = initialize.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/sbin/mysqld", "--no-defaults"])
        self.assertIn("--initialize-insecure", command)
        self.assertEqual(initialize.call_args.kwargs["env"]["HOME"], str(self.root))
        self.assertNotIn("BOT_TOKEN", initialize.call_args.kwargs["env"])
        connect.assert_called_once_with(process, self.root)
        self.assertTrue(connection.closed)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=20)
        process.kill.assert_not_called()
        credentials = json.loads((self.root / "credentials.json").read_text(encoding="utf-8"))
        self.assertEqual(credentials["expected_bot_id"], self.bot_id)
        self.assertEqual((credentials["host"], credentials["port"]), ("127.0.0.1", 3308))
        self.assertEqual(len({credentials[name] for name in ("root_password", "app_password", "validation_password")}), 3)
        grants = [(sql, values) for sql, values in connection.statements if sql.startswith("GRANT")]
        self.assertEqual(len(grants), 2)
        self.assertIn(credentials["database"].replace("_", "\\_"), grants[0][0])
        self.assertEqual(grants[0][1], [credentials["app_user"]])
        rendered_validation_grant = grants[1][0] % tuple(repr(value) for value in grants[1][1])
        self.assertIn(r"`corpo\_viewer\_validation\_%`.*", rendered_validation_grant)
        self.assertNotIn("%%", rendered_validation_grant)
        self.assertEqual(grants[1][1], [credentials["validation_user"]])
        for sql, parameters in connection.statements:
            self.assertNotIn("ON *.*", sql)
            self.assertNotIn("GRANT OPTION", sql)
            self.assertNotIn("DROP", sql)
            for name in ("root_password", "app_password", "validation_password"):
                self.assertNotIn(credentials[name], sql)
        self.assertEqual(connection.statements[-1][1], ["corpo-ch-web-staging-v1", self.bot_id])
        configuration = (self.root / "mysql.conf").read_text(encoding="utf-8")
        for option in ("bind-address=127.0.0.1", "port=3308", "mysqlx=0", "skip-name-resolve", "skip-log-bin", "partial-revokes=OFF"):
            self.assertIn(option, configuration)
        self.assertNotIn(credentials["root_password"], configuration)
        self.assertFalse((self.root / "credentials.pending.json").exists())
        if sys.platform == "linux":
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / "credentials.json").stat().st_mode & 0o777, 0o600)

    def test_occupied_port_refuses_before_directory_creation_or_process_start(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        reservation.bind.side_effect = OSError("fixture-private-error")
        with self.assertRaisesRegex(provision_mysql.ProvisionError, "port 3308"):
            provision_mysql.provision_mysql(str(self.root), self.bot_id)
        self.assertFalse(self.root.exists())
        initialize.assert_not_called()
        connect.assert_not_called()

    def test_initialization_failure_retains_partial_files_without_starting_server(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        initialize.side_effect = subprocess.CalledProcessError(1, "fixture-private-command")
        with self.assertRaises(subprocess.CalledProcessError):
            provision_mysql.provision_mysql(str(self.root), self.bot_id)
        self.assertTrue((self.root / "credentials.pending.json").is_file())
        self.assertFalse((self.root / "credentials.json").exists())
        connect.assert_not_called()
        process.terminate.assert_not_called()

    def test_startup_or_sql_failure_stops_only_owned_process_and_retains_partial_files(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        connect.side_effect = RuntimeError("fixture-private-error")
        with self.assertRaises(RuntimeError):
            provision_mysql.provision_mysql(str(self.root), self.bot_id)
        process.terminate.assert_called_once_with()
        self.assertTrue((self.root / "credentials.pending.json").exists())
        self.assertFalse((self.root / "credentials.json").exists())

    def test_sql_failure_closes_connection_and_stops_process_before_reporting_safe_error(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        connection.execute = Mock(side_effect=[None, None, RuntimeError("fixture-private-password")])
        output = io.StringIO()
        with redirect_stderr(output), redirect_stdout(output):
            status = provision_mysql.main(["--root", str(self.root), "--expected-bot-id", self.bot_id])
        self.assertEqual(status, 1)
        self.assertNotIn("fixture-private-password", output.getvalue())
        self.assertTrue(connection.closed)
        process.terminate.assert_called_once_with()
        self.assertFalse((self.root / "credentials.json").exists())
        pending = json.loads((self.root / "credentials.pending.json").read_text(encoding="utf-8"))
        self.assertEqual(connection.execute.call_args_list[0].args[1], [pending["root_password"]])
        self.assertIn(pending["database"], connection.execute.call_args_list[1].args[0])

    def test_slow_shutdown_uses_owned_handle_and_never_publishes_ready_credentials(self):
        reservation, initialize, process, connection, connect = self.create_harness()
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 20), 0]
        with self.assertRaisesRegex(provision_mysql.ProvisionError, "forced shutdown"):
            provision_mysql.provision_mysql(str(self.root), self.bot_id)
        process.kill.assert_called_once_with()
        self.assertFalse((self.root / "credentials.json").exists())

    def test_private_output_never_overwrites_an_existing_file(self):
        target = Path(self.storage.name) / "existing.json"
        target.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            provision_mysql.write_private_file(target, "replacement")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_bootstrap_connection_uses_only_the_owned_unix_socket(self):
        process = Mock()
        process.poll.return_value = None
        driver = Mock()
        with patch.object(provision_mysql.importlib, "import_module", return_value=driver):
            result = provision_mysql.connect_bootstrap(process, self.root)
        self.assertIs(result, driver.connect.return_value)
        options = driver.connect.call_args.kwargs
        self.assertEqual(options["unix_socket"], str(self.root / "bootstrap.sock"))
        self.assertEqual(options["host"], "localhost")
        self.assertEqual(options["user"], "root")
        self.assertNotIn("read_default_file", options)
        self.assertEqual(options["passwd"], "")

    @unittest.skipUnless(sys.platform == "linux", "Linux destination ownership checks.")
    def test_destination_refuses_existing_symlink_repository_root_and_bad_inputs(self):
        self.assertEqual(provision_mysql.validate_destination(str(self.root), self.bot_id), self.root)
        for value, bot_id in ((str(self.root), "invalid"), (str(self.root), str(2 ** 63)), (str(self.root), "15500000000000000001"), ("relative", self.bot_id), (str(self.root / ".." / "other"), self.bot_id), (str(self.root) + '"', self.bot_id), (str(Path(__file__).resolve().parents[1] / "private-mysql"), self.bot_id)):
            with self.subTest(value=value):
                with self.assertRaises(provision_mysql.ProvisionError):
                    provision_mysql.validate_destination(value, bot_id)
        self.root.mkdir()
        with self.assertRaisesRegex(provision_mysql.ProvisionError, "already exists"):
            provision_mysql.validate_destination(str(self.root), self.bot_id)
        link = Path(self.storage.name) / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(provision_mysql.ProvisionError, "symlinks"):
            provision_mysql.validate_destination(str(link / "mysql"), self.bot_id)
        with patch.object(provision_mysql.os, "getuid", return_value=0):
            with self.assertRaisesRegex(provision_mysql.ProvisionError, "normal Linux user"):
                provision_mysql.validate_destination(str(self.root), self.bot_id)

    @unittest.skipUnless(sys.platform == "linux", "Linux destination ownership checks.")
    def test_destination_refuses_parent_permissions_that_allow_other_users_to_write(self):
        parent = Path(self.storage.name)
        parent.chmod(0o777)
        try:
            with self.assertRaisesRegex(provision_mysql.ProvisionError, "not writable"):
                provision_mysql.validate_destination(str(self.root), self.bot_id)
        finally:
            parent.chmod(0o700)
