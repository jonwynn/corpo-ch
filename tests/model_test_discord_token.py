"""Checks real Discord token transport and renewal with outbound requests mocked."""

import ast
import traceback
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from requests import ConnectionError, Timeout

from corpoch.models import DiscordToken, DiscordUser


class DiscordTokenTests(TestCase):
    """Exercises stored-token renewal without Discord or private credentials."""

    def setUp(self):
        self.session_type = self.enterContext(patch("corpoch.models.misc.Session"))
        self.session = self.session_type.return_value
        self.identity = {"id": "8901", "avatar": None, "global_name": "Fixture Player"}
        self.tokens = {
            "access_token": "fixture-new-access",
            "refresh_token": "fixture-new-refresh",
            "scope": "identify guilds",
            "expires_in": 3600,
        }
        self.session.post.return_value = self.create_response(self.tokens)
        self.session.get.return_value = self.create_response(self.identity)

    def create_response(self, payload, status=200):
        """
        Builds a controlled HTTP response

        :param object payload: Decoded JSON value
        :param int status: HTTP status code
        :return: Mock response"""
        return Mock(status_code=status, json=Mock(return_value=payload))

    def create_token(self, expired=False, user_id=8901):
        """
        Stores and initializes a token in the disposable database

        :param bool expired: Whether renewal is already required
        :param int user_id: Distinct fixture account identifier
        :return: Stored token with a mocked HTTP session"""
        user = DiscordUser.objects.create(id=user_id)
        token = DiscordToken.objects.create(
            user=user,
            access_token="fixture-old-access",
            refresh_token="fixture-old-refresh",
            expires=timezone.now() + timedelta(days=-1 if expired else 7),
        )
        token.login()
        return token

    def load_refresh_task(self):
        """
        Loads the scheduled production function without importing service clients

        :return: Task function with real token models and isolated logging"""
        source = Path(__file__).parents[1] / "corpoch" / "tasks.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "update_oauth_tokens"
        )
        namespace = {
            "app": SimpleNamespace(task=lambda function: function),
            "DiscordToken": DiscordToken,
            "close_old_connections": Mock(),
            "print": Mock(),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        return namespace["update_oauth_tokens"]

    def assert_sanitized_error(self, action):
        """
        Checks that errors and their displayed traceback omit upstream secrets

        :param callable action: Token operation expected to fail"""
        try:
            action()
        except DiscordToken.AuthError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn("fixture-sensitive-response", rendered)
            self.assertNotIn("fixture-old-access", str(error))
            self.assertNotIn("fixture-old-refresh", str(error))
            self.assertNotIn("fixture-code", str(error))
        else:
            self.fail("Expected a sanitized DiscordToken.AuthError.")

    def test_login_without_code_does_not_make_an_http_request(self):
        self.create_token()
        self.session.post.assert_not_called()
        self.session.get.assert_not_called()

    def test_authorization_exchange_preserves_grant_and_uses_bounded_http(self):
        token = DiscordToken()
        token.login("fixture-code")
        self.assertEqual(token.access_token, "fixture-new-access")
        call = self.session.post.call_args
        self.assertEqual(call.args[0], "https://discord.com/api/v10/oauth2/token")
        self.assertEqual(call.kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(call.kwargs["data"]["code"], "fixture-code")
        self.assertEqual(call.kwargs["timeout"], (5, 15))
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_fresh_token_reads_identity_and_guilds_without_refresh(self):
        token = self.create_token()
        guilds = [{"id": "8902", "name": "Fixture Guild", "icon": None}]
        self.session.get.side_effect = [
            self.create_response(self.identity), self.create_response(guilds),
        ]
        self.assertEqual(token.identity(), self.identity)
        self.assertEqual(token.guilds(), guilds)
        self.session.post.assert_not_called()
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], (5, 15))
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(
                call.kwargs["headers"], {"Authorization": "Bearer fixture-old-access"},
            )

    def test_expired_identity_refreshes_and_persists_once_before_reading(self):
        token = self.create_token(expired=True)
        self.session.get.side_effect = [
            self.create_response(self.identity), self.create_response([]),
        ]
        self.assertEqual(token.identity(), self.identity)
        self.assertEqual(token.guilds(), [])
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs["data"], {
            "grant_type": "refresh_token", "refresh_token": "fixture-old-refresh",
        })
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-new-access")
        self.assertEqual(token.refresh_token, "fixture-new-refresh")
        self.assertGreater(token.expires, timezone.now())
        for call in self.session.get.call_args_list:
            self.assertEqual(
                call.kwargs["headers"], {"Authorization": "Bearer fixture-new-access"},
            )

    def test_guilds_can_refresh_expired_token_without_identity_call(self):
        token = self.create_token(expired=True)
        self.session.get.return_value = self.create_response([])
        self.assertEqual(token.guilds(), [])
        self.session.post.assert_called_once()
        self.assertTrue(self.session.get.call_args.args[0].endswith("/users/@me/guilds"))

    def test_browser_does_not_use_scheduled_two_day_renewal_window(self):
        token = self.create_token()
        token.expires = timezone.now() + timedelta(hours=1)
        token.save(update_fields=["expires"])
        token.identity()
        self.session.post.assert_not_called()
        token.update_code()
        self.session.post.assert_called_once()

    def test_scheduled_refresh_keeps_fresh_token_unchanged(self):
        token = self.create_token()
        token.update_code()
        self.session.post.assert_not_called()

    def test_scheduled_task_does_not_rewrite_unchanged_fresh_credentials(self):
        self.create_token()
        with patch.object(DiscordToken, "save") as save:
            self.load_refresh_task()()
        save.assert_not_called()
        self.session.post.assert_not_called()

    def test_second_refresh_uses_rotated_refresh_token(self):
        token = self.create_token(expired=True)
        token.update_code()
        token.update_code()
        grants = [call.kwargs["data"] for call in self.session.post.call_args_list]
        self.assertEqual(grants[0]["refresh_token"], "fixture-old-refresh")
        self.assertEqual(grants[1]["refresh_token"], "fixture-new-refresh")

    def test_token_exchange_transport_errors_are_sanitized(self):
        for failure in (Timeout, ConnectionError):
            with self.subTest(failure=failure):
                self.session.post.side_effect = failure("fixture-sensitive-response")
                self.assert_sanitized_error(lambda: DiscordToken().login("fixture-code"))

    def test_identity_and_guild_transport_errors_are_sanitized(self):
        token = self.create_token()
        for method in (token.identity, token.guilds):
            for failure in (Timeout, ConnectionError):
                with self.subTest(method=method.__name__, failure=failure):
                    self.session.get.side_effect = failure("fixture-sensitive-response")
                    self.assert_sanitized_error(method)

    def test_rejected_exchange_does_not_echo_error_body(self):
        for status in (302, 400, 401, 429, 500):
            with self.subTest(status=status):
                response = self.create_response({"error": "fixture-sensitive-response"}, status)
                self.session.post.return_value = response
                self.assert_sanitized_error(lambda: DiscordToken().login("fixture-code"))
                if status != 400:
                    response.json.assert_not_called()

    def test_non_json_success_responses_are_sanitized(self):
        token = self.create_token()
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("fixture-sensitive-response")
        self.session.post.return_value = response
        self.session.get.return_value = response
        for method in (
            lambda: DiscordToken().login("fixture-code"), token.identity, token.guilds,
        ):
            with self.subTest(method=method):
                self.assert_sanitized_error(method)

    def test_invalid_token_payload_never_partially_replaces_stored_fields(self):
        token = self.create_token(expired=True)
        previous_expiry = token.expires
        invalid_payloads = [None, [], {}, "fixture-sensitive-response"]
        for field in ("access_token", "refresh_token", "scope", "expires_in"):
            payload = dict(self.tokens)
            payload.pop(field)
            invalid_payloads.append(payload)
        for field in ("access_token", "refresh_token", "scope"):
            for value in (None, "", " ", [], 123):
                invalid_payloads.append({**self.tokens, field: value})
        for seconds in (None, True, False, 0, -1, "3600", 1.5, 10 ** 30):
            invalid_payloads.append({**self.tokens, "expires_in": seconds})
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.session.post.return_value = self.create_response(payload)
                self.assert_sanitized_error(token.identity)
                self.assertEqual(token.access_token, "fixture-old-access")
                self.assertEqual(token.refresh_token, "fixture-old-refresh")
                self.assertEqual(token.expires, previous_expiry)
                self.session.get.assert_not_called()
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-old-access")
        self.assertEqual(token.expires, previous_expiry)

    def test_malformed_identity_is_rejected_before_consumers_use_it(self):
        token = self.create_token()
        for payload in (
            None, [], {}, {"id": None}, {"id": 8901}, {"id": ""}, {"id": "0"},
            {"id": "fixture-sensitive-response"}, {"id": str(2 ** 63)},
            {"id": "8901", "avatar": []}, {"id": "8901", "global_name": []},
            {"id": "8901", "username": []},
        ):
            with self.subTest(payload=payload):
                self.session.get.return_value = self.create_response(payload)
                self.assert_sanitized_error(token.identity)

    def test_malformed_guild_collection_is_rejected(self):
        token = self.create_token()
        for payload in (None, {}, "fixture-sensitive-response", [None], [{}], [{"id": "invalid"}]):
            with self.subTest(payload=payload):
                self.session.get.return_value = self.create_response(payload)
                self.assert_sanitized_error(token.guilds)

    def test_failed_refresh_does_not_try_stale_bearer_or_change_database(self):
        token = self.create_token(expired=True)
        self.session.post.return_value = self.create_response({"error": "invalid_grant"}, 400)
        self.assert_sanitized_error(token.identity)
        self.session.get.assert_not_called()
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-old-access")
        self.assertEqual(token.refresh_token, "fixture-old-refresh")

    def test_database_save_failure_is_not_misreported_as_discord_failure(self):
        token = self.create_token(expired=True)
        with patch.object(token, "save", side_effect=DatabaseError("fixture database error")):
            with self.assertRaisesRegex(DatabaseError, "fixture database error"):
                token.identity()
        self.session.get.assert_not_called()

    def test_scheduled_network_failure_retains_token_and_processes_next_account(self):
        first = self.create_token(expired=True)
        second = self.create_token(expired=True, user_id=8902)
        self.session.post.side_effect = [
            Timeout("fixture-sensitive-response"), self.create_response(self.tokens),
        ]
        task = self.load_refresh_task()
        task()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.access_token, "fixture-old-access")
        self.assertEqual(second.access_token, "fixture-new-access")
        self.assertEqual(DiscordToken.objects.count(), 2)

    def test_scheduled_malformed_config_and_server_failures_never_delete_credentials(self):
        token = self.create_token(expired=True)
        malformed = Mock(status_code=400)
        malformed.json.side_effect = ValueError("fixture-sensitive-response")
        responses = [
            malformed, self.create_response(None),
            self.create_response({"error": "invalid_client"}, 400),
            self.create_response({"error": "invalid_grant"}, 401),
            self.create_response({"error": "invalid_grant"}, 429),
            self.create_response({"error": "invalid_grant"}, 500),
        ]
        task = self.load_refresh_task()
        for response in responses:
            with self.subTest(status=response.status_code):
                self.session.post.return_value = response
                task()
                token.refresh_from_db()
                self.assertEqual(token.access_token, "fixture-old-access")
                self.assertEqual(token.refresh_token, "fixture-old-refresh")

    def test_scheduled_missing_credentials_are_retained_for_configuration_repair(self):
        token = self.create_token(expired=True)
        token.refresh_token = ""
        with patch.object(DiscordToken.objects, "all", return_value=[token]):
            self.load_refresh_task()()
        self.assertTrue(DiscordToken.objects.filter(pk=token.pk).exists())
        self.session.post.assert_not_called()

    def test_scheduled_confirmed_invalid_grant_deletes_revoked_token_without_user(self):
        token = self.create_token(expired=True)
        token.user = None
        token.save()
        self.session.post.return_value = self.create_response({"error": "invalid_grant"}, 400)
        self.load_refresh_task()()
        self.assertFalse(DiscordToken.objects.filter(pk=token.pk).exists())

    def test_scheduled_database_errors_remain_visible(self):
        self.create_token(expired=True)
        with patch.object(DiscordToken, "save", side_effect=DatabaseError("fixture database error")):
            with self.assertRaisesRegex(DatabaseError, "fixture database error"):
                self.load_refresh_task()()

    def test_stale_browser_and_scheduled_copies_use_latest_stored_rotation(self):
        for initial_reader in ("browser", "scheduled"):
            with self.subTest(initial_reader=initial_reader):
                first = self.create_token(
                    expired=True, user_id=8901 if initial_reader == "browser" else 8902,
                )
                stale = DiscordToken.objects.get(pk=first.pk)
                stale.login()
                self.session.post.reset_mock()
                self.session.post.return_value = self.create_response({
                    **self.tokens, "expires_in": 7 * 24 * 60 * 60,
                })
                if initial_reader == "browser":
                    first.identity()
                else:
                    first.update_code()
                # A second exchange with the old grant would fail. The stale reader
                # must reload the stored rotation before deciding whether to renew.
                self.session.post.return_value = self.create_response({"error": "invalid_grant"}, 400)
                if initial_reader == "browser":
                    stale.update_code()
                else:
                    stale.identity()
                self.session.post.assert_called_once()
                self.assertEqual(stale.access_token, "fixture-new-access")
                self.assertEqual(stale.refresh_token, "fixture-new-refresh")
                self.assertTrue(DiscordToken.objects.filter(pk=first.pk).exists())

    def test_deleted_stored_token_does_not_use_a_stale_copy(self):
        token = self.create_token(expired=True)
        DiscordToken.objects.filter(pk=token.pk).delete()
        self.assert_sanitized_error(token.identity)
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()
