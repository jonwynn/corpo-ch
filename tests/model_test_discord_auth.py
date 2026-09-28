"""Exercises real OAuth views and token models with Discord requests replaced."""

from contextlib import contextmanager
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import clear_url_caches, path
from django.utils import timezone

from corpoch.models import DiscordToken, DiscordUser


urlpatterns = []


@contextmanager
def load_auth_views():
    """
    Loads the production views without importing the outbound task module

    :return: View module with a mocked user-update queue boundary"""
    tasks = ModuleType("corpoch.dbot.tasks")
    tasks.update_user = Mock()
    source = Path(__file__).parents[1] / "corpoch" / "views.py"
    specification = importlib.util.spec_from_file_location("isolated_discord_auth_views", source)
    module = importlib.util.module_from_spec(specification)
    with patch.dict(sys.modules, {"corpoch.dbot.tasks": tasks}):
        specification.loader.exec_module(module)
        routes = [
            path("auth", module.auth),
            path("auth/start", module.auth_start, name="discord_auth_start"),
            path("auth/user", module.user, name="user"),
            path("home", module.home, name="home"),
            path("privterms/", module.privterms),
        ]
        with patch.object(sys.modules[__name__], "urlpatterns", routes):
            clear_url_caches()
            try:
                yield module
            finally:
                clear_url_caches()


