"""Scopes temporary HTTPS sharing to the existing synthetic DEV match."""

import logging
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

from staging.configuration import StagingConfigurationError
from staging.referee_check import RefereeAccessDenied, RefereeCheckError, RefereeTransport, validate_member, validate_roles


def validate_public_origin(value):
    """Requires one exact Cloudflare Quick Tunnel HTTPS origin.

    :param str value: Explicit temporary public origin
    :return: Validated origin without a trailing slash"""
    if not isinstance(value, str) or re.fullmatch(
        r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com/?", value,
    ) is None:
        raise StagingConfigurationError("Paste only the HTTPS trycloudflare.com address printed by this test tunnel.")
    return value.rstrip("/")


def build_shared_settings(configuration, public_origin):
    """Builds explicit sharing overrides without changing local configuration.

    :param WebConfiguration configuration: Validated existing local settings
    :param str public_origin: Validated temporary HTTPS origin
    :return: Overrides for the separate foreground web process"""
    origin = validate_public_origin(public_origin)
    hostname = urlsplit(origin).hostname
    return {
        "BASE_URL": hostname,
        "ALLOWED_HOSTS": [hostname],
        "CSRF_TRUSTED_ORIGINS": [origin],
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
        "SESSION_COOKIE_NAME": "__Host-corpo_shared_session",
        "CSRF_COOKIE_NAME": "__Host-corpo_shared_csrf",
        "SESSION_COOKIE_SECURE": True,
        "CSRF_COOKIE_SECURE": True,
        "SESSION_COOKIE_DOMAIN": None,
        "CSRF_COOKIE_DOMAIN": None,
        "SESSION_COOKIE_PATH": "/",
        "CSRF_COOKIE_PATH": "/",
        "SESSION_COOKIE_AGE": 3600,
        "SECURE_REFERRER_POLICY": "no-referrer",
        "ROOT_URLCONF": "staging.shared_urls",
        "REDIRECT_URI": origin + "/auth",
        "AUTH_URL_DISCORD": "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": configuration.bot_id, "response_type": "code",
            "redirect_uri": origin + "/auth", "scope": "identify guilds",
        }),
        "MATCH_VIEWER_ENABLED": True,
        "MATCH_VIEWER_MYSQL_VERIFIED": True,
        "MATCH_VIEWER_POLLING_ENABLED": True,
    }


