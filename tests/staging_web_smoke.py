"""Loads the real web runtime in a fresh guarded process without database I/O."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from staging.configuration import validate_configuration
from staging.runtime import configure_web_runtime, create_request_handler, create_static_handler
from tests.test_staging_configuration import create_configuration_values
from tests.viewer_test_bootstrap import BlockedTestOperation, ViewerTestEnvironment


def run_smoke():
    """
    Checks real Django setup, imports, routing and rendering without native connections

    :return: Process exit status"""
    checks = unittest.TestCase()
    with tempfile.TemporaryDirectory(prefix="corpo-staging-web-smoke-") as directory:
        with ViewerTestEnvironment(directory, allow_models=True) as guard:
            # This smoke must exercise the real selected settings without the
            # model bootstrap's compatibility alias masking deployment imports.
            sys.modules.pop("corpoch.settings", None)
            # The actual Django MySQL backend may be imported, but its native
            # connection entry points are replaced before Django setup begins.
            guard.import_guard.blocked_modules = tuple(
                name for name in guard.import_guard.blocked_modules
                if name not in {"MySQLdb", "corpoch.dbot.tasks"}
            )
            import MySQLdb

            values = create_configuration_values()
            configuration = validate_configuration(values, Path(directory), values["bot_id"])
            platform_guard = patch("platform.system", return_value="Windows") if sys.platform == "win32" else nullcontext()
            with (
                patch.object(MySQLdb, "connect", side_effect=BlockedTestOperation("Native MySQL connections are blocked.")) as connect,
                patch.object(MySQLdb, "Connect", side_effect=BlockedTestOperation("Native MySQL connections are blocked.")),
                platform_guard,
            ):
                configure_web_runtime(configuration)
                import django
                from django.conf import settings
                from django.db import connection
                from django.test import Client, RequestFactory

                django.setup()
                checks.assertEqual(connection.vendor, "mysql")
                checks.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
                checks.assertEqual(connection.settings_dict["PORT"], 3308)
                checks.assertFalse(settings.DISCORD_PROFILE_SYNC_ENABLED)
                client = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1:8766")
                with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                    response = client.get("/home")
                    checks.assertEqual(response.status_code, 200)
                    checks.assertIn(b"Corpo CH local staging", response.content)
                    for route in ("/match-viewer/", "/admin/", "/api/", "/livematches/", "/unserved-private-media/key.json"):
                        checks.assertEqual(client.get(route).status_code, 404)
                    static = create_static_handler(lambda environment, start_response: [])
                    for route, expected in (("/static/corpoch/match_viewer.css", 200), ("/static/../../staging/configuration.py", 404)):
                        response = static.get_response(RequestFactory().get(route, HTTP_HOST="127.0.0.1:8766"))
                        checks.assertEqual(response.status_code, expected)
                        response.close()
                    handler = object.__new__(create_request_handler())
                    handler.log_message("GET /auth?code=%s", "fixture-sensitive-code")
                    handler.get_stderr().write("fixture-sensitive-code")
                checks.assertNotIn("fixture-sensitive-code", output.getvalue() + errors.getvalue())
                for module in ("corpoch.settings", "corpoch.providers", "corpoch.tasks", "corpoch.dbot.bot", "corpoch.dbot.settings"):
                    checks.assertNotIn(module, sys.modules)
                connect.assert_not_called()
    print("PASS: Fresh web runtime setup, local templates, restricted routes, static files and private logging passed.")
    print("No database connection, network request, worker, bot or private credential was used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_smoke())