@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ],
    AUTHENTICATION_BACKENDS=["corpoch.auth.DiscordBackend"],
    BOT_ID="8900",
    REDIRECT_URI="https://viewer.invalid/auth",
    AUTH_URL_DISCORD=(
        "https://discord.com/oauth2/authorize?client_id=8900&response_type=code"
        "&redirect_uri=https%3A%2F%2Fviewer.invalid%2Fauth&scope=identify+guilds&prompt=none"
    ),
)
class DiscordAuthTests(TestCase):
    """Verifies redirects, persisted tokens and successful login without services."""

    def setUp(self):
        self.factory = RequestFactory()
        self.views = self.enterContext(load_auth_views())
        self.session_type = self.enterContext(patch("corpoch.models.misc.Session"))
        self.discord_session = self.session_type.return_value
        self.identity = {"id": "8901", "avatar": None, "global_name": "Fixture Player"}
        self.token_response = {
            "access_token": "fixture-new-access",
            "refresh_token": "fixture-new-refresh",
            "scope": "identify guilds",
            "expires_in": 3600,
        }
        self.discord_session.post.return_value = Mock(status_code=200)
        self.discord_session.post.return_value.json.return_value = self.token_response
        self.discord_session.get.return_value = Mock(status_code=200)
        self.discord_session.get.return_value.json.return_value = self.identity

    def create_request(self, endpoint="/auth", parameters=None, session=None):
        """
        Creates a request using the deployment database session backend

        :param str endpoint: Requested path
        :param dict parameters: Query parameters
        :param SessionStore session: Existing browser session when provided
        :return: Request with a real stored session"""
        request = self.factory.get(endpoint, parameters or {})
        if session is not None:
            request.session = session
            return request
        SessionMiddleware(lambda unused_request: HttpResponse()).process_request(request)
        request.session.update({
            "access_token": "fixture-old-access",
            "user_id": "8901",
            "viewer_preference": "dark",
        })
        request.session.save()
        return request

    def create_callback(self, parameters=None):
        """
        Starts authorization and returns its callback in the same browser

        :param dict parameters: Code or denial returned by Discord
        :return: Callback with its real persisted browser session"""
        start = self.create_request("/auth/start")
        response = self.views.auth_start(start)
        self.assertEqual(response.status_code, 302)
        state = parse_qs(urlsplit(response.url).query)["state"][0]
        start.session.save()
        values = {"code": "fixture-code"} if parameters is None else dict(parameters)
        values["state"] = state
        return self.create_request(parameters=values, session=SessionStore(start.session.session_key))

    def create_token(self, **changes):
        """
        Stores a fixture token in the disposable model-test database

        :param dict changes: Token field overrides
        :return: Persisted token"""
        account = DiscordUser.objects.create(id=8901, global_name="Fixture Player")
        fields = {
            "user": account,
            "access_token": "fixture-old-access",
            "refresh_token": "fixture-old-refresh",
            "expires": timezone.now() + timedelta(days=7),
        }
        fields.update(changes)
        return DiscordToken.objects.create(**fields)

    def assert_login_recovery(self, request, response, status=400):
        """
        Checks recovery without discarding unrelated session preferences

        :param HttpRequest request: Request after view execution
        :param HttpResponse response: View response
        :param int status: Expected local recovery status"""
        self.assertEqual(response.status_code, status)
        self.assertContains(response, 'href="/auth/start"', status_code=status)
        self.assertNotIn("Location", response)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(request.session["viewer_preference"], "dark")
        for name in ("access_token", "user_id", "discord_oauth_state"):
            self.assertNotIn(name, request.session)
        self.views.update_user.assert_not_called()

    def test_auth_without_code_redirects_without_discord_or_database_work(self):
        request = self.create_request()
        with self.assertNumQueries(0):
            response = self.views.auth(request)
        self.assertEqual(response.url, "/auth/start")
        self.assertEqual(request.session["access_token"], "fixture-old-access")
        self.session_type.assert_not_called()

    def test_denied_consent_clears_only_stale_oauth_session_values(self):
        request = self.create_callback({"error": "access_denied"})
        self.assert_login_recovery(request, self.views.auth(request))
        self.session_type.assert_not_called()

    def test_rejected_callback_code_preserves_existing_stored_token(self):
        token = self.create_token()
        self.discord_session.post.return_value.status_code = 400
        self.discord_session.post.return_value.json.return_value = {"error": "invalid_grant"}
        request = self.create_callback()
        self.assert_login_recovery(request, self.views.auth(request))
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-old-access")
        self.discord_session.get.assert_not_called()

    def test_rejected_callback_identity_does_not_create_user_or_token(self):
        self.discord_session.get.return_value.status_code = 401
        request = self.create_callback()
        self.assert_login_recovery(request, self.views.auth(request))
        self.assertFalse(DiscordUser.objects.exists())
        self.assertFalse(DiscordToken.objects.exists())

    def test_valid_callback_creates_user_and_token_then_continues_login(self):
        self.assertFalse(hasattr(settings, "DISCORD_PROFILE_SYNC_ENABLED"))
        request = self.create_callback()
        response = self.views.auth(request)
        self.assertEqual(response.url, "/auth/user")
        token = DiscordToken.objects.get(user_id=8901)
        self.assertEqual(token.access_token, "fixture-new-access")
        self.assertEqual(token.refresh_token, "fixture-new-refresh")
        self.assertEqual(request.session["access_token"], token.access_token)
        self.assertEqual(request.session["user_id"], "8901")
        self.assertEqual(request.session["viewer_preference"], "dark")
        self.views.update_user.assert_called_once_with("8901")
        call = self.discord_session.post.call_args
        self.assertEqual(call.args[0], "https://discord.com/api/v10/oauth2/token")
        self.assertEqual(call.kwargs["data"]["code"], "fixture-code")
        self.assertEqual(call.kwargs["data"]["redirect_uri"], settings.REDIRECT_URI)

    @override_settings(DISCORD_PROFILE_SYNC_ENABLED=False)
    def test_disabled_profile_sync_preserves_login_without_queuing_bot_work(self):
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]
        start = self.client.get("/auth/start")
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        response = self.client.get("/auth", {"state": state, "code": "fixture-code"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["_auth_user_id"], "8901")
        account = DiscordUser.objects.get(id=8901)
        self.assertEqual(account.global_name, "Fixture Player")
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        token = DiscordToken.objects.get(user=account)
        self.assertEqual(token.access_token, "fixture-new-access")
        self.assertEqual(token.refresh_token, "fixture-new-refresh")
        self.views.update_user.assert_not_called()

    @override_settings(DISCORD_PROFILE_SYNC_ENABLED=True)
    def test_explicit_profile_sync_queues_the_new_account(self):
        request = self.create_callback()
        self.assertEqual(self.views.auth(request).url, "/auth/user")
        self.views.update_user.assert_called_once_with("8901")

    def test_valid_callback_updates_existing_token_without_duplicate_account(self):
        token = self.create_token()
        request = self.create_callback()
        self.assertEqual(self.views.auth(request).url, "/auth/user")
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-new-access")
        self.assertEqual(token.refresh_token, "fixture-new-refresh")
        self.assertEqual(DiscordToken.objects.count(), 1)
        self.assertEqual(DiscordUser.objects.count(), 1)
        self.views.update_user.assert_not_called()

    def test_user_with_incomplete_session_restarts_login_without_discord(self):
        for missing in ("access_token", "user_id"):
            with self.subTest(missing=missing):
                request = self.create_request("/auth/user")
                request.session.pop(missing)
                with self.assertNumQueries(0):
                    response = self.views.user(request)
                self.assert_login_recovery(request, response)
        self.session_type.assert_not_called()

    def test_user_with_deleted_token_restarts_login(self):
        request = self.create_request("/auth/user")
        self.assert_login_recovery(request, self.views.user(request))
        self.session_type.assert_not_called()

    def test_user_with_missing_refresh_token_restarts_login(self):
        token = self.create_token()
        token.refresh_token = ""
        request = self.create_request("/auth/user")
        with patch.object(DiscordToken.objects, "get", return_value=token):
            self.assert_login_recovery(request, self.views.user(request))
        self.assertTrue(DiscordToken.objects.filter(pk=token.pk).exists())
        self.discord_session.get.assert_not_called()
        self.discord_session.post.assert_not_called()

    def test_user_with_rejected_identity_restarts_login(self):
        self.create_token()
        self.discord_session.get.return_value.status_code = 401
        request = self.create_request("/auth/user")
        self.assert_login_recovery(request, self.views.user(request))

    def test_user_with_rejected_guild_request_restarts_login(self):
        self.create_token()
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=403),
        ]
        request = self.create_request("/auth/user")
        self.assert_login_recovery(request, self.views.user(request))

    def test_valid_user_response_keeps_existing_authentication_and_render_flow(self):
        token = self.create_token()
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]
        request = self.create_request("/auth/user")
        with (
            patch.object(self.views, "authenticate", return_value=token.user) as authenticate,
            patch.object(self.views, "login") as login,
            patch.object(self.views, "render", return_value=HttpResponse("Fixture user")) as render,
        ):
            response = self.views.user(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(authenticate.call_args.kwargs["user"].id, "8901")
        login.assert_called_once_with(request, token.user, backend="corpoch.auth.DiscordBackend")
        self.assertEqual(render.call_args.args[:2], (request, "user.html"))
        self.assertEqual(render.call_args.kwargs["context"]["internal_user"], token.user)
        self.assertEqual(request.session["access_token"], "fixture-old-access")
        self.discord_session.post.assert_not_called()

    def test_start_preserves_configured_parameters_and_replaces_static_state(self):
        request = self.create_request("/auth/start")
        with override_settings(AUTH_URL_DISCORD=settings.AUTH_URL_DISCORD + "&state=static&state=second"):
            response = self.views.auth_start(request)
        parameters = parse_qs(urlsplit(response.url).query)
        self.assertEqual(parameters["scope"], ["identify guilds"])
        self.assertEqual(parameters["redirect_uri"], [settings.REDIRECT_URI])
        self.assertEqual(parameters["client_id"], ["8900"])
        self.assertEqual(parameters["prompt"], ["none"])
        self.assertEqual(parameters["state"], [request.session["discord_oauth_state"]])
        self.assertNotEqual(parameters["state"][0], request.session.session_key)
        record = Session.objects.get(session_key=parameters["state"][0])
        self.assertNotIn(request.session.session_key, str(record.get_decoded()))
        self.assertLessEqual((record.expire_date - timezone.now()).total_seconds(), 600)
        self.session_type.assert_not_called()

    def test_start_replaces_previous_attempt_and_preserves_unrelated_browser_data(self):
        request = self.create_request("/auth/start")
        self.views.auth_start(request)
        first_nonce = request.session["discord_oauth_state"]
        self.views.auth_start(request)
        self.assertNotEqual(first_nonce, request.session["discord_oauth_state"])
        self.assertFalse(Session.objects.filter(session_key=first_nonce).exists())
        self.assertEqual(request.session["viewer_preference"], "dark")

    def test_client_starts_with_cookie_and_rejects_callback_from_different_browser(self):
        response = self.client.get("/auth/start")
        state = parse_qs(urlsplit(response.url).query)["state"][0]
        original_session = self.client.session
        other_request = self.create_request(parameters={"code": "fixture-code", "state": state})
        other_request.session["discord_oauth_state"] = state
        self.assert_login_recovery(other_request, self.views.auth(other_request))
        self.assertTrue(Session.objects.filter(session_key=state).exists())
        self.assertEqual(original_session["discord_oauth_state"], state)
        self.session_type.assert_not_called()

    def test_invalid_or_missing_state_is_rejected_before_discord_or_token_updates(self):
        for returned_state in (None, "wrong-state", "", "é" * 32):
            with self.subTest(state=returned_state):
                request = self.create_callback()
                request.GET = request.GET.copy()
                if returned_state is None:
                    request.GET.pop("state")
                else:
                    request.GET["state"] = returned_state
                self.assert_login_recovery(request, self.views.auth(request))
        self.session_type.assert_not_called()
        self.assertFalse(DiscordToken.objects.exists())

    def test_expired_future_or_unmarked_attempt_is_rejected(self):
        for change in ("expired", "future", "unmarked"):
            with self.subTest(change=change):
                request = self.create_callback()
                nonce = request.GET["state"]
                attempt = SessionStore(nonce)
                if change == "expired":
                    Session.objects.filter(session_key=nonce).update(expire_date=timezone.now() - timedelta(seconds=1))
                elif change == "future":
                    attempt["issued_at"] = timezone.now().timestamp() + 60
                    attempt.save()
                else:
                    attempt["purpose"] = "different-purpose"
                    attempt.save()
                self.assert_login_recovery(request, self.views.auth(request))
        self.session_type.assert_not_called()

    def test_denied_consent_consumes_state_without_external_redirect(self):
        request = self.create_callback({"error": "access_denied", "error_description": "private-external-detail"})
        response = self.views.auth(request)
        self.assert_login_recovery(request, response)
        self.assertFalse(Session.objects.filter(session_key=request.GET["state"]).exists())
        self.assertNotContains(response, "private-external-detail", status_code=400)
        self.session_type.assert_not_called()

    def test_preloaded_parallel_callback_sessions_allow_only_one_code_exchange(self):
        first = self.create_callback()
        second_session = SessionStore(first.session.session_key)
        self.assertEqual(second_session["discord_oauth_state"], first.GET["state"])
        self.assertEqual(first.session["discord_oauth_state"], first.GET["state"])
        second = self.create_request(parameters=first.GET, session=second_session)
        self.assertEqual(self.views.auth(first).status_code, 302)
        self.views.update_user.reset_mock()
        self.assert_login_recovery(second, self.views.auth(second))
        self.discord_session.post.assert_called_once()
        self.assertEqual(DiscordToken.objects.count(), 1)

    def test_client_callback_is_single_use_after_success_redirect(self):
        response = self.client.get("/auth/start")
        state = parse_qs(urlsplit(response.url).query)["state"][0]
        parameters = {"state": state, "code": "fixture-code"}
        first = self.client.get("/auth", parameters)
        self.assertEqual(first.url, "/auth/user")
        self.assertNotIn("discord_oauth_state", self.client.session)
        second = self.client.get("/auth", parameters)
        self.assertEqual(second.status_code, 400)
        self.discord_session.post.assert_called_once()

    def test_bad_authorization_configuration_has_no_external_redirect(self):
        replacements = (
            settings.AUTH_URL_DISCORD.replace("discord.com", "discord.com.attacker.invalid"),
            settings.AUTH_URL_DISCORD.replace("https://", "http://", 1),
            settings.AUTH_URL_DISCORD.replace("client_id=8900", "client_id=9999"),
            settings.AUTH_URL_DISCORD.replace("response_type=code", "response_type=token"),
            settings.AUTH_URL_DISCORD.replace("scope=identify+guilds", "scope=identify"),
            settings.AUTH_URL_DISCORD + "&client_id=8900",
            settings.AUTH_URL_DISCORD + "#fragment",
            settings.AUTH_URL_DISCORD.replace("viewer.invalid", "other.invalid"),
        )
        for destination in replacements:
            with self.subTest(destination=destination), override_settings(AUTH_URL_DISCORD=destination):
                request = self.create_request("/auth/start")
                self.assert_login_recovery(request, self.views.auth_start(request), status=503)
        self.session_type.assert_not_called()

    def test_unsupported_session_engine_and_storage_failure_recover_locally(self):
        request = self.create_request("/auth/start")
        with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies"):
            self.assert_login_recovery(request, self.views.auth_start(request), status=503)
        request = self.create_callback()
        with patch.object(self.views, "consume_discord_attempt", side_effect=DatabaseError("private-db-detail")):
            response = self.views.auth(request)
        self.assert_login_recovery(request, response, status=503)
        self.assertNotContains(response, "private-db-detail", status_code=503)
        self.session_type.assert_not_called()

    def test_login_links_use_local_start_and_oauth_routes_reject_post(self):
        for endpoint in ("/home", "/privterms/"):
            self.assertContains(self.client.get(endpoint), 'href="/auth/start"')
        for endpoint in ("/auth", "/auth/start", "/auth/user"):
            self.assertEqual(self.client.post(endpoint).status_code, 405)
        self.session_type.assert_not_called()

    def test_ineligible_user_is_not_logged_in(self):
        self.create_token()
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]
        request = self.create_request("/auth/user")
        with patch.object(self.views, "authenticate", return_value=None), patch.object(self.views, "login") as login:
            self.assert_login_recovery(request, self.views.user(request), status=403)
        login.assert_not_called()

    def test_successful_refresh_updates_browser_access_token(self):
        token = self.create_token(expires=timezone.now() - timedelta(minutes=1))
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]
        request = self.create_request("/auth/user")
        with (
            patch.object(self.views, "authenticate", return_value=token.user),
            patch.object(self.views, "login"),
            patch.object(self.views, "render", return_value=HttpResponse()),
        ):
            self.assertEqual(self.views.user(request).status_code, 200)
        self.assertEqual(request.session["access_token"], "fixture-new-access")
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-new-access")

    def test_optional_avatar_uses_existing_default(self):
        self.assertEqual(
            self.views.OAuthUser({"id": "8901"}).avatar,
            "https://cdn.discordapp.com/embed/avatars/0.png",
        )

    def test_ambiguous_or_incomplete_callback_never_exchanges_a_code(self):
        for invalid_field in ("repeated-state", "repeated-code", "no-code", "empty-error"):
            with self.subTest(invalid_field=invalid_field):
                request = self.create_callback()
                request.GET = request.GET.copy()
                if invalid_field == "repeated-state":
                    request.GET.setlist("state", [request.GET["state"], request.GET["state"]])
                elif invalid_field == "repeated-code":
                    request.GET.setlist("code", ["fixture-code", "second-code"])
                elif invalid_field == "no-code":
                    request.GET.pop("code")
                else:
                    request.GET["error"] = ""
                self.assert_login_recovery(request, self.views.auth(request))
        self.session_type.assert_not_called()

    def test_real_backend_signs_in_username_with_missing_optional_profile_fields(self):
        identity = {"id": "8901", "username": "FixtureUsername", "global_name": None}
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=identity)),
            Mock(status_code=200, json=Mock(return_value=identity)),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]
        start = self.client.get("/auth/start")
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        response = self.client.get("/auth", {"state": state, "code": "fixture-code"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FixtureUsername")
        self.assertEqual(self.client.session["_auth_user_id"], "8901")
        account = DiscordUser.objects.get(id=8901)
        self.assertEqual(account.global_name, "FixtureUsername")
        self.assertEqual(account.avatar, "https://cdn.discordapp.com/embed/avatars/0.png")
        self.assertEqual(account.flags, 0)
        self.assertFalse(account.mfa_enabled)
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)

    def test_callback_write_failure_preserves_existing_token_and_consumes_attempt(self):
        token = self.create_token()
        request = self.create_callback()
        with patch.object(DiscordToken, "save", side_effect=DatabaseError("private-write-detail")):
            response = self.views.auth(request)
        self.assert_login_recovery(request, response, status=503)
        self.assertNotContains(response, "private-write-detail", status_code=503)
        self.assertFalse(Session.objects.filter(session_key=request.GET["state"]).exists())
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-old-access")
