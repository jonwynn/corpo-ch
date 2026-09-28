"""Runs the restricted local web process and verifies its database boundary."""

import importlib.abc
import io
import os
import sys

from staging.configuration import StagingConfigurationError
from staging.settings import build_web_settings


class WebImportGuard(importlib.abc.MetaPathFinder):
    """Rejects deployment configuration and outbound service implementations."""

    def find_spec(self, fullname, path=None, target=None):
        """
        Blocks imports outside the local web-only boundary

        :param str fullname: Requested module name
        :param object path: Import search path
        :param object target: Existing module during reload"""
        blocked = (
            "corpoch.settings", "corpoch.providers", "corpoch.tasks",
            "corpoch.dbot.settings", "corpoch.dbot.bot", "corpoch.dbot.launcher",
            "corpoch.chdedi", "dotenv",
        )
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ImportError("This module is unavailable in the local web-only runtime.")
        return None


def configure_web_runtime(configuration):
    """
    Configures Django before importing any application or deployment module

    :param WebConfiguration configuration: Validated private configuration"""
    from django.conf import settings

    if settings.configured or any(name == "corpoch" or name.startswith("corpoch.") for name in sys.modules):
        raise StagingConfigurationError("Start the local web launcher in a fresh Python process.")
    preserved = {
        name: value for name, value in os.environ.items()
        if name in {"PATH", "HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR"}
    }
    os.environ.clear()
    os.environ.update(preserved)
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "staging.settings",
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(configuration.runtime_root / "tmp"),
    })
    sys.dont_write_bytecode = True
    sys.meta_path.insert(0, WebImportGuard())
    settings.configure(**build_web_settings(configuration))


def validate_database_grants(grants, configuration, literal_database_names=False):
    """
    Requires privileges to be confined to the single selected staging schema

    :param list grants: SHOW GRANTS rows returned by the selected MySQL account
    :param WebConfiguration configuration: Validated private configuration
    :param bool literal_database_names: Whether MySQL partial_revokes makes names literal"""
    grantees = {
        f"`{configuration.database_user}`@`127.0.0.1`",
        f"'{configuration.database_user}'@'127.0.0.1'",
    }
    database = configuration.database_name if literal_database_names else configuration.database_name.replace("_", "\\_")
    allowed = {"GRANT USAGE ON *.*", f"GRANT ALL PRIVILEGES ON `{database}`.*"}
    seen = set()
    for row in grants:
        if not isinstance(row, (tuple, list)) or len(row) != 1 or not isinstance(row[0], str):
            raise StagingConfigurationError("The staging database grants could not be verified.")
        components = row[0].split(" TO ")
        if len(components) != 2 or components[0] not in allowed or components[1] not in grantees:
            raise StagingConfigurationError("The staging database account has unexpected privileges.")
        seen.add(components[0])
    if f"GRANT ALL PRIVILEGES ON `{database}`.*" not in seen:
        raise StagingConfigurationError("The staging database account is missing its schema privileges.")


def validate_database_boundary(connection, configuration):
    """
    Checks the selected MySQL identity and provisioned ownership marker

    :param DatabaseWrapper connection: Selected Django database connection
    :param WebConfiguration configuration: Validated private configuration"""
    if connection.vendor != "mysql":
        raise StagingConfigurationError("The local web runtime requires its dedicated MySQL database.")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE(), CURRENT_USER(), @@port, @@global.partial_revokes")
            database, account, port, partial_revokes = cursor.fetchone()
            if (
                database != configuration.database_name
                or account != f"{configuration.database_user}@127.0.0.1"
                or port != configuration.database_port
                or partial_revokes not in (0, 1)
            ):
                raise StagingConfigurationError("The connected database does not match this staging instance.")
            cursor.execute("SHOW GRANTS")
            validate_database_grants(cursor.fetchall(), configuration, bool(partial_revokes))
            cursor.execute("SELECT id, purpose, bot_id FROM corpo_staging_runtime ORDER BY id")
            if list(cursor.fetchall()) != [(1, "corpo-ch-web-staging-v1", configuration.bot_id)]:
                raise StagingConfigurationError("The database is missing the expected staging ownership marker.")
    except StagingConfigurationError:
        raise
    except Exception:
        raise StagingConfigurationError("The dedicated staging database could not be verified.") from None
    finally:
        connection.close()


