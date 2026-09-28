"""Exercises private web configuration and boundaries without service connections."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from staging.configuration import (
    StagingConfigurationError, load_configuration, prepare_private_directories,
    read_unique_fields, validate_configuration,
)
from staging.runtime import (
    DiscardedRequestErrors, WebImportGuard, validate_database_boundary,
    validate_database_grants,
)
from staging.settings import build_web_settings


def create_configuration_values():
    """Returns illustrative configuration values with no real credentials."""
    return {
        "schema_version": 1,
        "database_name": "corpo_staging_fixture",
        "database_user": "corpo_stage_fixture",
        "database_password": "fixture-database-password-only",
        "database_port": 3308,
        "browser_port": 8766,
        "bot_id": "89001234567890123",
        "bot_secret": "fixture-oauth-client-secret",
        "secret_key": "fixture-session-key-" + "x" * 50,
        "salt_key": "fixture-encryption-salt-" + "y" * 32,
    }


class StagingConfigurationTests(unittest.TestCase):
    """Checks schema restrictions, fixed settings and sanitized diagnostics."""

    def setUp(self):
        self.values = create_configuration_values()
        self.root = Path(tempfile.gettempdir()) / "fixture-web-runtime"
        self.configuration = validate_configuration(self.values, self.root, self.values["bot_id"])

    def test_configuration_does_not_have_a_secret_representation(self):
        for name in ("database_password", "bot_secret", "secret_key", "salt_key"):
            self.assertNotIn(self.values[name], repr(self.configuration))

    def test_schema_rejects_unknown_fields_and_bot_or_google_credentials(self):
        for name in ("BOT_TOKEN", "google_service_account_file", "MYSQL_HOST", "debug"):
            with self.subTest(field=name), self.assertRaises(StagingConfigurationError):
                validate_configuration({**self.values, name: "unwanted"}, self.root, self.values["bot_id"])
        missing = dict(self.values)
        missing.pop("salt_key")
        with self.assertRaises(StagingConfigurationError):
            validate_configuration(missing, self.root, self.values["bot_id"])

    def test_database_scope_and_fixed_ports_cannot_be_overridden(self):
        for name, invalid in (
            ("database_name", "production"), ("database_name", "corpo_staging_%"),
            ("database_user", "root"), ("database_user", "corpo_stage_" + "x" * 17),
            ("database_port", 3306), ("database_port", "3308"),
            ("browser_port", 8000), ("schema_version", True),
        ):
            with self.subTest(field=name, value=invalid), self.assertRaises(StagingConfigurationError):
                validate_configuration({**self.values, name: invalid}, self.root, self.values["bot_id"])

    def test_independent_application_pin_must_match(self):
        for pin in ("89001234567890124", "0", " 89001234567890123", None):
            with self.subTest(pin=pin), self.assertRaises(StagingConfigurationError):
                validate_configuration(self.values, self.root, pin)

    def test_missing_or_shared_private_keys_are_rejected(self):
        for name, invalid in (
            ("secret_key", "short"), ("salt_key", "short"), ("database_password", "short"),
            ("bot_secret", ""), ("bot_secret", self.values["secret_key"]),
        ):
            with self.subTest(field=name), self.assertRaises(StagingConfigurationError):
                validate_configuration({**self.values, name: invalid}, self.root, self.values["bot_id"])

    def test_duplicate_json_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            json.loads('{"schema_version":1,"schema_version":2}', object_pairs_hook=read_unique_fields)

    def test_settings_ignore_ambient_deployment_values_and_keep_all_gates_off(self):
        poison = {
            "MYSQL_HOST": "production.invalid", "MYSQL_DB": "production",
            "BOT_TOKEN": "fixture-token-not-for-web", "BOT_SECRET": "wrong-client",
            "CELERY_BROKER_URL": "redis://production.invalid/0",
            "MATCH_VIEWER_ENABLED": "true", "DEBUG": "true",
        }
        with patch.dict(os.environ, poison):
            settings = build_web_settings(self.configuration)
        database = settings["DATABASES"]["default"]
        self.assertEqual(database["HOST"], "127.0.0.1")
        self.assertEqual(database["PORT"], 3308)
        self.assertEqual(database["NAME"], self.values["database_name"])
        self.assertEqual(database["USER"], self.values["database_user"])
        self.assertNotIn("read_default_file", database["OPTIONS"])
        self.assertEqual(settings["CELERY_BROKER_URL"], "memory://")
        self.assertEqual(settings["CELERY_BEAT_SCHEDULE"], {})
        for name in (
            "DEBUG", "CELERY_TASK_ALWAYS_EAGER", "DISCORD_PROFILE_SYNC_ENABLED",
            "MATCH_VIEWER_ENABLED", "MATCH_VIEWER_POLLING_ENABLED", "MATCH_VIEWER_MYSQL_VERIFIED",
        ):
            self.assertIs(settings[name], False)
        self.assertNotIn("BOT_TOKEN", settings)
        self.assertNotIn("django.contrib.admin", settings["INSTALLED_APPS"])
        self.assertNotIn("corpoch.api", settings["INSTALLED_APPS"])
        self.assertEqual(settings["ROOT_URLCONF"], "staging.urls")
        self.assertEqual(settings["ALLOWED_HOSTS"], ["127.0.0.1"])
        self.assertEqual(settings["SESSION_ENGINE"], "django.contrib.sessions.backends.db")

    def test_oauth_uses_the_exact_local_callback_and_separate_session_key(self):
        settings = build_web_settings(self.configuration)
        authorization = urlsplit(settings["AUTH_URL_DISCORD"])
        self.assertEqual((authorization.scheme, authorization.netloc), ("https", "discord.com"))
        self.assertEqual(parse_qs(authorization.query), {
            "client_id": [self.values["bot_id"]], "response_type": ["code"],
            "redirect_uri": ["http://127.0.0.1:8766/auth"], "scope": ["identify guilds"],
        })
        self.assertEqual(settings["REDIRECT_URI"], "http://127.0.0.1:8766/auth")
        self.assertNotEqual(settings["SECRET_KEY"], settings["BOT_SECRET"])
        self.assertEqual(settings["MEDIA_ROOT"], self.root / "media")

    def test_service_and_deployment_imports_are_blocked(self):
        guard = WebImportGuard()
        for module in (
            "corpoch.settings", "corpoch.providers", "corpoch.tasks",
            "corpoch.dbot.bot", "corpoch.dbot.launcher", "corpoch.dbot.settings",
            "corpoch.chdedi.management", "dotenv", "dotenv.main",
        ):
            with self.subTest(module=module), self.assertRaises(ImportError):
                guard.find_spec(module)
        for module in ("corpoch.models", "corpoch.views", "corpoch.dbot.tasks", "requests"):
            self.assertIsNone(guard.find_spec(module))

    def test_wsgi_diagnostics_are_discarded(self):
        sink = DiscardedRequestErrors()
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(sink.write("/auth?code=fixture-sensitive-code"), 33)
            sink.flush()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")

    def test_cli_does_not_run_unsupported_commands_or_print_private_exceptions(self):
        from staging.__main__ import main

        with patch("staging.__main__.run_command") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["--config", "/private/config.json", "--expected-bot-id", self.values["bot_id"], "run_dbot"])
            run.assert_not_called()
        with (
            patch("staging.__main__.load_configuration", side_effect=RuntimeError("private-value")),
            redirect_stderr(io.StringIO()) as errors,
        ):
            result = main(["--config", "/private/config.json", "--expected-bot-id", self.values["bot_id"], "check"])
        self.assertEqual(result, 1)
        self.assertNotIn("private-value", errors.getvalue())


class StagingDatabaseBoundaryTests(unittest.TestCase):
    """Checks database ownership and least-scope grants with a fake cursor."""

    def setUp(self):
        values = create_configuration_values()
        self.configuration = validate_configuration(values, Path("/private/staging"), values["bot_id"])
        self.grants = [
            ("GRANT USAGE ON *.* TO `corpo_stage_fixture`@`127.0.0.1`",),
            ("GRANT ALL PRIVILEGES ON `corpo\\_staging\\_fixture`.* TO `corpo_stage_fixture`@`127.0.0.1`",),
        ]
        self.connection = MagicMock(vendor="mysql")
        self.cursor = self.connection.cursor.return_value.__enter__.return_value = Mock()
        self.connection.cursor.return_value.__exit__ = Mock(return_value=False)
        self.cursor.fetchone.return_value = ("corpo_staging_fixture", "corpo_stage_fixture@127.0.0.1", 3308, 0)
        self.cursor.fetchall.side_effect = [self.grants, [(1, "corpo-ch-web-staging-v1", values["bot_id"])]]

    def test_scoped_database_and_marker_pass_without_a_write(self):
        validate_database_boundary(self.connection, self.configuration)
        self.assertEqual([call.args[0].split()[0] for call in self.cursor.execute.call_args_list], ["SELECT", "SHOW", "SELECT"])
        self.connection.close.assert_called_once_with()

    def test_wider_grants_and_grant_option_are_rejected(self):
        invalid_grants = [
            "GRANT ALL PRIVILEGES ON *.* TO `corpo_stage_fixture`@`127.0.0.1`",
            "GRANT ALL PRIVILEGES ON `other`.* TO `corpo_stage_fixture`@`127.0.0.1`",
            self.grants[1][0] + " WITH GRANT OPTION",
            "GRANT ALL PRIVILEGES ON `corpo_staging_fixture`.* TO `corpo_stage_fixture`@`127.0.0.1`",
        ]
        for invalid in invalid_grants:
            with self.subTest(grant=invalid), self.assertRaises(StagingConfigurationError):
                validate_database_grants([*self.grants, (invalid,)], self.configuration)

    def test_partial_revokes_uses_literal_database_names(self):
        literal = [(value[0].replace("\\_", "_"),) for value in self.grants]
        validate_database_grants(literal, self.configuration, literal_database_names=True)

    def test_wrong_database_identity_fails_before_marker_read(self):
        self.cursor.fetchone.return_value = ("production", "root@localhost", 3306, 0)
        with self.assertRaises(StagingConfigurationError):
            validate_database_boundary(self.connection, self.configuration)
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.close.assert_called_once_with()

    def test_wrong_marker_fails_and_connection_errors_do_not_expose_values(self):
        self.cursor.fetchall.side_effect = [self.grants, [(1, "other-instance", self.configuration.bot_id)]]
        with self.assertRaises(StagingConfigurationError):
            validate_database_boundary(self.connection, self.configuration)
        self.cursor.execute.side_effect = RuntimeError("private-database-detail")
        with self.assertRaises(StagingConfigurationError) as error:
            validate_database_boundary(self.connection, self.configuration)
        self.assertNotIn("private-database-detail", str(error.exception))
        self.assertEqual(self.connection.close.call_count, 2)


@unittest.skipUnless(sys.platform == "linux", "Requires native Linux ownership and mode checks.")
class StagingPrivateFileTests(unittest.TestCase):
    """Verifies native private-file modes, links and bounded reads on Linux."""

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(
            prefix="corpo-staging-config-", dir=os.environ["CORPO_VIEWER_TEST_DIRECTORY"],
        ))
        self.root = Path(self.directory)
        self.path = self.root / "web.json"
        self.values = create_configuration_values()
        self.path.write_text(json.dumps(self.values), encoding="utf-8")
        self.path.chmod(0o600)

    def test_owned_private_configuration_and_directories_are_accepted(self):
        configuration = load_configuration(str(self.path), self.values["bot_id"])
        prepare_private_directories(configuration)
        self.assertEqual(configuration.database_port, 3308)
        for name in ("media", "static", "tmp"):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o700)

    def test_public_file_and_directory_permissions_are_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaises(StagingConfigurationError):
            load_configuration(str(self.path), self.values["bot_id"])
        self.path.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(StagingConfigurationError):
            load_configuration(str(self.path), self.values["bot_id"])

    def test_symbolic_and_hard_links_are_rejected(self):
        symbolic = self.root / "symbolic.json"
        symbolic.symlink_to(self.path)
        with self.assertRaises(StagingConfigurationError):
            load_configuration(str(symbolic), self.values["bot_id"])
        linked = self.root / "linked.json"
        os.link(self.path, linked)
        with self.assertRaises(StagingConfigurationError):
            load_configuration(str(self.path), self.values["bot_id"])

    def test_oversized_and_ambiguous_json_are_rejected_without_content(self):
        for content in ("x" * 65537, '{"private-duplicate":"secret","private-duplicate":"secret"}'):
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(StagingConfigurationError) as error:
                load_configuration(str(self.path), self.values["bot_id"])
            self.assertNotIn("private-duplicate", str(error.exception))
            self.assertNotIn("secret", str(error.exception))

    def test_relative_paths_and_repository_paths_are_rejected(self):
        for path in ("web.json", str(Path(__file__).parents[1] / "private-web.json")):
            with self.subTest(path=path), self.assertRaises(StagingConfigurationError):
                load_configuration(path, self.values["bot_id"])
