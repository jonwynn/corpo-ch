from django.contrib import admin, messages
from django.forms import CharField, HiddenInput, ModelForm
from django.shortcuts import redirect

from adminsortable2.admin import CustomInlineFormSet, SortableAdminBase, SortableStackedInline, SortableAdminMixin
from django_jsonform.widgets import JSONFormWidget
from django_pydantic_field import fields

from corpoch.models import TournamentConfig, Match, MatchRound, MatchBan, TournamentPlayer, DiscordUser, GroupSeed, Group, Bracket, Chart
from corpoch.dbot.models import Channels
from corpoch.match_actions import MatchActionError, advance_match_revision, get_match_state_token, locked_match
from corpoch.match_publication import finish_match_evidence, publish_export_status, publish_round_evidence


class MatchAdminForm(ModelForm):
    match_state = CharField(widget=HiddenInput, required=False)

    class Meta:
        model = Match
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.group_id and not self.instance._state.adding:
            self.fields['match_state'].initial = get_match_state_token(self.instance)

class RoundsForm(ModelForm):
	class Meta:
		model = MatchRound
		fields = '__all__'

	def __init__(self, *args, **kwargs):
		super(RoundsForm, self).__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			if not self.instance.screenshot and 'steg' in self.fields:
				self.fields['steg'].disabled = True
		return

class RoundsInline(SortableStackedInline):
	model = MatchRound
	formfield_overrides = { fields.PydanticSchemaField: {"widget": JSONFormWidget}, }
	extra = 0

	def get_formset(self, request, obj=None, **kwargs):
		formset = super().get_formset(request, obj, **kwargs)
		if request.user.is_superuser:
			return formset

		staff, ref = False, False
		if obj:
			try:
				is_staff = obj.group.tournament.guild.admins.get(id=request.user.id)
				staff = True
			except DiscordUser.DoesNotExist:
				pass
			try:
				is_ref = obj.group.tournament.guild.referees.get(id=request.user.id)
				ref = True
			except DiscordUser.DoesNotExist:
				pass

		if not staff and not ref:
			if 'steg' in formset.form.base_fields:
				formset.form.base_fields['steg'].disabled = True

		return formset

	def get_readonly_fields(self, request, obj=None):
		readonly_fields = ('created', 'selection_kind')
		if obj and obj.ruleset.tb_ruleset == 'corp_cup':
			readonly_fields += ('screenshot', 'steg')
		if not obj or request.user.is_superuser:
			return readonly_fields
		staff = False
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return readonly_fields
		except DiscordUser.DoesNotExist:
			return ('id', 'num', 'match', 'picked', 'chart', 'winner', 'loser', 'screenshot', 'created', 'selection_kind')

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "winner" or db_field.name == "loser" or db_field.name == 'picked':
			if 'object_id' in request.resolver_match.kwargs:
				match = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = TournamentPlayer.objects.all().filter(id__in=match.players.all().values("player"))
			else:
				kwargs["queryset"] = TournamentPlayer.objects.none()
		if db_field.name == 'chart':
			if 'object_id' in request.resolver_match.kwargs:
				match = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs["queryset"] = match.bracket.setlist.all()
			else:
				kwargs["queryset"] = GroupSeed.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

