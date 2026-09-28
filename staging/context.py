"""Supplies local checkpoint labels without exposing private configuration."""

from django.conf import settings


def viewer_checkpoint(request):
    """
    Reports the active local viewer mode to the staging templates

    :param HttpRequest request: Current local request
    :return: Public checkpoint labels"""
    return {
        "staging_viewer_enabled": settings.MATCH_VIEWER_ENABLED is True,
        "staging_viewer_polling": settings.MATCH_VIEWER_POLLING_ENABLED is True,
    }
