"""Checks the temporary shared viewer without tunnel or Discord connections."""

import ast
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import inspect
import io
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import Resolver404, clear_url_caches, resolve

import corpoch
from corpoch.dbot.models import Guilds
from corpoch.models import DiscordToken, DiscordUser
from staging.configuration import StagingConfigurationError, validate_configuration
from staging.referee_check import RefereeCheckError
from staging.settings import build_web_settings
from staging.sharing import (
    SharedAccessError, SharedRefereeAccess, SharedRefereeMiddleware, SharedSurface,
    build_shared_settings, validate_public_origin,
)
from staging.viewer_fixture import prepare_fixture
from tests.model_test_discord_auth import load_auth_views
from tests.test_staging_configuration import create_configuration_values


class SharedSettingsTests(SimpleTestCase):
    """Keeps temporary HTTPS settings separate from the existing local website."""

    def test_origin_accepts_only_one_exact_temporary_https_hostname(self):
        origin = "https://fixture-viewer.trycloudflare.com"
        self.assertEqual(validate_public_origin(origin), origin)
        self.assertEqual(validate_public_origin(origin + "/"), origin)
        for invalid in (
            None, "", " " + origin, origin + "\n", origin + "/home", origin + "?query=1",
            origin + "#fragment", origin + ":443", origin + ".invalid", origin + "//",
            "http://fixture-viewer.trycloudflare.com", "https://trycloudflare.com",
            "https://fixture_viewer.trycloudflare.com", "https://-fixture.trycloudflare.com",
            "https://fixture-.trycloudflare.com", "https://fixture..trycloudflare.com",
            "https://account@fixture-viewer.trycloudflare.com", "https://127.0.0.1",
        ):
            with self.subTest(origin=invalid), self.assertRaises(StagingConfigurationError):
                validate_public_origin(invalid)

    def test_shared_settings_use_secure_host_cookies_and_matching_oauth_without_changing_local_defaults(self):
        values = create_configuration_values()
        configuration = validate_configuration(values, Path("/private/staging"), values["bot_id"])
        local = build_web_settings(configuration)
        origin = "https://fixture-viewer.trycloudflare.com"
        shared = {**local, **build_shared_settings(configuration, origin)}
        self.assertEqual(shared["ALLOWED_HOSTS"], ["fixture-viewer.trycloudflare.com"])
        self.assertEqual(shared["CSRF_TRUSTED_ORIGINS"], [origin])
        self.assertEqual(shared["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertEqual(shared["ROOT_URLCONF"], "staging.shared_urls")
        self.assertEqual(shared["REDIRECT_URI"], origin + "/auth")
        oauth = urlsplit(shared["AUTH_URL_DISCORD"])
        self.assertEqual((oauth.scheme, oauth.netloc, oauth.path), ("https", "discord.com", "/oauth2/authorize"))
        self.assertEqual(parse_qs(oauth.query), {
            "client_id": [values["bot_id"]], "response_type": ["code"],
            "redirect_uri": [origin + "/auth"], "scope": ["identify guilds"],
        })
        for prefix in ("SESSION", "CSRF"):
            self.assertTrue(shared[prefix + "_COOKIE_NAME"].startswith("__Host-"))
            self.assertTrue(shared[prefix + "_COOKIE_SECURE"])
            self.assertIsNone(shared[prefix + "_COOKIE_DOMAIN"])
            self.assertEqual(shared[prefix + "_COOKIE_PATH"], "/")
        self.assertTrue(shared["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(shared["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(shared["SESSION_COOKIE_AGE"], 3600)
        self.assertFalse(shared["DEBUG"] or shared["DISCORD_PROFILE_SYNC_ENABLED"])
        self.assertEqual(shared["DATABASES"], local["DATABASES"])
        self.assertEqual(local, build_web_settings(configuration))
        self.assertEqual(local["ALLOWED_HOSTS"], ["127.0.0.1"])
        self.assertFalse(local["SESSION_COOKIE_SECURE"] or local["MATCH_VIEWER_ENABLED"])


class SharedAccessTests(SimpleTestCase):
    """Verifies live-role denial, short caches and bounded membership reads."""

    def setUp(self):
        self.bot_id = "89001234567890123"
        self.guild_id = "89001234567890125"
        self.account_id = 89001234567890124
        self.role_id = "89001234567890126"
        self.access = SharedRefereeAccess("fixture-token", self.bot_id, {
            "guild_id": int(self.guild_id), "role_ids": (int(self.role_id),),
        })
        self.transport_type = self.enterContext(patch("staging.sharing.RefereeTransport"))
        self.transport = self.transport_type.return_value
        self.documents = [
            {"id": self.bot_id, "bot": True},
            [{"id": self.role_id, "name": "DEV referee"}],
            {"user": {"id": str(self.account_id), "bot": False}, "roles": [self.role_id]},
        ]
        self.transport.get_json.side_effect = self.documents

    def test_checks_only_pinned_identity_roles_and_member_then_closes_connection(self):
        self.access.verify(self.account_id)
        self.transport_type.assert_called_once_with(self.guild_id, str(self.account_id))
        self.assertEqual([call.args for call in self.transport.get_json.call_args_list], [
            ("https://discord.com/api/v10/users/@me", "fixture-token"),
            (f"https://discord.com/api/v10/guilds/{self.guild_id}/roles", "fixture-token"),
            (f"https://discord.com/api/v10/guilds/{self.guild_id}/members/{self.account_id}", "fixture-token"),
        ])
        self.transport.close.assert_called_once()
        self.assertNotIn("fixture-token", repr(self.access))

    def test_wrong_bot_or_unavailable_configured_role_denies_before_reading_member(self):
        for documents, calls in (
            ([{"id": "89001234567890129", "bot": True}], 1),
            ([{"id": self.bot_id, "bot": False}], 1),
            ([self.documents[0], []], 2),
        ):
            with self.subTest(documents=documents):
                self.transport.reset_mock()
                self.transport.get_json.side_effect = documents
                with self.assertRaises(SharedAccessError) as raised:
                    self.access.read_member(self.account_id)
                self.assertEqual(raised.exception.status, 503)
                self.assertEqual(self.transport.get_json.call_count, calls)
                self.transport.close.assert_called_once()

    def test_confirmed_missing_membership_or_role_is_403_and_transport_failure_is_503(self):
        for member, expected in (
            ({"user": {"id": str(self.account_id)}, "roles": []}, 403),
            ({"user": {"id": str(self.account_id), "bot": True}, "roles": [self.role_id]}, 403),
            ({"user": {"id": str(self.account_id)}, "roles": [self.role_id], "pending": True}, 403),
            (RefereeCheckError("Fixture member absent.", status_code=404), 403),
            (RefereeCheckError("Fixture request forbidden.", status_code=403), 503),
            (RefereeCheckError("Fixture rate limit.", status_code=429), 503),
            (RefereeCheckError("Fixture transport failed."), 503),
        ):
            with self.subTest(member=member):
                self.transport.reset_mock()
                self.transport.get_json.side_effect = [*self.documents[:2], member]
                with self.assertRaises(SharedAccessError) as raised:
                    self.access.read_member(self.account_id)
                self.assertEqual(raised.exception.status, expected)
                self.transport.close.assert_called_once()

    def test_positive_cache_expires_at_ten_seconds_and_then_observes_revocation(self):
        with (
            patch("staging.sharing.time.monotonic", return_value=100) as clock,
            patch.object(self.access, "read_member") as read,
        ):
            self.access.verify(self.account_id)
            clock.return_value = 109.999
            self.access.verify(self.account_id)
            read.assert_called_once_with(self.account_id)
            clock.return_value = 110
            read.side_effect = SharedAccessError("No referee role.", 403)
            with self.assertRaises(SharedAccessError) as raised:
                self.access.verify(self.account_id)
            self.assertEqual(raised.exception.status, 403)
            self.assertEqual(read.call_count, 2)

    def test_malformed_member_metadata_is_temporary_failure_instead_of_confirmed_revocation(self):
        for member in (
            None, [], {}, {"roles": [self.role_id]},
            {"user": {"id": "89001234567890129"}, "roles": [self.role_id]},
            {"user": {"id": str(self.account_id)}, "roles": None},
            {"user": {"id": str(self.account_id)}, "roles": [self.role_id, self.role_id]},
            {"user": {"id": str(self.account_id)}, "roles": ["invalid-role"]},
            {"user": {"id": str(self.account_id), "bot": "false"}, "roles": [self.role_id]},
            {"user": {"id": str(self.account_id)}, "roles": [self.role_id], "pending": "false"},
        ):
            with self.subTest(member=member):
                self.transport.reset_mock()
                self.transport.get_json.side_effect = [*self.documents[:2], member]
                with self.assertRaises(SharedAccessError) as raised:
                    self.access.read_member(self.account_id)
                self.assertEqual(raised.exception.status, 503)
                self.transport.close.assert_called_once()

    def test_failure_cache_retries_at_five_seconds_without_extending_on_poll(self):
        for status in (403, 503):
            with (
                self.subTest(status=status),
                patch("staging.sharing.time.monotonic", return_value=100) as clock,
                patch.object(self.access, "read_member", side_effect=SharedAccessError("Fixture denied.", status)) as read,
            ):
                self.access.entries.clear()
                for now in (100, 104.999):
                    clock.return_value = now
                    with self.assertRaises(SharedAccessError) as raised:
                        self.access.verify(self.account_id)
                    self.assertEqual(raised.exception.status, status)
                read.assert_called_once_with(self.account_id)
                clock.return_value = 105
                read.side_effect = None
                self.access.verify(self.account_id)
                self.assertEqual(read.call_count, 2)

    def test_cache_accepts_twenty_live_entries_and_recovers_space_after_expiry(self):
        with (
            patch("staging.sharing.time.monotonic", return_value=100) as clock,
            patch.object(self.access, "read_member") as read,
        ):
            for offset in range(20):
                self.access.verify(self.account_id + offset)
            self.access.verify(self.account_id)
            with self.assertRaises(SharedAccessError) as raised:
                self.access.verify(self.account_id + 20)
            self.assertEqual(raised.exception.status, 503)
            self.assertEqual(read.call_count, 20)
            self.assertEqual(len(self.access.entries), 20)
            clock.return_value = 110
            self.access.verify(self.account_id + 20)
            self.assertEqual(read.call_count, 21)
            self.assertEqual(len(self.access.entries), 1)

    def test_invalid_identity_never_reaches_discord(self):
        for account_id in (None, True, 0, -1, "89001234567890124", 2 ** 63):
            with self.subTest(account_id=account_id), self.assertRaises(SharedAccessError) as raised:
                self.access.verify(account_id)
            self.assertEqual(raised.exception.status, 401)
        self.transport_type.assert_not_called()


class SharedSurfaceTests(SimpleTestCase):
    """Exercises the actual ASGI outer boundary before Django or static serving."""

    def setUp(self):
        self.messages = []
        self.calls = []
        self.application = SharedSurface(self.respond, "https://fixture-viewer.trycloudflare.com")

    async def respond(self, scope, receive, send):
        self.calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"cache-control", b"public, max-age=3600"), (b"Cache-Control", b"public"),
            (b"Referrer-Policy", b"unsafe-url"), (b"content-type", b"text/html"),
        ]})
        await send({"type": "http.response.body", "body": b"fixture"})

    async def collect(self, message):
        self.messages.append(message)

    def request(self, **overrides):
        self.messages.clear()
        self.calls.clear()
        scope = {
            "type": "http", "method": "GET", "path": "/home",
            "headers": [(b"host", b"fixture-viewer.trycloudflare.com"), (b"x-forwarded-proto", b"https")],
        }
        scope.update(overrides)
        coroutine = self.application(scope, AsyncMock(), self.collect)
        try:
            coroutine.send(None)
        except StopIteration:
            pass
        else:
            self.fail("The boundary attempted an unexpected asynchronous operation.")
        finally:
            coroutine.close()
        return self.messages

    def test_all_required_get_routes_reach_application_and_are_private(self):
        for path in (
            "/", "/home", "/auth/start", "/auth", "/auth/user", "/match-viewer/",
            "/match-viewer/local-viewer-pilot/", "/match-viewer/local-viewer-pilot/state/",
            "/static/corpoch/match_viewer.css", "/static/corpoch/match_viewer.js",
        ):
            with self.subTest(path=path):
                messages = self.request(path=path)
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(messages[0]["status"], 200)
                self.assertEqual(messages[0]["headers"], [
                    (b"content-type", b"text/html"), (b"cache-control", b"private, no-store, max-age=0"),
                    (b"referrer-policy", b"no-referrer"),
                ])
                self.assertEqual(messages[1]["body"], b"fixture")

    def test_other_match_admin_api_media_and_static_paths_never_reach_application(self):
        for path in (
            "/admin/", "/api/", "/livematches/", "/update-live-matches/", "/match-viewer/other/",
            "/match-viewer/other/state/", "/match-viewer/local-viewer-pilot/state", "/home/",
            "/unserved-private-media/key.json", "/static/corpoch/other.js", "/static/admin/css/base.css",
            "/static/../staging/configuration.py", "/static/corpoch/match_viewer.js/../private",
        ):
            with self.subTest(path=path):
                messages = self.request(path=path)
                self.assertEqual(messages[0]["status"], 404)
                self.assertIn((b"cache-control", b"no-store"), messages[0]["headers"])
                self.assertEqual(self.calls, [])

    def test_non_get_requests_and_websockets_are_denied_before_application(self):
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method)[0]["status"], 404)
                self.assertEqual(self.calls, [])
        self.assertEqual(self.request(type="websocket"), [{"type": "websocket.close", "code": 1008}])
        self.assertEqual(self.calls, [])

    def test_exact_single_host_and_https_forwarding_are_required(self):
        host = (b"host", b"fixture-viewer.trycloudflare.com")
        protocol = (b"x-forwarded-proto", b"https")
        for headers in (
            [], [host], [protocol], [host, host, protocol], [host, protocol, protocol],
            [(b"host", b"127.0.0.1"), protocol], [(b"host", b"other.trycloudflare.com"), protocol],
            [(b"host", b"fixture-viewer.trycloudflare.com:443"), protocol],
            [host, (b"x-forwarded-proto", b"http")], [host, (b"x-forwarded-proto", b"https,http")],
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0]["status"], 404)
                self.assertEqual(self.calls, [])


