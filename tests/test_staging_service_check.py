"""Exercises the read-only service boundary with fake HTTP responses only."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.oauth2 import service_account
import requests

from tests.staging_service_check import ServiceTransport, check_staging_services, main


class FakeResponse:
    def __init__(self, document=None, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]


class StagingServiceCheckTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(
            prefix="staging-service-fixture-",
            dir=os.environ.get("CORPO_VIEWER_TEST_DIRECTORY"),
        )
        self.addCleanup(self.storage.cleanup)
        self.directory = Path(self.storage.name)
        self.resources = self.directory / "staging-resources.json"
        self.credentials = self.directory / "dev-credentials.env"
        self.google_key = self.directory / "google-service-account.json"
        self.secret = "fixture-secret-never-print"
        self.bot_id = "111111111111111111"
        self.guild_id = "222222222222222222"
        self.channel_id = "333333333333333333"
        self.role_id = "444444444444444444"
        self.spreadsheet_id = "fixture_workbook_12345678901234567890"
        self.expected = {
            "discord_bot_id": self.bot_id,
            "discord_guild_id": self.guild_id,
            "discord_test_channel_id": self.channel_id,
            "google_spreadsheet_id": self.spreadsheet_id,
        }
        self.inventory = {
            **self.expected,
            "credentials_file": str(self.credentials),
            "google_service_account_file": str(self.google_key),
        }
        self.google_document = {
            "type": "service_account",
            "client_email": "fixture@fixture-project.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nRklYVFVSRU9OTFk=\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        self.resources.write_text(json.dumps(self.inventory), encoding="utf-8")
        self.credentials.write_text(f'BOT_TOKEN="{self.secret}"\nBOT_SECRET="{self.secret}"\n', encoding="utf-8")
        self.google_key.write_text(json.dumps(self.google_document), encoding="utf-8")
        self.arguments = [
            "--resources", str(self.resources),
            "--expected-bot-id", self.bot_id,
            "--expected-guild-id", self.guild_id,
            "--expected-channel-id", self.channel_id,
            "--expected-spreadsheet-id", self.spreadsheet_id,
            "--referee-role-id", self.role_id,
        ]
        self.responses = [
            FakeResponse({"id": self.bot_id, "bot": True}),
            FakeResponse({"id": self.bot_id, "flags": 1 << 15}),
            FakeResponse({"id": self.channel_id, "guild_id": self.guild_id, "type": 0}),
            FakeResponse([{"id": self.role_id, "managed": False}]),
            FakeResponse({"access_token": self.secret, "expires_in": 3600, "token_type": "Bearer"}),
            FakeResponse({"spreadsheetId": self.spreadsheet_id}),
            FakeResponse({
                "id": self.spreadsheet_id,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "trashed": False,
                "capabilities": {"canEdit": True, "canModifyContent": True},
            }),
        ]
        self.calls = []
        self.session = SimpleNamespace(trust_env=True, request=self.request, close=self.close)
        self.closed = False
        self.factory_values = []
        self.original_credentials_factory = service_account.Credentials.from_service_account_info
        session_patch = patch("tests.staging_service_check.requests.Session", return_value=self.session)
        credentials_patch = patch(
            "tests.staging_service_check.service_account.Credentials.from_service_account_info",
            side_effect=self.create_credentials,
        )
        session_patch.start()
        credentials_patch.start()
        self.addCleanup(session_patch.stop)
        self.addCleanup(credentials_patch.stop)

    def request(self, method, url, **keywords):
        self.calls.append((method, url, keywords))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True

    def create_credentials(self, document, scopes):
        self.factory_values.append((document, scopes))

        def refresh(transport):
            response = transport(
                "https://oauth2.googleapis.com/token", method="POST",
                body=b"fixture-assertion", headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            credential.token = json.loads(response.data)["access_token"]

        credential = SimpleNamespace(token=None, refresh=refresh)
        return credential

    def run_check(self, arguments=None):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = main(arguments if arguments is not None else self.arguments)
        text = output.getvalue()
        for private in (
            self.secret, str(self.directory), self.google_document["client_email"],
            self.bot_id, self.guild_id, self.channel_id, self.role_id, self.spreadsheet_id,
            "Traceback",
        ):
            self.assertNotIn(private, text)
        return result, text

    def test_success_only_reads_metadata_and_does_not_change_files(self):
        files = (self.resources, self.credentials, self.google_key)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
        result, output = self.run_check()
        self.assertEqual(result, 0)
        self.assertEqual(output.count("PASS:"), 8)
        self.assertIn("BOT_SECRET authenticity", output)
        self.assertIn("does not enable role authorization", output)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files})
        self.assertEqual(len(self.calls), 7)
        self.assertEqual([call[0] for call in self.calls], ["GET", "GET", "GET", "GET", "POST", "GET", "GET"])
        self.assertFalse(self.session.trust_env)
        self.assertTrue(self.closed)
        for method, url, keywords in self.calls:
            self.assertFalse(keywords["allow_redirects"])
            self.assertTrue(keywords["verify"])
            self.assertTrue(keywords["stream"])
            self.assertEqual(keywords["timeout"], (5, 15))
            if method == "POST":
                self.assertEqual(url, "https://oauth2.googleapis.com/token")
            else:
                self.assertIsNone(keywords["data"])
        self.assertEqual(self.calls[5][2]["params"], {"fields": "spreadsheetId"})
        self.assertEqual(self.calls[6][2]["params"], {"fields": "id,mimeType,trashed,capabilities(canEdit,canModifyContent)"})
        self.assertTrue(all(scope.endswith("readonly") for scope in self.factory_values[0][1]))

    def test_each_expected_mismatch_stops_before_credential_reads_and_network(self):
        for field in self.expected:
            expected = {**self.expected, field: "555555555555555555" if field.startswith("discord") else "other_workbook_12345678901234567890"}
            with self.subTest(field=field), patch("tests.staging_service_check.read_private_text") as read:
                report = check_staging_services(str(self.resources), expected, [self.role_id])
            self.assertIn("No credentials were read", report.issue)
            read.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_missing_expected_argument_does_not_read_inventory(self):
        with patch("tests.staging_service_check.load_inventory") as load:
            for arguments in ([], ["--resources", str(self.resources)], ["--bad", self.secret]):
                self.assertEqual(self.run_check(arguments)[0], 2)
            load.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_invalid_duplicate_or_everyone_referee_roles_stop_before_credentials(self):
        for roles in ([self.secret], [self.role_id, self.role_id], [self.guild_id]):
            with self.subTest(roles=roles), patch("tests.staging_service_check.read_private_text") as read:
                report = check_staging_services(str(self.resources), self.expected, roles)
            self.assertIsNotNone(report.issue)
            read.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_missing_credential_does_not_fall_back_to_environment(self):
        self.credentials.write_text('BOT_TOKEN=""\nBOT_SECRET=""', encoding="utf-8")
        with patch.dict(os.environ, {"BOT_TOKEN": self.secret, "BOT_SECRET": self.secret}):
            self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(self.calls, [])

    def test_bad_google_key_structure_stops_before_discord_request(self):
        self.google_document["token_uri"] = "https://example.invalid/" + self.secret
        self.google_key.write_text(json.dumps(self.google_document), encoding="utf-8")
        self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(self.calls, [])

    def test_wrong_bot_or_nonbot_identity_stops_all_downstream_requests(self):
        for identity in ({"id": self.role_id, "bot": True}, {"id": self.bot_id, "bot": False}, []):
            self.responses = [FakeResponse(identity)]
            self.calls.clear()
            self.assertEqual(self.run_check()[0], 2)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.factory_values, [])

    def test_wrong_application_or_missing_intent_stops_before_channel(self):
        for application in ({"id": self.role_id, "flags": 1 << 15}, {"id": self.bot_id, "flags": 0}, {"id": self.bot_id, "flags": True}):
            self.responses = [FakeResponse({"id": self.bot_id, "bot": True}), FakeResponse(application)]
            self.calls.clear()
            self.assertEqual(self.run_check()[0], 2)
            self.assertEqual(len(self.calls), 2)

    def test_new_string_flags_support_member_intent(self):
        self.responses[1] = FakeResponse({"id": self.bot_id, "flags_new": str(1 << 14), "flags": 0})
        self.assertEqual(self.run_check()[0], 0)

    def test_wrong_guild_or_nontext_channel_stops_before_roles(self):
        for change in ({"guild_id": self.role_id}, {"type": 11}, {"type": False}, {"id": self.role_id}):
            self.responses = [
                FakeResponse({"id": self.bot_id, "bot": True}),
                FakeResponse({"id": self.bot_id, "flags": 1 << 15}),
                FakeResponse({"id": self.channel_id, "guild_id": self.guild_id, "type": 0, **change}),
            ]
            self.calls.clear()
            self.assertEqual(self.run_check()[0], 2)
            self.assertEqual(len(self.calls), 3)

    def test_missing_or_integration_managed_role_stops_before_google(self):
        initial = self.responses[:3]
        for roles in ([], [{"id": self.role_id, "managed": True}], [{"id": self.role_id}], {"error": self.secret}):
            self.responses = [*initial, FakeResponse(roles)]
            self.calls.clear()
            self.assertEqual(self.run_check()[0], 2)
            self.assertEqual(len(self.calls), 4)
            self.assertEqual(self.factory_values, [])

    def test_google_auth_uses_minimal_fields_without_universe_or_delegation(self):
        self.google_document.update({
            "universe_domain": "example.invalid", "quota_project_id": self.secret,
            "trust_boundary": self.secret, "subject": self.secret,
        })
        self.google_key.write_text(json.dumps(self.google_document), encoding="utf-8")
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(set(self.factory_values[0][0]), {"type", "client_email", "private_key", "token_uri"})

    def test_google_key_parsing_exception_does_not_print_key_material(self):
        with patch(
            "tests.staging_service_check.service_account.Credentials.from_service_account_info",
            side_effect=ValueError(self.secret),
        ):
            result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("Google authentication failed", output)
        self.assertEqual(len(self.calls), 4)

    def test_real_google_auth_signs_only_for_the_fixed_mock_token_endpoint(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        fixture_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.google_document["private_key"] = fixture_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        self.google_key.write_text(json.dumps(self.google_document), encoding="utf-8")
        with patch(
            "tests.staging_service_check.service_account.Credentials.from_service_account_info",
            side_effect=self.original_credentials_factory,
        ):
            self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(self.calls[4][0:2], ("POST", "https://oauth2.googleapis.com/token"))
        self.assertIn(b"grant_type=urn", self.calls[4][2]["data"])
        self.assertEqual(len(self.calls), 7)

    def test_google_auth_failure_does_not_retry_or_read_spreadsheet(self):
        self.responses[4] = FakeResponse({"error": self.secret}, status=503)
        self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(len(self.calls), 5)

    def test_wrong_spreadsheet_identity_stops_before_drive(self):
        self.responses[5] = FakeResponse({"spreadsheetId": self.secret})
        self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(len(self.calls), 6)

    def test_drive_denied_edit_or_trashed_file_is_not_ready(self):
        initial = self.responses[:6]
        for change in (
            {"id": self.secret}, {"trashed": True}, {"mimeType": "text/plain"},
            {"capabilities": {"canEdit": False, "canModifyContent": True}},
            {"capabilities": {"canEdit": True, "canModifyContent": False}},
            {"capabilities": {}},
        ):
            self.responses = [*initial, FakeResponse({
                "id": self.spreadsheet_id, "mimeType": "application/vnd.google-apps.spreadsheet",
                "trashed": False, "capabilities": {"canEdit": True, "canModifyContent": True},
                **change,
            })]
            self.assertEqual(self.run_check()[0], 2)

    def test_status_errors_hide_response_bodies(self):
        for status in (401, 403, 404, 429, 500):
            self.responses = [FakeResponse({"message": self.secret}, status=status)]
            self.calls.clear()
            self.assertEqual(self.run_check()[0], 2)
            self.assertEqual(len(self.calls), 1)

    def test_redirect_is_not_followed_or_decoded(self):
        self.responses = [FakeResponse({"location": "https://example.invalid/" + self.secret}, status=302)]
        result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("no redirect was followed", output)
        self.assertEqual(len(self.calls), 1)

    def test_request_timeout_is_sanitized_and_closes_session(self):
        self.responses = [requests.Timeout(self.secret)]
        result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("timed out", output)
        self.assertTrue(self.closed)

    def test_response_size_is_bounded_and_elapsed_is_checked_between_chunks(self):
        self.responses = [FakeResponse(data=b"x" * 1048577)]
        self.assertEqual(self.run_check()[0], 2)
        self.responses = [FakeResponse({"id": self.bot_id, "bot": True})]
        with patch("tests.staging_service_check.time.monotonic", side_effect=[0, 31]):
            result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("size or time limit", output)

    def test_invalid_or_duplicate_json_is_sanitized(self):
        for contents in (self.secret.encode(), b'{"id":"a","id":"b"}', b"\xff"):
            self.responses = [FakeResponse(data=contents)]
            self.assertEqual(self.run_check()[0], 2)

    def test_transport_blocks_unexpected_host_method_and_google_auth_destination(self):
        transport = ServiceTransport(self.inventory)
        for method, url in (
            ("GET", "https://example.invalid/" + self.secret),
            ("POST", f"https://discord.com/api/v10/channels/{self.channel_id}"),
            ("GET", "http://discord.com/api/v10/users/@me"),
        ):
            with self.assertRaisesRegex(ValueError, "blocked"):
                transport.request(method, url)
        with self.assertRaisesRegex(ValueError, "blocked"):
            transport.google_auth_request("https://example.invalid/token", method="POST")
        self.assertEqual(self.calls, [])

    def test_transport_blocks_second_google_token_exchange(self):
        transport = ServiceTransport(self.inventory)
        self.responses = [FakeResponse({"access_token": self.secret})]
        transport.google_auth_request("https://oauth2.googleapis.com/token", method="POST")
        with self.assertRaisesRegex(ValueError, "first attempt"):
            transport.google_auth_request("https://oauth2.googleapis.com/token", method="POST")
        self.assertEqual(len(self.calls), 1)

    def test_result_retains_only_safe_labels(self):
        result = check_staging_services(str(self.resources), self.expected, [self.role_id])
        self.assertIsNone(result.issue)
        for value in (self.secret, self.bot_id, self.guild_id, self.channel_id, self.spreadsheet_id, self.google_document["client_email"]):
            self.assertNotIn(value, repr(result))
