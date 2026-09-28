"""Checks real model storage and the isolated Django bootstrap boundary."""

import ast
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


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

    def test_all_loaded_migrations_are_applied(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        self.assertTrue(any(app_label == "corpoch" for app_label, name in targets))
        self.assertTrue(any(app_label == "dbot" for app_label, name in targets))
        self.assertEqual(executor.migration_plan(targets), [])
        self.assertTrue(set(targets).issubset(executor.loader.applied_migrations))

    def test_current_gsheet_credentials_roundtrip_without_plaintext_storage(self):
        from django.db import connection
        from corpoch.models import GSheetAPI

        credentials = {
            "client_email": "fixture-account@viewer.invalid",
            "private_key": "fixture-private-value-not-a-real-key",
            "nested": {"scopes": ["fixture-read-scope", "fixture-write-scope"]},
        }
        record = GSheetAPI(api_key=credentials)
        record.save()
        stored_record = GSheetAPI.objects.get(pk=record.pk)
        self.assertEqual(stored_record.api_key, credentials)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_key FROM corpoch_gsheetapi WHERE id = %s",
                [record.pk],
            )
            stored_text = cursor.fetchone()[0]
        self.assertIsInstance(stored_text, str)
        encrypted_values = json.loads(stored_text)
        self.assertEqual(set(encrypted_values), set(credentials))
        for value in (
            credentials["client_email"], credentials["private_key"],
            *credentials["nested"]["scopes"],
        ):
            self.assertNotIn(value, stored_text)

    def test_empty_current_gsheet_credentials_roundtrip(self):
        from corpoch.models import GSheetAPI

        record = GSheetAPI(api_key={})
        record.save()
        self.assertEqual(GSheetAPI.objects.get(pk=record.pk).api_key, {})


@unittest.skipUnless(
    os.environ.get("CORPO_VIEWER_TEST_MODE") == "isolated-model",
    "Requires the explicit isolated model bootstrap.",
)
class ViewerModelBootstrapBoundaryTests(unittest.TestCase):
    def test_historical_encrypted_field_deconstructs_without_legacy_keys(self):
        from django.core.serializers.json import DjangoJSONEncoder

        with patch(
            "encrypted_json_fields.helpers.get_default_crypter",
            side_effect=AssertionError("Migration loading must not request encryption keys."),
        ):
            migration = importlib.import_module("corpoch.migrations.0001_initial")
            operation = next(
                operation for operation in migration.Migration.operations
                if getattr(operation, "name", None) == "GSheetAPI"
            )
            field = dict(operation.fields)["api_key"]
            name, path, args, kwargs = field.deconstruct()
            self.assertEqual(path, "encrypted_json_fields.fields.EncryptedJSONField")
            self.assertIsNone(kwargs["crypter"])
            self.assertIs(kwargs["encoder"], DjangoJSONEncoder)
            reconstructed_field = field.__class__(*args, **kwargs)
            self.assertEqual(reconstructed_field.deconstruct(), (name, path, args, kwargs))

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

    def test_application_consumers_do_not_import_deployment_settings_directly(self):
        application = Path(__file__).parents[1] / "corpoch"
        violations = []
        for source in application.rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    direct = node.module == "corpoch.settings" or (
                        node.module == "corpoch" and any(name.name == "settings" for name in node.names)
                    )
                else:
                    direct = isinstance(node, ast.Import) and any(
                        name.name == "corpoch.settings" for name in node.names
                    )
                if direct:
                    violations.append(f"{source.relative_to(application)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_match_embed_uses_active_settings_despite_a_different_direct_module(self):
        from django.conf import settings
        from django.test import override_settings
        from corpoch import settings as direct_settings
        from corpoch.models import match as match_models

        round_record = SimpleNamespace(
            steg={}, match="Fixture match", num=1,
            screenshot=SimpleNamespace(url="/fixture-media/result.png"),
            chart=SimpleNamespace(icon=SimpleNamespace(img=SimpleNamespace(url="/fixture-media/icon.png"))),
        )
        with override_settings(BASE_URL="alternate-viewer.invalid"):
            self.assertNotEqual(direct_settings.BASE_URL, settings.BASE_URL)
            for property_name, builder_name in (
                ("steg_embed", "build_stats_embed"),
                ("full_steg_embed", "build_full_stats_embed"),
            ):
                with self.subTest(property=property_name), patch.object(match_models, builder_name):
                    embed = getattr(match_models.MatchRoundAbstract, property_name).fget(round_record)
                    embed.set_thumbnail.assert_called_once_with(
                        url="https://alternate-viewer.invalid/fixture-media/result.png",
                    )
                    embed.set_footer.assert_called_once_with(
                        text=embed.footer.text,
                        icon_url="https://alternate-viewer.invalid/fixture-media/icon.png",
                    )

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