class SharedMiddlewareTests(TestCase):
    """Uses real sample grants and OAuth views with mocked membership responses."""

    def setUp(self):
        self.owner = DiscordUser.objects.create(pk=710, is_active=True)
        DiscordToken.objects.create(user=self.owner, access_token="fixture-owner", refresh_token="fixture-owner-refresh")
        prepare_fixture(510, self.owner.pk, [{"id": 610, "name": "DEV referee"}])
        self.account = DiscordUser.objects.create(pk=711, is_active=True)
        DiscordToken.objects.create(user=self.account, access_token="fixture-other", refresh_token="fixture-other-refresh")
        self.access = Mock(guild_id="510")
        self.factory = RequestFactory()
        self.downstream = Mock(return_value=HttpResponse("Approved fixture response."))
        self.middleware = SharedRefereeMiddleware(self.downstream)
        self.enterContext(self.settings(SHARED_DEV_ACCESS=self.access))
        values = create_configuration_values()
        configuration = validate_configuration(values, Path("/private/staging"), values["bot_id"])
        web = build_web_settings(configuration)
        self.origin = "https://fixture-viewer.trycloudflare.com"
        shared = build_shared_settings(configuration, self.origin)
        self.views = self.enterContext(load_auth_views())
        source = Path(__file__).parents[1] / "staging" / "shared_urls.py"
        specification = importlib.util.spec_from_file_location("isolated_shared_urls", source)
        self.urls = importlib.util.module_from_spec(specification)
        with (
            patch.dict(sys.modules, {"corpoch.views": self.views}),
            patch.object(corpoch, "views", self.views, create=True),
        ):
            specification.loader.exec_module(self.urls)
        selected = {name: web[name] for name in (
            "TEMPLATES", "MIDDLEWARE", "AUTHENTICATION_BACKENDS", "BOT_ID", "BOT_SECRET",
            "DISCORD_PROFILE_SYNC_ENABLED", "STATIC_URL", "DEBUG", "SESSION_ENGINE",
        )}
        selected.update(shared)
        selected["ROOT_URLCONF"] = self.urls
        selected["MIDDLEWARE"] = [*selected["MIDDLEWARE"], "staging.sharing.SharedRefereeMiddleware"]
        self.enterContext(self.settings(**selected))
        clear_url_caches()
        self.addCleanup(clear_url_caches)

    def request(self, user=None, path="/match-viewer/local-viewer-pilot/state/"):
        request = self.factory.get(path, HTTP_HOST="fixture-viewer.trycloudflare.com", secure=True)
        request.user = self.account if user is None else user
        return request

    def is_granted(self):
        return Guilds.objects.get(pk=510).referees.filter(pk=self.account.pk).exists()

    def test_verified_oauth_referee_receives_only_scoped_local_grant(self):
        self.assertFalse(self.is_granted())
        self.assertEqual(self.middleware(self.request()).status_code, 200)
        self.access.verify.assert_called_once_with(self.account.pk)
        self.assertTrue(self.is_granted())
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff or self.account.is_superuser)
        self.assertFalse(self.account.guilds_admin.exists())
        self.downstream.assert_called_once()

    def test_role_removal_revokes_grant_but_temporary_failure_keeps_it_and_denies_read(self):
        guild = Guilds.objects.get(pk=510)
        for status, remains_granted in ((403, False), (503, True)):
            with self.subTest(status=status):
                guild.referees.add(self.account)
                self.downstream.reset_mock()
                self.access.verify.side_effect = SharedAccessError("Fixture role check denied.", status)
                response = self.middleware(self.request())
                self.assertEqual(response.status_code, status)
                self.assertEqual(self.is_granted(), remains_granted)
                self.assertIn("no-store", response["Cache-Control"])
                self.downstream.assert_not_called()
                self.assertTrue(guild.referees.filter(pk=self.owner.pk).exists())

    def test_disabled_or_missing_oauth_account_cannot_gain_web_access(self):
        for restriction in ("disabled", "missing_oauth"):
            with self.subTest(restriction=restriction):
                if restriction == "disabled":
                    self.account.is_active = False
                    self.account.save(update_fields=["is_active"])
                else:
                    self.account.is_active = True
                    self.account.save(update_fields=["is_active"])
                    DiscordToken.objects.filter(user=self.account).delete()
                response = self.middleware(self.request())
                self.assertIn(response.status_code, (401, 403))
                self.assertFalse(self.is_granted())
                self.downstream.assert_not_called()
        self.access.verify.assert_not_called()

    def test_stale_session_user_cannot_restore_a_disabled_database_account(self):
        DiscordUser.objects.filter(pk=self.account.pk).update(is_active=False)
        self.assertTrue(self.account.is_active)
        self.assertEqual(self.middleware(self.request()).status_code, 403)
        self.access.verify.assert_not_called()
        self.assertFalse(self.is_granted())
        self.downstream.assert_not_called()

    def test_anonymous_or_non_viewer_requests_cannot_create_grants(self):
        for request in (self.request(user=AnonymousUser()), self.request(path="/home"), self.request(path="/auth/start")):
            self.assertEqual(self.middleware(request).status_code, 200)
        self.access.verify.assert_not_called()
        self.assertFalse(self.is_granted())

    def test_changed_owned_fixture_returns_controlled_failure_during_grant_or_revoke(self):
        for action in ("grant_shared_referee", "revoke_shared_referee"):
            with (
                self.subTest(action=action),
                patch("staging.discord_fixture." + action, side_effect=StagingConfigurationError("Fixture unavailable.")),
            ):
                self.access.verify.side_effect = SharedAccessError("Role removed.", 403) if action.startswith("revoke") else None
                response = self.middleware(self.request())
                self.assertEqual(response.status_code, 503)
                self.downstream.assert_not_called()

    def test_database_failures_during_grant_or_revoke_return_sanitized_503(self):
        for action in ("grant_shared_referee", "revoke_shared_referee"):
            with (
                self.subTest(action=action),
                patch("staging.discord_fixture." + action, side_effect=DatabaseError("private fixture database detail")),
            ):
                self.access.verify.side_effect = SharedAccessError("Role removed.", 403) if action.startswith("revoke") else None
                response = self.middleware(self.request())
                self.assertEqual(response.status_code, 503)
                self.assertNotContains(response, "private fixture database detail", status_code=503)
                self.downstream.assert_not_called()

    def test_shared_routes_omit_unrelated_pages_and_selector_redirects_only_to_sample(self):
        for path in ("/admin/", "/api/", "/livematches/", "/unserved-private-media/key.json"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
        response = self.client.get("/match-viewer/", secure=True, HTTP_HOST="fixture-viewer.trycloudflare.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/match-viewer/local-viewer-pilot/")

    def test_anonymous_fragment_is_denied_and_successful_login_still_requires_current_roles(self):
        hostname = "fixture-viewer.trycloudflare.com"
        response = self.client.get("/match-viewer/local-viewer-pilot/state/", secure=True, HTTP_HOST=hostname)
        self.assertEqual(response.status_code, 401)
        self.access.verify.assert_not_called()
        identity = {"id": str(self.account.pk), "username": "Shared fixture", "global_name": "Shared fixture"}
        with patch("corpoch.models.misc.Session") as session_type:
            session = session_type.return_value
            session.post.return_value = Mock(status_code=200, json=Mock(return_value={
                "access_token": "fixture-new-access", "refresh_token": "fixture-new-refresh",
                "scope": "identify guilds", "expires_in": 3600,
            }))
            session.get.side_effect = [
                Mock(status_code=200, json=Mock(return_value=identity)),
                Mock(status_code=200, json=Mock(return_value=identity)),
                Mock(status_code=200, json=Mock(return_value=[])),
            ]
            start = self.client.get("/auth/start", secure=True, HTTP_HOST=hostname)
            parameters = parse_qs(urlsplit(start.url).query)
            self.assertEqual(parameters["redirect_uri"], [self.origin + "/auth"])
            response = self.client.get("/auth", {"state": parameters["state"][0], "code": "fixture-code"},
                                       secure=True, HTTP_HOST=hostname)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "/auth/user")
            response = self.client.get(response.url, secure=True, HTTP_HOST=hostname)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/match-viewer/local-viewer-pilot/")
        self.assertEqual(self.client.session["_auth_user_id"], str(self.account.pk))
        self.assertFalse(self.is_granted())
        self.assertTrue(self.client.cookies["__Host-corpo_shared_session"]["secure"])
        self.views.update_user.assert_not_called()
        self.access.verify.side_effect = SharedAccessError("An approved DEV referee role is required.", 403)
        denied = self.client.get("/match-viewer/local-viewer-pilot/state/", secure=True, HTTP_HOST=hostname)
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(self.is_granted())
        self.access.verify.side_effect = None
        allowed = self.client.get("/match-viewer/local-viewer-pilot/state/", secure=True, HTTP_HOST=hostname)
        self.assertContains(allowed, "Blue Player")
        self.assertTrue(self.is_granted())
        self.assertIn("no-store", allowed["Cache-Control"])

    def test_shared_login_wrapper_preserves_errors_and_never_bypasses_authentication(self):
        for user, status in ((AnonymousUser(), 200), (self.account, 403), (self.account, 503)):
            with self.subTest(status=status, authenticated=user.is_authenticated):
                expected = HttpResponse("Fixture login continuation.", status=status)
                with patch.object(self.views, "user", return_value=expected):
                    actual = self.urls.shared_user(self.request(user=user, path="/auth/user"))
                self.assertIs(actual, expected)


