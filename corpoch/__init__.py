from .celery import app as celery_app

__all__ = ['celery_app']
__version__ = '1.7.0-beta.1'
__name__ = "corpoch"
__title__ = 'Corpo CH'
__url__ = 'https://github.com/Jetsurf/corpo-ch'
__user_agent__ = f'{__title__} - v{__version__} - <{__url__}>'
NAME = f'{__title__} v{__version__}'
