from .misc import GSheetAPIAdmin
from .charts import ChartAdmin, BYOSChartAdmin
from .tournament import TournamentAdmin, BracketAdmin, GroupAdmin, TournamentPlayerAdmin, QualifierAdmin, QualifierSubmissionAdmin
from .match import MatchAdmin#, ExhibitionMatchAdmin

from django.conf import settings
from django.contrib import admin

from corpoch import __version__ as version

admin.site.site_header = f'Corpo CH Admin {version}{f' - DEV' if settings.DEBUG else ''}'
admin.site.site_title = 'Corpo CH'