class BansInline(SortableStackedInline):
	model = MatchBan
	readonly_fields = ['created']
	extra = 0

	def get_fields(self, request, obj=None):
		if obj.bracket.ruleset.ban_ruleset == "bansave":
			return ('saved', 'player', 'chart')
		else:
			return ('player', 'chart')

	def check_perm(self, request):
		if 'object_id' in request.resolver_match.kwargs:
			obj = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
		else:
			obj = None

		if not obj or request.user.is_superuser:
			return True
		try:
			is_staff = obj.tournament.guild.admins.get(id=request.user.id)
			return True
		except DiscordUser.DoesNotExist:
			pass
		try:
			is_ref = obj.tournament.guild.referees.get(id=request.user.id)
			return True
		except DiscordUser.DoesNotExist:
			return False

	def has_add_permission(self, request, obj=None):
		return self.check_perm(request)

	def has_delete_permission(self, request, obj=None):
		return self.check_perm(request)

	def has_change_permission(self, request, obj=None):
		return self.check_perm(request)

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "player":
			if 'object_id' in request.resolver_match.kwargs:
				match = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = TournamentPlayer.objects.all().filter(id__in=match.players.all().values("player"))
			else:
				kwargs["queryset"] = TournamentPlayer.objects.none()
		if db_field.name == 'chart':
			if 'object_id' in request.resolver_match.kwargs:
				match = self.parent_model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs["queryset"] = match.bracket.setlist.all()
			else:
				kwargs["queryset"] = Chart.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Match)
