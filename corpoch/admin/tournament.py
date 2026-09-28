from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from adminsortable2.admin import SortableStackedInline, SortableAdminBase
from django_jsonform.widgets import JSONFormWidget
from django_pydantic_field import fields

from corpoch.forms import TournamentPlayerForm
from corpoch.models import Tournament, TournamentConfig, BracketRules, Bracket, Qualifier, QualifierSubmission, TournamentPlayer, Group, GroupSeed, DiscordUser
from corpoch.dbot.models import Channels, Guilds, Roles

import corpoch.tasks

class TournamentConfigInline(admin.StackedInline):
	model = TournamentConfig
	extra = 0

	def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
		if db_field.name == "proof_channel":
			if 'object_id' in request.resolver_match.kwargs:
				conf = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Channels.objects.all().filter(guild=conf.tournament.guild)
			else:
				kwargs["queryset"] = Channels.objects.none()
		return super(TournamentConfigInline, self).formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
	list_display = ('name', 'guild', 'active')
	inlines = [TournamentConfigInline]
	actions = ['set_tournament_role', 'set_players_active', 'regen_player_list_gsheet']

	def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
		if db_field.name == "role":
			if 'object_id' in request.resolver_match.kwargs:
				tournament = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Roles.objects.all().filter(guild=tournament.guild)
			else:
				kwargs["queryset"] = Roles.objects.none()
		return super(TournamentAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Set tournament role for players")
	def set_tournament_role(modeladmin, request, queryset):
		for tourney in queryset:
			role = tourney.role
			guild = tourney.guild
			if role:
				for bracket in tourney.brackets.all():
					for group in bracket.groups.all():
						for seed in group.seeding.all():
							ply = seed.player.user
							corpoch.dbot.tasks.set_group_role(ply.id, guild.id, role.id)

	@admin.action(description="Set players as active")
	def set_players_active(modeladmin, request, queryset):
		for tourney in queryset:
			role = tourney.role
			guild = tourney.guild
			for bracket in tourney.brackets.all():
				for group in bracket.groups.all():
					for seed in group.seeding.all():
						seed.player.is_active = True
						seed.player.save()

	@admin.action(description="Regenerate Player List GSheet")
	def regen_player_list_gsheet(modeladmin, request, queryset):
		for tournament in queryset:
			corpoch.tasks.update_gsheet.apply_async(args=[tournament.id])


class BracketRulesInline(admin.StackedInline):
	model = BracketRules
	extra = 0

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return ()
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return ()
		except DiscordUser.DoesNotExist:
			if obj == None or len(obj.setlist.all().filter(boss=True)) == 0:
				return ('num_players', 'num_bans', 'num_rounds', 'ban_ruleset', 'pick_ruleset', 'tb_ruleset', 'seed_inversions')
			else:
				return ('num_players', 'num_bans', 'num_rounds', 'boss_active', 'boss_bannable', 'ban_ruleset', 'pick_ruleset', 'tb_ruleset', 'seed_inversions')

	def get_fields(self, request, obj=None):
		if obj == None or len(obj.setlist.all().filter(boss=True)) == 0:
			return ('num_players', 'num_bans', 'num_rounds', 'ban_ruleset', 'pick_ruleset', 'tb_ruleset', 'seed_inversions',)
		else:
			return ('num_players', 'num_bans', 'num_rounds', 'boss_active', 'boss_bannable', 'ban_ruleset', 'pick_ruleset', 'tb_ruleset', 'seed_inversions',)

@admin.register(Bracket)
class BracketAdmin(admin.ModelAdmin):
	list_display = ("_name", 'tournament')
	list_filter = ['tournament']
	inlines = [BracketRulesInline]
	actions = ['set_bracket_role']

	def _name(self, obj):
		return f"{obj}"

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return ()
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return ()
		except DiscordUser.DoesNotExist:
			return ('id', 'name', 'tournament', 'score_log', 'is_active', 'revealed', 'role')

	def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
		if db_field.name == "role":
			if 'object_id' in request.resolver_match.kwargs:
				bracket = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Roles.objects.all().filter(guild=bracket.tournament.guild)
			else:
				kwargs["queryset"] = Roles.objects.none()
		if db_field.name == "score_log":
			if 'object_id' in request.resolver_match.kwargs:
				bracket = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Channels.objects.all().filter(guild=bracket.tournament.guild)
			else:
				kwargs["queryset"] = Channels.objects.none()
		return super(BracketAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Set bracket role for players")
	def set_bracket_role(modeladmin, request, queryset):
		for bracket in queryset:
			guild = bracket.tournament.guild
			role = bracket.role
			if role:
				for group in bracket.groups.all():
					for seed in group.seeding.all():
						ply = seed.player.user
						corpoch.dbot.tasks.set_group_role(ply.id, guild.id, role.id)

@admin.register(TournamentPlayer)
class TournamentPlayerAdmin(admin.ModelAdmin):
	form = TournamentPlayerForm
	list_display = ('user', 'tournament', 'display_exact_ch_name', 'is_active')
	list_filter = ['tournament', 'is_active']
	actions = ["set_tournament_roles"]
	readonly_fields = ("display_exact_ch_name",)
	search_fields = ('name',)
	fields = (
		'user',
		'name',
		'tournament',
		'display_exact_ch_name',
		'primary_ch_name_selection',
		'new_ch_name',
		'is_active',
		'config',
		'delete_ch_name',
	)

	def check_perm(self, request, obj):
		if not obj or request.user.is_superuser:
			return True
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return True
		except DiscordUser.DoesNotExist:
			return False

	def has_add_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_change_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	@admin.display(description='Clone Hero Name', ordering='ch_name')
	def display_exact_ch_name(self, obj):
		if not obj.ch_name:
			return "-"

		return format_html(
			'<span style="white-space: pre-wrap; background-color: rgba(128, 128, 128, 0.2); padding: 2px 4px; border-radius: 3px; font-family: monospace;">{}</span>',
			obj.ch_name
		)

	@admin.action(description="Set tournament roles")
	def set_tournament_roles(modeladmin, request, queryset):
		for ply in queryset:
			guild = ply.tournament.guild
			for seed in ply.group_seeding.all():
				grole = seed.group.role if seed.group.role else None
				brole = seed.group.bracket.role if seed.group.bracket.role else None
				trole = tourney.role if tourney.role else None
				if grole is not None:
					corpoch.dbot.tasks.set_group_role(ply.user.id, guild.id, grole.id)
				if brole is not None:
					corpoch.dbot.tasks.set_group_role(ply.user.id, guild.id, brole.id)
			if trole is not None:
				corpoch.dbot.tasks.set_group_role(ply.user.id, guild.id, trole.id)

@admin.register(Qualifier)
class QualifierAdmin(admin.ModelAdmin):
	list_display = ('id', 'tournament', '_players', '_submissions')
	list_filter = ['tournament']
	actions = ['submit_final_scores', "send_discord_reminders"]
	filter_horizontal = ['charts']

	def _players(self, obj):
		return QualifierSubmission.objects.all().filter(qualifier__tournament=obj.tournament).values('player').distinct().count()

	def _submissions(self, obj):
		return QualifierSubmission.objects.filter(qualifier=obj).count()

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "player":
			if 'object_id' in request.resolver_match.kwargs:
				group = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs["queryset"] = group.tournament.players
			else:
				kwargs["queryset"] = GroupSeed.objects.none()
		if db_field.name == "channel":
			if 'object_id' in request.resolver_match.kwargs:
				qualifier = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Channels.objects.all().filter(guild=qualifier.tournament.guild)
			else:
				kwargs["queryset"] = Channels.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Submit Final Top Scores")
	def submit_final_scores(modeladmin, request, queryset):
		for quali in queryset:
			corpoch.tasks.submit_final_sheet.apply_async(args=[quali.id])

	@admin.action(description="Send discord reminders submissions")
	def send_discord_reminders(modeladmin, request, queryset):
		for quali in queryset:
			corpoch.tasks.send_qualifier_discord_dms.apply_async(args=[quali.id])

class SeedingInline(SortableStackedInline):
	model = GroupSeed
	extra = 0

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "player":
			if 'object_id' in request.resolver_match.kwargs:
				group = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs["queryset"] = group.bracket.tournament.players.all()
			else:
				kwargs["queryset"] = GroupSeed.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

	def check_perm(self, request, obj):
		if not obj or request.user.is_superuser:
			return True
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return True
		except DiscordUser.DoesNotExist:
			return False

	def has_add_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_change_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return ()
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return ()
		except DiscordUser.DoesNotExist:
			return ('id', 'seed', 'player', 'eliminated',)

@admin.register(Group)
class GroupAdmin(SortableAdminBase, admin.ModelAdmin):
	list_display = ('name', 'bracket_name','tournament', 'active_count', 'player_count')
	list_filter = ('bracket__tournament',)
	inlines = [SeedingInline]
	search_fields = ('bracket',)
	list_per_page = 32
	actions = ['set_group_role']

	def tournament(self, obj):
		return obj.bracket.tournament.short_name

	def bracket_name(self, obj):
		return obj.bracket.name

	def active_count(self, obj):
		return obj.seeding.all().filter(player__is_active=True, eliminated=False).count()

	def player_count(self, obj):
		return obj.seeding.all().count()

	def check_perm(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return True
		try:
			is_staff = obj.bracket.tournament.guild.admins.get(id=request.user.id)
			return True
		except DiscordUser.DoesNotExist:
			return False

	def has_add_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_delete_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def has_change_permission(self, request, obj=None):
		return self.check_perm(request, obj)

	def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
		if db_field.name == 'bracket':
			if 'object_id' in request.resolver_match.kwargs:
				model = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Bracket.objects.all().filter(tournament=model.bracket.tournament)
			else:
				kwargs['queryset'] = Bracket.objects.all().filter(tournament__active=True)
		if db_field.name == "role":
			if 'object_id' in request.resolver_match.kwargs:
				group = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Roles.objects.filter(guild=group.bracket.tournament.guild)
			else:
				kwargs["queryset"] = Roles.objects.none()
		return super(GroupAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Set group role for players")
	def set_group_role(modeladmin, request, queryset):
		for group in queryset:
			guild = group.bracket.tournament.guild
			role = group.role
			if role:
				for seed in group.seeding.all():
					ply = seed.player.user
					corpoch.dbot.tasks.set_group_role(ply.id, guild.id, role.id)

@admin.register(QualifierSubmission)
class QualifierSubmissionAdmin(admin.ModelAdmin):
	list_display = ('id', 'qualifier', 'player_ch_name', 'score', '_miss', '_hit', '_excess', '_ghosts', '_phrases', 'submitted')
	list_filter = ["qualifier"]
	search_fields = ['id', 'player__name']
	formfield_overrides = { fields.PydanticSchemaField: {"widget": JSONFormWidget}, }
	actions = ['set_unsubmitted',"reread_steg", "resubmit_gsheet"]

	def tournament(self, obj):
		return obj.qualifier.tournament.short_name

	def player_ch_name(self, obj):
		return obj.player.ch_name

	def _miss(self, obj):
		return obj.steg.players[0].notes_missed if len(obj.steg.players) > 0 else '-'

	def _hit(self, obj):
		return obj.steg.players[0].notes_hit if len(obj.steg.players) > 0 else '-'

	def _excess(self, obj):
		return obj.steg.players[0].excess_hits if len(obj.steg.players) > 0 and obj.steg.players[0].excess_hits > -1 else '-'

	def _ghosts(self, obj):
		return obj.steg.players[0].frets_ghosted if len(obj.steg.players) > 0 else '-'

	def _phrases(self, obj):
		return obj.steg.players[0].sp_phrases_earned if len(obj.steg.players) > 0 else '-'

	def get_form(self, request, obj=None, **kwargs):
		form = super(QualifierSubmissionAdmin, self).get_form(request, obj=obj, **kwargs)
		user = request.user
		staff = False
		if obj:
			try:
				is_staff = obj.qualifier.tournament.guild.admins.get(id=user.id)
				staff = True
			except DiscordUser.DoesNotExist:
				pass
			if not staff or (obj and not obj.screenshot):
				form.base_fields['steg'].disabled = True
		return form

	def get_readonly_fields(self, request, obj=None):
		if not obj or request.user.is_superuser:
			return ()
		try:
			is_staff = obj.qualifier.tournament.guild.admins.get(id=request.user.id)
			return ()
		except DiscordUser.DoesNotExist:
			return ('id', 'player', 'screenshot', 'qualifier', 'submitted')

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		if request.user.is_superuser:
			return qs
		for obj in qs:
			staff = False
			try:
				is_admin = obj.qualifier.tournament.guild.admins.get(id=request.user.id)
				staff = True
			except DiscordUser.DoesNotExist:
				pass

			if not staff and obj.qualifier.end_time > timezone.now():
				qs = qs.all().exclude(id=obj.id)
		return qs

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "player":
			if 'object_id' in request.resolver_match.kwargs:
				sub = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = TournamentPlayer.objects.all().filter(tournament=sub.qualifier.tournament)
			else:
				kwargs["queryset"] = TournamentPlayer.objects.all()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Mark Qualifiers GSheet Unsent")
	def set_unsubmitted(modeladmin, request, queryset):
		for quali in queryset:
			quali.submitted = False
			quali.save()

	@admin.action(description="Reread steg data")
	def reread_steg(modeladmin, request, queryset):
		for quali in queryset:
			quali.steg = None
			quali.save()

	@admin.action(description="Correct GSheet Values")
	def resubmit_gsheet(modeladmin, request, queryset):
		for submission in queryset:
			corpoch.tasks.update_gsheet.apply_async(args=[submission.id])
