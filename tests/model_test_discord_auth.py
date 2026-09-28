"""Exercises real OAuth views and token models with Discord requests replaced."""

from contextlib import contextmanager
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from corpoch.models import DiscordToken, DiscordUser


urlpatterns = [path("auth/user", lambda request: HttpResponse(), name="user")]


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
        yield module


@override_settings(ROOT_URLCONF=__name__)
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

    def create_request(self, endpoint="/auth", parameters=None):
        """
        Creates a request carrying stale OAuth values and unrelated session data

        :param str endpoint: Requested path
        :param dict parameters: Query parameters
        :return: Request with an isolated session dictionary"""
        request = self.factory.get(endpoint, parameters or {})
        request.session = {
            "access_token": "fixture-old-access",
            "user_id": "8901",
            "viewer_preference": "dark",
        }
        return request

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

    def assert_login_redirect(self, request, response, cleared=True):
        """
        Checks recovery without discarding unrelated session preferences

        :param HttpRequest request: Request after view execution
        :param HttpResponse response: View response
        :param bool cleared: Whether stale OAuth fields should be absent"""
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.views.settings.AUTH_URL_DISCORD)
        self.assertEqual(request.session["viewer_preference"], "dark")
        if cleared:
            self.assertNotIn("access_token", request.session)
            self.assertNotIn("user_id", request.session)
        self.views.update_user.assert_not_called()

    def test_auth_without_code_redirects_without_discord_or_database_work(self):
        request = self.create_request()
        with self.assertNumQueries(0):
            response = self.views.auth(request)
        self.assert_login_redirect(request, response, cleared=False)
        self.assertEqual(request.session["access_token"], "fixture-old-access")
        self.session_type.assert_not_called()

    def test_denied_consent_clears_only_stale_oauth_session_values(self):
        request = self.create_request(parameters={"error": "access_denied"})
        self.assert_login_redirect(request, self.views.auth(request))
        self.session_type.assert_not_called()

    def test_rejected_callback_code_preserves_existing_stored_token(self):
        token = self.create_token()
        self.discord_session.post.return_value.status_code = 400
        self.discord_session.post.return_value.json.return_value = {"error": "invalid_grant"}
        request = self.create_request(parameters={"code": "fixture-code"})
        self.assert_login_redirect(request, self.views.auth(request))
        token.refresh_from_db()
        self.assertEqual(token.access_token, "fixture-old-access")
        self.discord_session.get.assert_not_called()

    def test_rejected_callback_identity_does_not_create_user_or_token(self):
        self.discord_session.get.return_value.status_code = 401
        request = self.create_request(parameters={"code": "fixture-code"})
        self.assert_login_redirect(request, self.views.auth(request))
        self.assertFalse(DiscordUser.objects.exists())
        self.assertFalse(DiscordToken.objects.exists())

    def test_valid_callback_creates_user_and_token_then_continues_login(self):
        request = self.create_request(parameters={"code": "fixture-code"})
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
        self.assertEqual(call.kwargs["data"]["redirect_uri"], self.views.settings.REDIRECT_URI)

    def test_valid_callback_updates_existing_token_without_duplicate_account(self):
        token = self.create_token()
        request = self.create_request(parameters={"code": "fixture-code"})
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
                self.assert_login_redirect(request, response)
        self.session_type.assert_not_called()

    def test_user_with_deleted_token_restarts_login(self):
        request = self.create_request("/auth/user")
        self.assert_login_redirect(request, self.views.user(request))
        self.session_type.assert_not_called()

    def test_user_with_missing_refresh_token_restarts_login(self):
        token = self.create_token()
        token.refresh_token = ""
        request = self.create_request("/auth/user")
        with patch.object(DiscordToken.objects, "get", return_value=token):
            self.assert_login_redirect(request, self.views.user(request))
        self.assertTrue(DiscordToken.objects.filter(pk=token.pk).exists())
        self.discord_session.get.assert_not_called()
        self.discord_session.post.assert_not_called()

    def test_user_with_rejected_identity_restarts_login(self):
        self.create_token()
        self.discord_session.get.return_value.status_code = 401
        request = self.create_request("/auth/user")
        self.assert_login_redirect(request, self.views.user(request))

    def test_user_with_rejected_guild_request_restarts_login(self):
        self.create_token()
        self.discord_session.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=self.identity)),
            Mock(status_code=403),
        ]
        request = self.create_request("/auth/user")
        self.assert_login_redirect(request, self.views.user(request))

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
