"""Isolated viewer routes without the legacy admin or service imports."""

from django.urls import include, path


urlpatterns = [path("match-viewer/", include("corpoch.match_viewer_urls"))]
