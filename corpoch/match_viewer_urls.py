"""Additive routes for the staff match viewer."""

from django.urls import path

from corpoch import match_viewer_views


urlpatterns = [
    path("", match_viewer_views.match_viewer_index, name="match_viewer_index"),
    path("<str:match_id>/", match_viewer_views.match_viewer, name="match_viewer"),
    path("<str:match_id>/state/", match_viewer_views.match_viewer_state, name="match_viewer_state"),
]
