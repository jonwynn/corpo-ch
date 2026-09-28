"""Verifies private-input checks without credentials, application imports or services."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.staging_preflight import check_staging_inputs, main


class StagingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(
            prefix="staging-input-fixture-",
            dir=os.environ.get("CORPO_VIEWER_TEST_DIRECTORY"),
        )
        self.addCleanup(self.storage.cleanup)
        self.directory = Path(self.storage.name)
        self.resources = self.directory / "staging-resources.json"
        self.credentials = self.directory / "dev-credentials.env"
        self.google_key = self.directory / "google-service-account.json"
        self.secret = "fixture-private-value-never-print"
        self.inventory = {
            "purpose": "Fixture inventory only",
            "discord_bot_id": "111111111111111111",
            "discord_guild_id": "222222222222222222",
            "discord_test_channel_id": "333333333333333333",
            "google_spreadsheet_id": "fixture_workbook_12345678901234567890",
            "credentials_file": str(self.credentials),
            "google_service_account_file": str(self.google_key),
        }
        self.google_document = {
            "type": "service_account",
            "client_email": "fixture@fixture-project.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nRklYVFVSRU9OTFk=\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        self.write_json(self.resources, self.inventory)
        self.credentials.write_text(
            f'# Fixture credentials\nBOT_TOKEN="{self.secret}"\nBOT_SECRET=\'{self.secret}\'\n',
            encoding="utf-8-sig",
        )
        self.write_json(self.google_key, self.google_document)

    def write_json(self, path, document):
        path.write_text(json.dumps(document), encoding="utf-8-sig")

    def run_check(self, arguments=None):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = main(arguments if arguments is not None else ["--resources", str(self.resources)])
        text = output.getvalue()
        self.assertNotIn(self.secret, text)
        self.assertNotIn(str(self.directory), text)
        self.assertNotIn("Traceback", text)
        return result, text

    def test_valid_bom_files_report_structure_only_and_leave_files_unchanged(self):
        files = (self.resources, self.credentials, self.google_key)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
        result, text = self.run_check()
        self.assertEqual(result, 0)
        self.assertIn("PASS: Development resource inventory is valid.", text)
        self.assertIn("Local credential fields and Google key structure are present.", text)
        self.assertIn("does not verify credential authenticity", text)
        self.assertNotIn(self.google_document["client_email"], text)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files})

    def test_optional_purpose_is_not_used_as_configuration(self):
        del self.inventory["purpose"]
        self.write_json(self.resources, self.inventory)
        self.assertEqual(self.run_check()[0], 0)
        self.inventory["purpose"] = {"secret": self.secret}
        self.write_json(self.resources, self.inventory)
        self.assertEqual(self.run_check()[0], 0)

    def test_reports_both_missing_files_without_invalidating_inventory(self):
        self.credentials.unlink()
        self.google_key.unlink()
        result, text = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("inventory is valid", text)
        self.assertIn("dev-credentials.env is missing", text)
        self.assertIn("google-service-account.json is missing", text)
        self.assertNotIn("fields and Google key structure are present", text)

    def test_reports_each_blank_discord_field_and_all_missing_google_fields(self):
        self.credentials.write_text('BOT_TOKEN=""\nBOT_SECRET=""\n', encoding="utf-8")
        self.write_json(self.google_key, {})
        result, text = self.run_check()
        self.assertEqual(result, 2)
        for field in ("BOT_TOKEN", "BOT_SECRET", "service_account", "client_email", "private_key", "token_uri"):
            self.assertIn(field, text)
        self.assertEqual(text.count("NOT READY:"), 6)

    def test_missing_discord_assignment_is_not_filled_from_environment(self):
        self.credentials.write_text('BOT_TOKEN="fixture"\n', encoding="utf-8")
        with patch.dict(os.environ, {"BOT_SECRET": self.secret, "BOT_TOKEN": self.secret}):
            result, text = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("BOT_SECRET is missing or blank", text)

    def test_rejects_unknown_duplicate_unquoted_and_interpolated_assignments(self):
        for contents in (
            'BOT_TOKEN="fixture"\nBOT_TOKEN="duplicate"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="fixture"\nBOT_SECRET="fixture"\nOTHER="value"',
            "BOT_TOKEN=unquoted\nBOT_SECRET='fixture'",
            'BOT_TOKEN="${PRIVATE_VALUE}"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="%PRIVATE_VALUE%"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="$(private-command)"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="fixture\\nvalue"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="fixture value"\nBOT_SECRET="fixture"',
            'BOT_TOKEN="fixture" # ambiguous inline comment\nBOT_SECRET="fixture"',
            f'BOT_TOKEN="{self.secret}\nBOT_SECRET="fixture"',
        ):
            with self.subTest(contents=contents):
                self.credentials.write_text(contents, encoding="utf-8")
                result, text = self.run_check()
                self.assertEqual(result, 2)
                self.assertIn("inventory is valid", text)

    def test_rejects_invalid_discord_identifiers_before_reading_credentials(self):
        for invalid in (111111111111111111, "0", "001111111111111111", "18446744073709551616", self.secret):
            with self.subTest(value=invalid):
                self.write_json(self.resources, {**self.inventory, "discord_bot_id": invalid})
                with patch("tests.staging_preflight.check_discord_credentials") as credentials:
                    result, text = self.run_check()
                self.assertEqual(result, 2)
                self.assertNotIn("inventory is valid", text)
                credentials.assert_not_called()

    def test_rejects_spreadsheet_urls_and_ambiguous_inventory_fields(self):
        documents = [
            {**self.inventory, "google_spreadsheet_id": "https://example.invalid/" + self.secret},
            {**self.inventory, "google_spreadsheet_id": ""},
            {**self.inventory, "extra": self.secret},
            {key: value for key, value in self.inventory.items() if key != "discord_guild_id"},
        ]
        for document in documents:
            with self.subTest(document=document):
                self.write_json(self.resources, document)
                self.assertEqual(self.run_check()[0], 2)

    def test_rejects_paths_outside_inventory_folder_or_wrong_filenames(self):
        for invalid in (
            str(self.directory.parent / "dev-credentials.env"),
            str(self.directory / "different.env"),
            "dev-credentials.env",
            str(self.directory / "child" / ".." / "dev-credentials.env"),
        ):
            with self.subTest(path=invalid):
                self.write_json(self.resources, {**self.inventory, "credentials_file": invalid})
                self.assertEqual(self.run_check()[0], 2)

    def test_repository_inventory_is_rejected_before_read(self):
        repository_file = Path(__file__).resolve()
        with patch.object(Path, "open", side_effect=AssertionError("File must not be read")):
            result, text = self.run_check(["--resources", str(repository_file)])
        self.assertEqual(result, 2)
        self.assertIn("outside the repository", text)

    def test_network_and_device_paths_are_rejected_without_file_inspection(self):
        if os.name != "nt":
            self.skipTest("Windows drive paths require the Windows path implementation.")
        for path in (
            "\\\\fixture-private-value-never-print.invalid\\share\\staging-resources.json",
            "\\\\?\\C:\\staging-resources.json",
        ):
            with self.subTest(path=path), patch.object(Path, "lstat") as inspect:
                result, text = self.run_check(["--resources", path])
            self.assertEqual(result, 2)
            self.assertIn("not UNC or device paths", text)
            inspect.assert_not_called()
        with (
            patch("tests.staging_preflight.ctypes.windll.kernel32.GetDriveTypeW", return_value=4),
            patch.object(Path, "lstat") as inspect,
        ):
            result, text = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("not mapped network storage", text)
        inspect.assert_not_called()

    def test_rejects_missing_inventory_relative_paths_and_directory_targets(self):
        for path in (self.directory / "absent.json", Path("staging-resources.json"), self.directory):
            with self.subTest(path=path):
                result, text = self.run_check(["--resources", str(path)])
                self.assertEqual(result, 2)
                self.assertNotIn("inventory is valid", text)
        self.credentials.unlink()
        self.credentials.mkdir()
        self.assertEqual(self.run_check()[0], 2)

    def test_rejects_symlink_reparse_and_hardlink_inputs_before_open(self):
        original_lstat = Path.lstat
        for target, change in (
            (self.resources, {"st_mode": stat.S_IFLNK}),
            (self.directory, {"st_file_attributes": 0x400}),
            (self.credentials, {"st_nlink": 2}),
        ):
            def inspect(path, *arguments, **keywords):
                information = original_lstat(path, *arguments, **keywords)
                if path != target:
                    return information
                return SimpleNamespace(
                    **{
                        "st_mode": information.st_mode,
                        "st_nlink": information.st_nlink,
                        "st_file_attributes": 0,
                        **change,
                    },
                )

            with self.subTest(target=target, change=change), patch.object(Path, "lstat", inspect):
                self.assertEqual(self.run_check()[0], 2)

    def test_rejects_file_replacement_between_inspection_and_read(self):
        information = self.resources.stat()
        replacement = SimpleNamespace(
            st_dev=information.st_dev, st_ino=information.st_ino + 1,
            st_mode=information.st_mode, st_nlink=1,
        )
        with patch("tests.staging_preflight.os.fstat", return_value=replacement):
            result, text = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("changed while being inspected", text)

    def test_malformed_duplicate_nonobject_and_oversize_json_hide_contents(self):
        for contents in (
            '{"purpose": "' + self.secret,
            '{"purpose": "first", "purpose": "' + self.secret + '"}',
            '["' + self.secret + '"]',
            '{"purpose": "' + self.secret + "x" * 65536 + '"}',
        ):
            with self.subTest(length=len(contents)):
                self.resources.write_text(contents, encoding="utf-8")
                self.assertEqual(self.run_check()[0], 2)

    def test_invalid_google_structure_or_endpoint_never_reports_ready(self):
        for update in (
            {"type": "authorized_user"},
            {"client_email": self.secret + "@example.invalid"},
            {"private_key": self.secret},
            {"token_uri": "https://" + self.secret + ".invalid/token"},
            {"token_uri": "https://oauth2.googleapis.com/token?extra=1"},
        ):
            with self.subTest(update=update):
                self.write_json(self.google_key, {**self.google_document, **update})
                result, text = self.run_check()
                self.assertEqual(result, 2)
                self.assertIn("inventory is valid", text)

    def test_corrupt_google_json_does_not_hide_discord_readiness_issues(self):
        self.credentials.write_text('BOT_TOKEN=""\n', encoding="utf-8")
        self.google_key.write_text('{"private_key": "' + self.secret, encoding="utf-8")
        result, text = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("BOT_TOKEN is missing or blank", text)
        self.assertIn("BOT_SECRET is missing or blank", text)
        self.assertIn("Google service-account file must contain valid JSON", text)

    def test_permission_and_unexpected_errors_never_print_exception_details(self):
        with patch.object(Path, "lstat", side_effect=PermissionError(self.secret)):
            self.assertEqual(self.run_check()[0], 2)
        with patch("tests.staging_preflight.check_staging_inputs", side_effect=RuntimeError(self.secret)):
            self.assertEqual(self.run_check()[0], 2)

    def test_invalid_arguments_hide_values_and_do_not_inspect_files(self):
        with patch("tests.staging_preflight.check_staging_inputs") as checker:
            for arguments in ([], ["--resources"], ["--unexpected", self.secret]):
                self.assertEqual(self.run_check(arguments)[0], 2)
            checker.assert_not_called()

    def test_report_does_not_retain_credentials_or_resource_identifiers(self):
        report = check_staging_inputs(str(self.resources))
        self.assertTrue(report.ready)
        self.assertNotIn(self.secret, repr(report))
        self.assertNotIn(self.inventory["discord_bot_id"], repr(report))
        self.assertEqual(report.issues, ())

    def test_preflight_does_not_open_dotenv_start_processes_or_contact_services(self):
        original_open = Path.open
        opened = []

        def open_file(path, *arguments, **keywords):
            opened.append(path)
            if path not in {self.resources, self.credentials, self.google_key}:
                self.fail("Unexpected file read")
            return original_open(path, *arguments, **keywords)

        with (
            patch.object(Path, "open", open_file),
            patch("socket.create_connection", side_effect=AssertionError("Network operation")),
            patch("socket.getaddrinfo", side_effect=AssertionError("Network operation")),
            patch("subprocess.Popen", side_effect=AssertionError("Process operation")),
        ):
            self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(opened, [self.resources, self.credentials, self.google_key])
