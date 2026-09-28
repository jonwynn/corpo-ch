"""Exposes only the local login and read-only viewer routes."""

from django.urls import include, path

from corpoch import views


urlpatterns = [
    path("", views.null),
    path("home", views.home, name="home"),
    path("auth/start", views.auth_start, name="discord_auth_start"),
    path("auth", views.auth),
    path("auth/user", views.user, name="user"),
    path("match-viewer/", include("corpoch.match_viewer_urls")),
]
