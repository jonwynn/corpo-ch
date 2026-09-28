"""Read-only staff pages and fragments for one recorded match."""

import json

from django.conf import settings
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_cache_control, patch_vary_headers

from corpoch.match_viewer import build_match_presentation
from corpoch.match_viewer_reader import ViewerReadError, discover_matches, read_match_snapshot


def viewer_response(response):
    """
    Prevents staff match content from entering shared or persistent caches

    :param HttpResponse response: Viewer response
    :return: Response with privacy headers"""
    patch_cache_control(response, no_store=True, private=True, max_age=0)
    patch_vary_headers(response, ["Cookie"])
    response["X-Content-Type-Options"] = "nosniff"
    return response


def viewer_error(request, message, status, fragment=False):
    """
    Renders a controlled error without redirects or private exception details

    :param HttpRequest request: Viewer request
    :param str message: Safe explanation
    :param int status: HTTP status
    :param bool fragment: Whether only replacement content is requested
    :return: Controlled response"""
    template = "match_viewer/error_state.html" if fragment else "match_viewer/error.html"
    context = {"error_message": message, "error_status": status}
    return viewer_response(render(request, template, context, status=status))


def check_viewer_request(request, fragment=False):
    """
    Enforces the default-off gate and a GET-only interface

    :param HttpRequest request: Viewer request
    :param bool fragment: Whether a fragment was requested
    :return: Rejection response or None"""
    if getattr(settings, "MATCH_VIEWER_ENABLED", False) is not True:
        return viewer_error(request, "Match viewing is not enabled.", 404, fragment)
    if request.method != "GET":
        response = viewer_error(request, "Use a read-only GET request.", 405, fragment)
        response["Allow"] = "GET"
        return response
    return None


def request_account_id(request):
    """
    Uses the session only to identify the account to reload

    :param HttpRequest request: Viewer request
    :return: Account identifier or None"""
    account = getattr(request, "user", None)
    return account.pk if account is not None and account.is_authenticated else None


def match_viewer_index(request):
    """
    Lists the requesting staff member's authorized matches

    :param HttpRequest request: Viewer request
    :return: Scoped selection page"""
    rejection = check_viewer_request(request)
    if rejection is not None:
        return rejection
    try:
        context = discover_matches(request_account_id(request), request.GET.get("page", 1))
    except ViewerReadError as error:
        return viewer_error(request, str(error), error.status)
    except DatabaseError:
        return viewer_error(request, "Match data is temporarily unavailable. Try again shortly.", 503)
    return viewer_response(render(request, "match_viewer/index.html", context))


def render_match(request, match_id, fragment=False):
    """
    Serves the same validated presentation for the page and fragment

    :param HttpRequest request: Viewer request
    :param str match_id: Selected stored identifier
    :param bool fragment: Whether to render only the match region
    :return: Staff-only page or fragment"""
    rejection = check_viewer_request(request, fragment)
    if rejection is not None:
        return rejection
    try:
        pins = None
        if request.GET.get("pins"):
            if len(request.GET["pins"]) > 2048:
                raise ViewerReadError("Player assignment parameters are invalid.", 400)
            try:
                pins = json.loads(request.GET["pins"])
            except (ValueError, TypeError) as error:
                raise ViewerReadError("Player assignment parameters are invalid.", 400) from error
        source = read_match_snapshot(request_account_id(request), match_id)
        viewer = build_match_presentation(source, pins)
    except ViewerReadError as error:
        return viewer_error(request, str(error), error.status, fragment)
    except DatabaseError:
        return viewer_error(request, "Match data is temporarily unavailable. Try again shortly.", 503, fragment)
    polling = getattr(settings, "MATCH_VIEWER_POLLING_ENABLED", False) is True
    selected_theme = request.GET.get("theme", "dark")
    if selected_theme not in {"dark", "light", "system"}:
        selected_theme = "dark"
    context = {
        "viewer": viewer,
        "selected_theme": selected_theme,
        "polling_enabled": polling,
        "current_state_url": reverse("match_viewer_state", kwargs={"match_id": match_id}),
        "state_url": reverse("match_viewer_state", kwargs={"match_id": match_id}) if polling else None,
        "connection_label": "Recorded match state" if polling else "Automatic updates are off · reload to refresh",
    }
    template = "match_viewer/state.html" if fragment else "match_viewer/page.html"
    return viewer_response(render(request, template, context))


def match_viewer(request, match_id):
    """
    Displays one selected match

    :param HttpRequest request: Viewer request
    :param str match_id: Stored match identifier
    :return: Read-only match page"""
    return render_match(request, match_id)


def match_viewer_state(request, match_id):
    """
    Returns the selected match fragment without an authentication redirect

    :param HttpRequest request: Viewer request
    :param str match_id: Stored match identifier
    :return: Read-only match fragment"""
    return render_match(request, match_id, fragment=True)
