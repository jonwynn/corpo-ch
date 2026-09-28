"""Creates and consumes browser-bound Discord authorization attempts."""

from hashlib import sha256
import re
from secrets import compare_digest
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone


def validate_discord_authorization_url():
    """
    Checks the configured authorization destination before adding browser state

    :return: Parsed URL and its unchanged authorization parameters"""
    if settings.SESSION_ENGINE != "django.contrib.sessions.backends.db":
        raise ImproperlyConfigured("Discord login requires database-backed sessions.")
    try:
        destination = urlsplit(settings.AUTH_URL_DISCORD or "")
        parameters = parse_qsl(destination.query, keep_blank_values=True)
        valid_origin = (
            destination.scheme == "https"
            and destination.hostname == "discord.com"
            and destination.port in (None, 443)
            and destination.username is None
            and destination.password is None
            and destination.path in {"/oauth2/authorize", "/api/oauth2/authorize"}
            and not destination.fragment
        )
    except (TypeError, ValueError) as error:
        raise ImproperlyConfigured("Invalid Discord authorization configuration.") from error
    required = {
        "client_id": str(settings.BOT_ID),
        "response_type": "code",
        "redirect_uri": settings.REDIRECT_URI,
    }
    if not valid_origin or any(
        [value for key, value in parameters if key == name] != [expected]
        for name, expected in required.items()
    ):
        raise ImproperlyConfigured("Invalid Discord authorization configuration.")
    scopes = [value for key, value in parameters if key == "scope"]
    if len(scopes) != 1 or not {"identify", "guilds"}.issubset(scopes[0].split()):
        raise ImproperlyConfigured("Discord login requires identify and guilds scopes.")
    return destination, parameters


def load_discord_attempt(request, nonce):
    """
    Loads a still-valid authorization attempt belonging to this browser session

    :param HttpRequest request: Current browser request
    :param str nonce: Authorization attempt identifier
    :return: Matching session record or None"""
    if (
        settings.SESSION_ENGINE != "django.contrib.sessions.backends.db"
        or not request.session.session_key
        or not isinstance(nonce, str)
        or re.fullmatch(r"[a-z0-9]{32}", nonce) is None
    ):
        return None
    attempt = Session.objects.filter(
        session_key=nonce,
        expire_date__gt=timezone.now(),
    ).first()
    if attempt is None:
        return None
    data = attempt.get_decoded()
    expected_binding = sha256(request.session.session_key.encode("utf-8")).hexdigest()
    binding = data.get("browser_binding")
    issued_at = data.get("issued_at")
    if (
        data.get("purpose") != "discord_oauth"
        or not isinstance(binding, str)
        or re.fullmatch(r"[a-f0-9]{64}", binding) is None
        or not compare_digest(binding, expected_binding)
        or not isinstance(issued_at, (int, float))
        or not 0 <= timezone.now().timestamp() - issued_at <= 600
    ):
        return None
    return attempt


def discard_discord_attempt(request):
    """
    Removes the current browser's pending authorization attempt

    :param HttpRequest request: Current browser request"""
    nonce = request.session.pop("discord_oauth_state", None)
    attempt = load_discord_attempt(request, nonce)
    if attempt is not None:
        attempt.delete()


def create_discord_authorization_url(request):
    """
    Creates one expiring authorization attempt without exposing a browser cookie

    :param HttpRequest request: Browser starting Discord authorization
    :return: Validated Discord URL with its new state parameter"""
    destination, parameters = validate_discord_authorization_url()
    discard_discord_attempt(request)
    if request.session.session_key is None:
        request.session.save()
    attempt = SessionStore()
    attempt["purpose"] = "discord_oauth"
    attempt["browser_binding"] = sha256(
        request.session.session_key.encode("utf-8"),
    ).hexdigest()
    attempt["issued_at"] = timezone.now().timestamp()
    attempt.set_expiry(600)
    attempt.save()
    request.session["discord_oauth_state"] = attempt.session_key
    parameters = [(key, value) for key, value in parameters if key != "state"]
    parameters.append(("state", attempt.session_key))
    return urlunsplit(destination._replace(query=urlencode(parameters)))


def consume_discord_attempt(request, nonce):
    """
    Atomically consumes a matching attempt before any Discord request

    :param HttpRequest request: Authorization callback request
    :param str nonce: State returned by Discord
    :return: Whether this callback alone consumed the valid attempt"""
    expected_nonce = request.session.pop("discord_oauth_state", None)
    if (
        not isinstance(nonce, str)
        or not isinstance(expected_nonce, str)
        or re.fullmatch(r"[a-z0-9]{32}", nonce) is None
        or re.fullmatch(r"[a-z0-9]{32}", expected_nonce) is None
        or not compare_digest(nonce, expected_nonce)
    ):
        return False
    attempt = load_discord_attempt(request, nonce)
    if attempt is None:
        return False
    # An auxiliary session row makes consumption atomic even when two requests
    # have already loaded the same browser session. Cookie data alone cannot.
    deleted, unused_breakdown = Session.objects.filter(
        session_key=attempt.session_key,
        session_data=attempt.session_data,
        expire_date=attempt.expire_date,
    ).delete()
    return deleted == 1
