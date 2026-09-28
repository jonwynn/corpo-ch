"""Exercises the local web surface with real Django requests and mocked OAuth."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.test import RequestFactory, TestCase
from django.urls import clear_url_caches, resolve, Resolver404

import corpoch
from corpoch.models import DiscordUser
from staging.configuration import validate_configuration
from staging.runtime import create_request_handler, create_static_handler, serve_web
from staging.settings import build_web_settings
from tests.model_test_discord_auth import load_auth_views
from tests.test_staging_configuration import create_configuration_values


class StagingWebTests(TestCase):
    """Uses the staging URLs, templates and settings without a service connection."""

    def setUp(self):
        values = create_configuration_values()
        self.configuration = validate_configuration(values, Path("/private/staging"), values["bot_id"])
        web_settings = build_web_settings(self.configuration)
        self.views = self.enterContext(load_auth_views())
        source = Path(__file__).parents[1] / "staging" / "urls.py"
        specification = importlib.util.spec_from_file_location("isolated_staging_urls", source)
        self.urls = importlib.util.module_from_spec(specification)
        with (
            patch.dict(sys.modules, {"corpoch.views": self.views}),
            patch.object(corpoch, "views", self.views, create=True),
        ):
            specification.loader.exec_module(self.urls)
        selected = {name: web_settings[name] for name in (
            "TEMPLATES", "MIDDLEWARE", "AUTHENTICATION_BACKENDS", "BOT_ID", "BOT_SECRET",
            "AUTH_URL_DISCORD", "REDIRECT_URI", "DISCORD_PROFILE_SYNC_ENABLED",
            "MATCH_VIEWER_ENABLED", "MATCH_VIEWER_POLLING_ENABLED", "MATCH_VIEWER_MYSQL_VERIFIED",
            "STATIC_URL", "DEBUG",
        )}
        selected["ROOT_URLCONF"] = self.urls
        self.enterContext(self.settings(**selected))
        clear_url_caches()
        self.addCleanup(clear_url_caches)

    def test_web_surface_omits_admin_api_legacy_overlay_and_media(self):
        for path in ("/admin/", "/api/", "/livematches/", "/update-live-matches/", "/unserved-private-media/key.json"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
        for path in ("/", "/home", "/auth", "/auth/start", "/auth/user", "/match-viewer/"):
            self.assertIsNotNone(resolve(path))

    def test_home_uses_local_template_without_production_links_or_remote_assets(self):
        response = self.client.get("/home")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corpo CH local staging")
        self.assertContains(response, 'href="/auth/start"')
        self.assertContains(response, '/static/corpoch/match_viewer.css')
        self.assertContains(response, '/static/corpoch/match_viewer.js')
        self.assertContains(response, 'id="match-viewer-theme"')
        self.assertContains(response, '<option value="system">System</option>')
        for content in ("cdn.discordapp.com", "ajax.googleapis.com", "cdnjs.cloudflare.com", 'href="/admin/', 'href="/api/'):
            self.assertNotContains(response, content)

    def test_staging_oauth_completes_without_profile_job_or_automatic_access(self):
        identity = {"id": "89001234567890124", "username": "Fixture Referee", "global_name": None}
        with patch("corpoch.models.misc.Session") as session_type:
            session = session_type.return_value
            session.post.return_value = Mock(status_code=200, json=Mock(return_value={
                "access_token": "fixture-access", "refresh_token": "fixture-refresh",
                "scope": "identify guilds", "expires_in": 3600,
            }))
            session.get.side_effect = [
                Mock(status_code=200, json=Mock(return_value=identity)),
                Mock(status_code=200, json=Mock(return_value=identity)),
                Mock(status_code=200, json=Mock(return_value=[])),
            ]
            start = self.client.get("/auth/start")
            state = parse_qs(urlsplit(start.url).query)["state"][0]
            response = self.client.get("/auth", {"state": state, "code": "fixture-code"}, follow=True)
        self.assertContains(response, "Discord sign-in verified")
        self.assertContains(response, "Fixture Referee")
        self.assertNotContains(response, identity["id"])
        self.assertNotContains(response, "cdn.discordapp.com")
        account = DiscordUser.objects.get(pk=identity["id"])
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertFalse(account.guilds_referee.exists())
        self.assertFalse(account.guilds_admin.exists())
        self.views.update_user.assert_not_called()
        self.assertEqual(self.client.session["_auth_user_id"], identity["id"])
        self.assertEqual(self.client.get("/match-viewer/").status_code, 404)

    def test_callback_and_wsgi_logs_do_not_emit_query_values(self):
        handler_type = create_request_handler()
        handler = object.__new__(handler_type)
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            handler.log_message('GET /auth?code=%s', "fixture-sensitive-code")
            handler.get_stderr().write("private callback failure")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")

    def test_static_handler_serves_local_css_and_rejects_traversal(self):
        with self.settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "django.contrib.staticfiles"]):
            handler = create_static_handler(lambda environment, start_response: [])
            factory = RequestFactory()
            for path, expected in (("/static/corpoch/match_viewer.css", 200), ("/static/../../staging/configuration.py", 404)):
                with self.subTest(path=path):
                    response = handler.get_response(factory.get(path))
                    self.assertEqual(response.status_code, expected)
                    response.close()

    def test_server_binds_only_loopback_and_uses_the_static_wsgi_wrapper(self):
        from django.contrib.staticfiles.handlers import StaticFilesHandler

        with (
            patch("django.core.servers.basehttp.WSGIServer.__init__", return_value=None) as initialize,
            patch("django.core.servers.basehttp.WSGIServer.set_app") as set_app,
            patch("django.core.servers.basehttp.WSGIServer.serve_forever") as serve_forever,
            patch("django.core.servers.basehttp.WSGIServer.server_close"),
            patch("django.core.wsgi.get_wsgi_application", return_value=Mock()),
            redirect_stdout(io.StringIO()),
        ):
            serve_web(self.configuration)
        initialize.assert_called_once()
        self.assertEqual(initialize.call_args.args[0], ("127.0.0.1", 8766))
        self.assertIsInstance(set_app.call_args.args[0], StaticFilesHandler)
        serve_forever.assert_called_once_with()