class DiscardedRequestErrors:
    """Discards WSGI diagnostics that could contain a callback query string."""

    def write(self, message):
        """Discards diagnostic text and returns its length."""
        return len(message)

    def flush(self):
        """Keeps the file-like diagnostic interface without buffered content."""


def create_request_handler():
    """Creates the request handler without exposing request URLs in logs."""
    from django.core.servers.basehttp import WSGIRequestHandler

    class LocalRequestHandler(WSGIRequestHandler):
        """Omits access lines and raw WSGI exception diagnostics."""

        timeout = 10

        def handle(self):
            """Closes inactive browser connections without logging their contents."""
            try:
                super().handle()
            except TimeoutError:
                self.close_connection = True

        def log_message(self, format_string, *arguments):
            """Discards request logging, including OAuth callback values."""

        def get_stderr(self):
            """Returns a sink for WSGI diagnostic output."""
            return DiscardedRequestErrors()

    return LocalRequestHandler


def create_static_handler(application):
    """
    Wraps local application assets with a safe missing-file response

    :param object application: Configured Django WSGI application
    :return: Static asset and application handler"""
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.exceptions import SuspiciousFileOperation
    from django.http import Http404

    class LocalStaticHandler(StaticFilesHandler):
        """Returns a controlled response for invalid static paths."""

        def serve(self, request):
            """Serves an application asset without exposing rejected filesystem paths."""
            try:
                return super().serve(request)
            except SuspiciousFileOperation:
                raise Http404("Static asset unavailable.") from None

    return LocalStaticHandler(application)


def create_web_server(port):
    """
    Creates a threaded loopback server with per-thread database cleanup

    :param int port: Local listening port, or zero for an isolated socket check
    :return: Local Django HTTP server"""
    from django.core.servers.basehttp import ThreadedWSGIServer

    class LocalWebServer(ThreadedWSGIServer):
        """Contains request failure logging within the local web process."""

        def handle_error(self, request, client_address):
            """Reports request failure without private request details."""
            print("A local web request failed. Retry from the home page.", flush=True)

    return LocalWebServer(("127.0.0.1", port), create_request_handler())


def serve_web(configuration):
    """
    Serves local static assets and the application without autoreload

    :param WebConfiguration configuration: Validated private configuration"""
    from django.core.wsgi import get_wsgi_application

    with create_web_server(configuration.browser_port) as server:
        server.set_app(create_static_handler(get_wsgi_application()))
        print(f"Local web staging is available at http://127.0.0.1:{configuration.browser_port}/home", flush=True)
        from django.conf import settings

        if settings.MATCH_VIEWER_ENABLED:
            refresh = "automatic updates" if settings.MATCH_VIEWER_POLLING_ENABLED else "manual refresh"
            print(f"The isolated sample match is available with {refresh}.", flush=True)
        else:
            print("Match viewer gates are off.", flush=True)
        print("Bot, workers and spreadsheet exports are not running.", flush=True)
        server.serve_forever()


def run_command(configuration, command):
    """
    Runs one fixed management operation or the local web server

    :param WebConfiguration configuration: Validated private configuration
    :param str command: Explicit management or local web checkpoint"""
    if command not in {"check", "migrate", "serve", "serve-viewer", "serve-viewer-live"}:
        raise StagingConfigurationError("This command is unavailable in the local web runtime.")
    configure_web_runtime(configuration)
    import django
    from django.core.management import call_command
    from django.db import connection

    django.setup()
    validate_database_boundary(connection, configuration)
    if command in {"serve-viewer", "serve-viewer-live"}:
        from django.conf import settings
        from staging.viewer_fixture import validate_fixture

        validate_fixture()
        settings.MATCH_VIEWER_ENABLED = True
        settings.MATCH_VIEWER_MYSQL_VERIFIED = True
        settings.MATCH_VIEWER_POLLING_ENABLED = command == "serve-viewer-live"
    if command in {"serve", "serve-viewer", "serve-viewer-live"}:
        serve_web(configuration)
        return
    with io.StringIO() as output:
        if command == "migrate":
            call_command("migrate", interactive=False, verbosity=0, stdout=output, stderr=output)
        else:
            call_command("check", verbosity=0, stdout=output, stderr=output)
    connection.close()
    print(f"PASS: Local staging {command} completed. No bot or worker was started.")