class SharedLauncherTests(SimpleTestCase):
    """Checks explicit launcher scope without opening files, listeners or services."""

    def setUp(self):
        from staging import shared_web

        self.launcher = shared_web
        values = create_configuration_values()
        self.configuration = validate_configuration(values, Path("/private/staging"), values["bot_id"])
        self.arguments = [
            "--config", "/private/staging/config.json", "--expected-bot-id", values["bot_id"],
            "--guild-id", "510", "--credentials-file", "/private/dev-credentials.env",
            "--public-origin", "https://fixture-viewer.trycloudflare.com",
        ]
        self.snapshot = {"guild_id": 510, "account_id": 710, "role_ids": (610,)}
        selected = build_shared_settings(self.configuration, "https://fixture-viewer.trycloudflare.com")
        selected.update(MIDDLEWARE=[], SHARED_DEV_ACCESS=None)
        self.enterContext(self.settings(**selected))
        self.load = self.enterContext(patch("staging.shared_web.load_configuration", return_value=self.configuration))
        self.configure = self.enterContext(patch("staging.shared_web.configure_web_runtime"))
        self.setup = self.enterContext(patch("django.setup"))
        self.boundary = self.enterContext(patch("staging.shared_web.validate_database_boundary"))
        self.snapshot_reader = self.enterContext(patch("staging.discord_fixture.build_control_snapshot", return_value=self.snapshot))
        self.close = self.enterContext(patch("django.db.connection.close"))
        self.read_private = self.enterContext(patch("staging.shared_web.read_private_text", return_value="fixture-private-text"))
        self.credentials = self.enterContext(patch("staging.shared_web.parse_discord_credentials", return_value={"BOT_TOKEN": "fixture-token"}))
        self.access_type = self.enterContext(patch("staging.shared_web.SharedRefereeAccess"))
        self.static = self.enterContext(patch("django.contrib.staticfiles.handlers.ASGIStaticFilesHandler"))
        self.asgi = self.enterContext(patch("django.core.asgi.get_asgi_application", return_value=Mock()))
        server_module = ModuleType("daphne.server")
        server_module.Server = Mock()
        self.server = server_module.Server
        self.server.return_value.listening_addresses = [("127.0.0.1", 8768)]
        self.enterContext(patch.dict(sys.modules, {"daphne.server": server_module}))
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))

    def test_check_reads_owned_database_and_builds_application_without_startup_or_remote_verification(self):
        self.assertEqual(self.launcher.main([*self.arguments, "check"]), 0)
        self.configure.assert_called_once_with(self.configuration)
        self.boundary.assert_called_once()
        self.snapshot_reader.assert_called_once_with()
        self.close.assert_called_once()
        self.access_type.assert_called_once_with("fixture-token", self.configuration.bot_id, self.snapshot)
        self.access_type.return_value.verify.assert_not_called()
        self.asgi.assert_called_once()
        self.static.assert_called_once_with(self.asgi.return_value)
        self.server.assert_called_once()
        self.server.return_value.run.assert_not_called()
        self.assertNotIn("fixture-token", self.output.getvalue() + self.errors.getvalue())

    def test_run_verifies_owner_before_binding_only_the_separate_loopback_port(self):
        self.assertEqual(self.launcher.main([*self.arguments, "run"]), 0)
        self.access_type.return_value.verify.assert_called_once_with(710)
        self.server.assert_called_once()
        self.assertIsInstance(self.server.call_args.args[0], SharedSurface)
        self.assertEqual(self.server.call_args.kwargs, {
            "endpoints": ["tcp:port=8768:interface=127.0.0.1"],
            "action_logger": None, "http_timeout": 30, "verbosity": 0,
        })
        self.server.return_value.run.assert_called_once_with()

        # Importing daphne.server installs a socket-backed reactor. Check the
        # installed constructor itself without running those module imports.
        specification = importlib.util.find_spec("daphne")
        source = Path(specification.origin).with_name("server.py")
        definitions = ast.parse(source.read_text(encoding="utf-8"))
        server_definition = next(node for node in definitions.body if isinstance(node, ast.ClassDef) and node.name == "Server")
        constructor = next(node for node in server_definition.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        namespace = {}
        exec(compile(ast.Module(body=[constructor], type_ignores=[]), str(source), "exec"), namespace)
        inspect.signature(namespace["__init__"]).bind(object(), *self.server.call_args.args, **self.server.call_args.kwargs)

    def test_failed_listener_returns_failure_instead_of_reported_success(self):
        self.server.return_value.listening_addresses = []
        self.assertEqual(self.launcher.main([*self.arguments, "run"]), 1)
        self.assertIn("listener could not open", self.errors.getvalue())

    def test_invalid_origin_rejects_before_private_configuration_or_database_read(self):
        self.arguments[-1] = "https://elsewhere.invalid"
        self.assertEqual(self.launcher.main([*self.arguments, "run"]), 1)
        self.load.assert_not_called()
        self.boundary.assert_not_called()
        self.server.assert_not_called()

    def test_wrong_guild_or_unverified_database_cannot_read_credentials_or_open_listener(self):
        for failure in ("guild", "database"):
            with self.subTest(failure=failure):
                self.snapshot["guild_id"] = 511 if failure == "guild" else 510
                self.boundary.side_effect = StagingConfigurationError("Fixture database mismatch.") if failure == "database" else None
                self.assertEqual(self.launcher.main([*self.arguments, "run"]), 1)
                self.read_private.assert_not_called()
                self.server.assert_not_called()

    def test_membership_failure_prevents_listener_and_does_not_print_private_exception(self):
        self.access_type.return_value.verify.side_effect = SharedAccessError("private fixture response", 503)
        self.assertEqual(self.launcher.main([*self.arguments, "run"]), 1)
        self.server.return_value.run.assert_not_called()
        self.assertNotIn("private fixture response", self.output.getvalue() + self.errors.getvalue())
