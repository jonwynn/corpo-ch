from django.contrib import admin
from corpoch.dbot.models import Guilds, Channels, Roles
from django.utils.html import mark_safe

from corpoch.models import DiscordUser
import corpoch.dbot.tasks

@admin.register(Guilds)
class GuildAdmin(admin.ModelAdmin):
	list_display = ('_icon', '_id', 'name')
	readonly_fields = ['name', 'icon', 'deleted']
	actions = ['update_discord_guild']
	filter_horizontal = ('admins', 'referees',)
	def _id(self, obj):
		return str(obj.id)

	@mark_safe
	def _icon(self, obj):
		if obj.icon:
			return f'<img src="{obj.icon}" width="24" height="24"'
		else:
			return "None"

	def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
		if db_field.name == "ref_role" or db_field.name == "admin_role":
			if 'object_id' in request.resolver_match.kwargs:
				guild = self.model.objects.get(pk=request.resolver_match.kwargs['object_id'])
				kwargs['queryset'] = Roles.objects.all().filter(guild=guild)
			else:
				kwargs["queryset"] = Roles.objects.none()
		return super(GuildAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

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
