from django import forms
from django.contrib import admin
from corpoch.dbot.models import Guilds, Channels, Roles
from django.utils.html import mark_safe

from corpoch.models import DiscordUser
import corpoch.dbot.tasks

class GuildAdminForm(forms.ModelForm):
	"""Limits referee role selections to active roles in the edited guild."""

	class Meta:
		model = Guilds
		fields = '__all__'

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		roles = Roles.objects.filter(guild_id=self.instance.pk, deleted=False) if self.instance.pk else Roles.objects.none()
		self.fields['ref_role'].queryset = roles
		self.fields['additional_ref_roles'].queryset = roles

@admin.register(Guilds)
class GuildAdmin(admin.ModelAdmin):
	form = GuildAdminForm
	list_display = ('_icon', '_id', 'name')
	readonly_fields = ['name', 'icon', 'deleted']
	actions = ['update_discord_guild']
	filter_horizontal = ('admins', 'referees', 'additional_ref_roles',)
	def _id(self, obj):
		return str(obj.id)

	@mark_safe
	def _icon(self, obj):
		if obj.icon:
			return f'<img src="{obj.icon}" width="24" height="24"'
		else:
			return "None"

	@admin.action(description="Update Discord Info")
	def update_discord_guild(modeladmin, request, queryset):
		for guild in queryset:
			corpoch.dbot.tasks.update_guild(guild.id)

@admin.register(Channels)
class ChannelAdmin(admin.ModelAdmin):
	list_display = ('_id', 'guild', 'name')
	readonly_fields = ['name', 'deleted']
	search_fields = ['id', 'name']

	def _id(self, obj):
		return str(obj.id)

@admin.register(Roles)
class RoleAdmin(admin.ModelAdmin):
	list_display = ('_id', 'guild', 'name')
	readonly_fields = ['name', 'deleted']
	search_fields = ['id', 'name']

	def _id(self, obj):
		return str(obj.id)