class SharedAccessError(ValueError):
    """Carries only a fixed public denial and its HTTP status."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class SharedRefereeAccess:
    """Rechecks DEV roles with a small, bounded process-local permission cache."""

    def __init__(self, bot_token, expected_bot_id, snapshot):
        self.log = logging.getLogger(__name__)
        self.bot_token = bot_token
        self.bot_id = str(expected_bot_id)
        self.guild_id = str(snapshot["guild_id"])
        self.role_ids = tuple(str(value) for value in snapshot["role_ids"])
        self.entries = {}
        self.lock = threading.Lock()

    def __repr__(self):
        return "SharedRefereeAccess()"

    def verify(self, account_id):
        """Checks the current member or a result no older than ten seconds.

        :param int account_id: Account authenticated through Discord OAuth"""
        if type(account_id) is not int or not 0 < account_id < 2 ** 63:
            raise SharedAccessError("Sign in with your DEV referee account.", 401)
        with self.lock:
            now = time.monotonic()
            cached = self.entries.get(account_id)
            if cached is not None and now < cached[0]:
                if cached[1] is not None:
                    raise SharedAccessError(*cached[1])
                return
            self.entries = {key: value for key, value in self.entries.items() if now < value[0]}
            if len(self.entries) >= 20:
                raise SharedAccessError("This small DEV session is busy. Try again shortly.", 503)
            try:
                self.read_member(account_id)
            except SharedAccessError as error:
                self.entries[account_id] = (time.monotonic() + 5, (str(error), error.status))
                raise
            self.entries[account_id] = (time.monotonic() + 10, None)

    def read_member(self, account_id):
        """Fetches only pinned bot, role and member metadata without retries.

        :param int account_id: Authenticated human account to verify"""
        transport = RefereeTransport(self.guild_id, str(account_id))
        try:
            identity = transport.get_json("https://discord.com/api/v10/users/@me", self.bot_token)
            if not isinstance(identity, dict) or identity.get("id") != self.bot_id or identity.get("bot") is not True:
                raise SharedAccessError("The DEV identity check failed. Viewing is temporarily unavailable.", 503)
            validate_roles(transport.get_json(
                f"https://discord.com/api/v10/guilds/{self.guild_id}/roles", self.bot_token,
            ), self.role_ids)
            try:
                member = transport.get_json(
                    f"https://discord.com/api/v10/guilds/{self.guild_id}/members/{account_id}", self.bot_token,
                )
            except RefereeCheckError as error:
                if error.status_code == 404:
                    raise SharedAccessError("An approved DEV referee role is required.", 403) from None
                raise
            try:
                validate_member(member, str(account_id), self.role_ids)
            except RefereeAccessDenied:
                raise SharedAccessError("An approved DEV referee role is required.", 403) from None
        except RefereeCheckError:
            raise SharedAccessError("Discord membership could not be verified. Try again shortly.", 503) from None
        finally:
            try:
                transport.close()
            except RefereeCheckError:
                pass


class SharedRefereeMiddleware:
    """Applies fresh DEV membership before any match page or fragment read."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.db import DatabaseError
        from corpoch.match_viewer_views import viewer_error
        from corpoch.models import DiscordToken, DiscordUser
        from staging.discord_fixture import grant_shared_referee, revoke_shared_referee

        if request.path.startswith("/match-viewer/") and request.user.is_authenticated:
            fragment = request.path.endswith("/state/")
            if not request.user.is_active:
                return viewer_error(request, "Your account does not have access to this match.", 403, fragment)
            access = settings.SHARED_DEV_ACCESS
            try:
                if not (DiscordUser.objects.filter(pk=request.user.pk, is_active=True).exists()
                        and DiscordToken.objects.filter(user_id=request.user.pk).exists()):
                    return viewer_error(request, "Sign in with your DEV referee account.", 403, fragment)
                access.verify(request.user.pk)
                grant_shared_referee(request.user.pk, int(access.guild_id))
            except SharedAccessError as error:
                if error.status == 403:
                    try:
                        revoke_shared_referee(request.user.pk, int(access.guild_id))
                    except (StagingConfigurationError, DatabaseError):
                        return viewer_error(request, "The owned DEV sample is unavailable.", 503, fragment)
                return viewer_error(request, str(error), error.status, fragment)
            except (StagingConfigurationError, DatabaseError):
                return viewer_error(request, "The owned DEV sample is unavailable.", 503, fragment)
        return self.get_response(request)


class SharedSurface:
    """Rejects other hosts, schemes, paths and methods before Django or static files."""

    def __init__(self, application, public_origin):
        self.application = application
        self.hostname = urlsplit(validate_public_origin(public_origin)).hostname.encode("ascii")
        self.paths = {
            "/", "/home", "/auth/start", "/auth", "/auth/user", "/match-viewer/",
            "/match-viewer/local-viewer-pilot/", "/match-viewer/local-viewer-pilot/state/",
            "/static/corpoch/match_viewer.css", "/static/corpoch/match_viewer.js",
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers", [])
        hosts = [value for key, value in headers if key.lower() == b"host"]
        protocols = [value for key, value in headers if key.lower() == b"x-forwarded-proto"]
        allowed = (
            hosts == [self.hostname] and protocols == [b"https"]
            and scope.get("path") in self.paths and scope.get("method") == "GET"
        )
        if not allowed:
            await send({"type": "http.response.start", "status": 404, "headers": [
                (b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store"),
            ]})
            await send({"type": "http.response.body", "body": b"This route is unavailable."})
            return

        async def send_private(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() not in {b"cache-control", b"referrer-policy"}
                ] + [(b"cache-control", b"private, no-store, max-age=0"), (b"referrer-policy", b"no-referrer")]
            await send(message)

        await self.application(scope, receive, send_private)
