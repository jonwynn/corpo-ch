"""Exposes OAuth and the single owned sample during temporary sharing."""

from django.http import HttpResponseRedirect
from django.urls import path

from corpoch import views
from corpoch.match_viewer_views import match_viewer, match_viewer_state


def sample_index(request):
    """Keeps the match selector limited to the one synthetic sample."""
    return HttpResponseRedirect("/match-viewer/local-viewer-pilot/")


def shared_user(request):
    """Completes the existing login before opening the one shared sample."""
    response = views.user(request)
    if response.status_code == 200 and request.user.is_authenticated:
        return views.discord_auth_response(sample_index(request))
    return response


urlpatterns = [
    path("", views.null),
    path("home", views.home, name="home"),
    path("auth/start", views.auth_start, name="discord_auth_start"),
    path("auth", views.auth),
    path("auth/user", shared_user, name="user"),
    path("match-viewer/", sample_index, name="match_viewer_index"),
    path("match-viewer/<str:match_id>/", match_viewer, name="match_viewer"),
    path("match-viewer/<str:match_id>/state/", match_viewer_state, name="match_viewer_state"),
]
