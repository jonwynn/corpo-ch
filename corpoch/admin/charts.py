from itertools import chain

from django.conf import settings
from django.contrib import admin
from django.utils.safestring import mark_safe

from corpoch.models import Chart, BYOSChart, Qualifier

import corpoch.tasks

@admin.register(Chart)
class ChartAdmin(admin.ModelAdmin):
	list_display = ('_icon','_tournament_name', '_brackets', '_category', 'boss', 'tiebreaker')
	list_filter = ['brackets__tournament', 'tiebreaker', 'boss']
	actions = ['run_encore_import', 'import_song_ini']
	readonly_fields = ['_icon', 'game_version']
	search_fields = ('name', 'charter')
	filter_horizontal = ['brackets']

	def _tournament_name(self, obj):
		return obj.tournament_name

	def _brackets(self, obj):
		retList = []
		for bracket in obj.brackets.iterator():
			retList.append(bracket)

		for quali in Qualifier.objects.all().filter(charts__in=[obj]):
			retList.append(f"{quali} Qualifier")

		return retList

	def _category(self, obj):
		return obj.category.capitalize()

	@mark_safe
	def _icon(self, obj):
		if obj.icon:
			return f'<img src="{obj.icon.img.url}" width="24" height="24"'
		else:
			return "None"

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return self.readonly_fields

		for bracket in obj.brackets.all():
			try:
				is_staff = bracket.tournament.guild.admins.get(id=request.user.id)
				return self.readonly_fields
			except DiscordUser.DoesNotExist:
				continue

		return list(chain(self.readonly_fields, ['id', 'name', 'artist', 'album', 'charter', 'boss', 'tiebreaker', 'difficulty', 'instrument', 'modifiers', 'speed', 'category', 'brackets', 'md5', 'blake3', 'url', 'icon', 'sngfile']))

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		#Ensure DEV envs always return for SU's
		if settings.DEBUG and request.user.is_superuser:
			return qs

		for obj in qs:
			for bracket in obj.brackets.all():
				if bracket.revealed:
					break
				else:
					try:
						is_admin = bracket.tournament.guild.admins.get(id=request.user.id)
						is_player = bracket.tournament.players.get(user=is_admin)
						qs = qs.all().exclude(id=obj.id) #If staff user in tournament, hide chart
					except DiscordUser.DoesNotExist:
						qs = qs.all().exclude(id=obj.id)
					except TournamentPlayer.DoesNotExist:
						pass
		return qs

	@admin.action(description="Run Encore import")
	def run_encore_import(modeladmin, request, queryset):
		for chart in queryset:
			corpoch.tasks.encore_import.apply_async(args=[chart.id])

	@admin.action(description="Import data from song.ini")
	def import_song_ini(modeladmin, request, queryset):
		for chart in queryset:
			corpoch.tasks.chart_songini_import.apply_async(args=[chart.id])

@admin.register(BYOSChart)
class BYOSChartAdmin(ChartAdmin):
	list_display = ('_icon','_tournament_name')
	list_filter = ['groups__bracket']
	actions = ['run_encore_import', 'import_song_ini']
	readonly_fields = ['_icon', 'game_version']
	exclude = ('boss', 'category', 'tiebreaker', 'brackets')
	search_fields = ('name', 'charter')
	filter_horizontal = ['groups']

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return self.readonly_fields

		for bracket in obj.brackets.all():
			try:
				is_staff = bracket.tournament.guild.admins.get(id=request.user.id)
				return self.readonly_fields
			except DiscordUser.DoesNotExist:
				continue

		return list(chain(self.readonly_fields, ['id', 'name', 'artist', 'album', 'charter', 'difficulty', 'instrument', 'modifiers', 'speed', 'groups', 'md5', 'icon', 'sngfile']))