class MatchAdmin(SortableAdminBase, admin.ModelAdmin):
	form = MatchAdminForm
	list_display = ('__str__', 'group', '_match_players', 'score', 'started_on', 'ended_on', 'complete', 'finished', 'submitted')
	list_filter = ('group__bracket__tournament',)
	inlines = [BansInline, RoundsInline]
	list_per_page = 25
	search_fields = ['id']
	actions = ['set_unsubmitted', "reread_steg", "resubmit_gsheet", "resubmit_discord"]

	def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
		if request.method == 'POST' and object_id:
			match = self.get_object(request, object_id)
			if match and self.has_change_permission(request, match) and match.ruleset.tb_ruleset == 'corp_cup':
				try:
					with locked_match(object_id, expected_state=request.POST.get('match_state') or 'missing'):
						request.match_viewer_locked = True
						return super().changeform_view(request, object_id, form_url, extra_context)
				except MatchActionError as error:
					self.message_user(request, str(error), level=messages.WARNING)
					return redirect(request.path)
		return super().changeform_view(request, object_id, form_url, extra_context)

	def save_model(self, request, obj, form, change):
		if change and getattr(request, 'match_viewer_locked', False):
			fields = {field.name for field in obj._meta.concrete_fields} & set(form.changed_data)
			if fields:
				obj.save(update_fields=fields)
		else:
			super().save_model(request, obj, form, change)

	def save_formset(self, request, form, formset, change):
		if not getattr(request, 'match_viewer_locked', False):
			return super().save_formset(request, form, formset, change)
		instances = formset.save(commit=False)
		for deleted in formset.deleted_objects:
			deleted.delete()
		for instance in instances:
			changed = next(
				(set(item.changed_data) for item in formset.forms if item.instance is instance), set(),
			)
			if isinstance(instance, MatchRound) and changed & {'chart', 'picked'}:
				instance.selection_kind = 'unknown'
				changed.add('selection_kind')
			elif isinstance(instance, MatchBan) and changed & {'chart', 'player', 'saved'}:
				instance.action_phase = 'unknown'
				changed.add('action_phase')
			if instance._state.adding:
				instance.save()
			else:
				fields = {field.name for field in instance._meta.concrete_fields} & changed
				if fields:
					instance.save(update_fields=fields)
		formset.save_m2m()
		if instances or formset.deleted_objects:
			Match.objects.filter(pk=form.instance.pk).update(
				complete=False, finished=False, winner=None, loser=None, ended_on=None,
			)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		if getattr(request, 'match_viewer_locked', False) and set(form.changed_data) & {'group', 'players', 'rev_seeds', 'defer'}:
			MatchBan.objects.filter(match=form.instance).update(action_phase='unknown')
			MatchRound.objects.filter(match=form.instance).update(selection_kind='unknown')
			Match.objects.filter(pk=form.instance.pk).update(
				complete=False, finished=False, winner=None, loser=None, ended_on=None,
			)
		if getattr(request, 'match_viewer_locked', False) and (form.has_changed() or any(item.has_changed() for item in formsets)):
			advance_match_revision(form.instance)

	def get_readonly_fields(self, request, obj=None):
		identity_fields = ('id',) if obj is not None else ()
		if request.user.is_superuser:
			return ('started_on', 'action_revision') + identity_fields
		if obj is None:
			return ('started_on', 'action_revision')
		try:
			is_ref = obj.group.tournament.guild.referees.get(id=request.user.id)
			return ('started_on', 'action_revision') + identity_fields
		except DiscordUser.DoesNotExist:
			pass
		try:
			is_admin = obj.group.tournament.guild.admins.get(id=request.user.id)
			return ('started_on', 'action_revision') + identity_fields
		except DiscordUser.DoesNotExist:
			return ('id', 'players', 'loser', 'winner', 'defer', 'group', 'started_on', 'ended_on', 'complete', 'finished', 'submitted', 'channel', 'message', 'referee', 'exhibition', 'action_revision')

	def _match_players(self, obj):
		retList = []
		for seed in obj.players.iterator():
			retList.append(str(seed))
		return " vs ".join(retList)

	def formfield_for_manytomany(self, db_field, request, **kwargs):
		if db_field.name == "players":
			if 'object_id' in request.resolver_match.kwargs:
				match = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = match.group.seeding.all()
			else:
				kwargs["queryset"] = GroupSeed.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "winner" or db_field.name == "loser":
			if 'object_id' in request.resolver_match.kwargs:
				match = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = TournamentPlayer.objects.all().filter(id__in=match.players.all().values("player"))
			else:
				kwargs["queryset"] = TournamentPlayer.objects.none()
		if db_field.name == "channel":
			if 'object_id' in request.resolver_match.kwargs:
				match = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Channels.objects.all().filter(guild=match.tournament.guild)
			else:
				kwargs["queryset"] = Channels.objects.none()
		if db_field.name == "referee":
			if 'object_id' in request.resolver_match.kwargs:
				match = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				queryset =  match.tournament.guild.referees.all() | match.tournament.guild.admins.all()
				if match.referee:
					queryset = queryset | DiscordUser.objects.filter(pk=match.referee.id)
				queryset = queryset.distinct()
				kwargs['queryset'] = queryset
			else:
				kwargs['queryset'] = DiscordUser.objects.none()
		return super().formfield_for_foreignkey(db_field, request, **kwargs)

	@admin.action(description="Mark Match GSheet Unsent")
	def set_unsubmitted(modeladmin, request, queryset):
		for match in queryset:
			publish_export_status(match.pk, submitted=False)

	@admin.action(description="Reread steg data")
	def reread_steg(modeladmin, request, queryset):
		from corpoch.providers import CHStegTool
		tool = CHStegTool()
		for match in queryset:
			for rnd in match.rounds:
				if not rnd.screenshot:
					continue
				expected_state = get_match_state_token(match)
				filename = str(rnd.screenshot)
				steg = tool.getStegInfoSync(rnd.screenshot)
				try:
					publish_round_evidence(
						match.pk, rnd.pk, rnd.chart_id, filename, steg,
						expected_screenshot=filename, expected_state=expected_state,
					)
				except MatchActionError as error:
					modeladmin.message_user(request, str(error), level=messages.WARNING)
			try:
				finish_match_evidence(match.pk)
			except MatchActionError as error:
				modeladmin.message_user(request, str(error), level=messages.WARNING)

	@admin.action(description="Correct GSheet Values")
	def resubmit_gsheet(modeladmin, request, queryset):
		import corpoch.tasks
		for match in queryset:
			corpoch.tasks.update_gsheet.apply_async(args=[match.id])

	@admin.action(description="Refresh Discord Message")
	def resubmit_discord(modeladmin, request, queryset):
		import corpoch.dbot.tasks
		for match in queryset:
			corpoch.dbot.tasks.refresh_match_message(match.id)
