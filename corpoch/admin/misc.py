from django.contrib import admin
from django.utils.safestring import mark_safe

from solo.admin import SingletonModelAdmin

from corpoch.models import GSheetAPI, DiscordUser

import corpoch.dbot.tasks

@admin.register(GSheetAPI)
class GSheetAPIAdmin(SingletonModelAdmin):
	readonly_fields = ['sa_name']

@admin.register(DiscordUser)
class DiscordUserAdmin(admin.ModelAdmin):
	model = DiscordUser
	list_display = ('_avatar', 'id', 'global_name')
	readonly_fields = ['global_name', 'mfa_enabled', '_id', 'avatar', 'locale', 'flags', 'public_flags', 'last_login', 'date_joined']
	exclude = ['password', 'first_name', 'last_name', 'email', 'username']
	search_fields = ['id', "global_name"]
	actions = ['update_discord_user']

	def _id(self, obj):
		return str(obj.id)

	@mark_safe
	def _avatar(self, obj):
		return f'<img src="{obj.avatar}" width="24" height="24"'

	@admin.action(description="Update Discord Info")
	def update_discord_user(modeladmin, request, queryset):
		for user in queryset:
			corpoch.dbot.tasks.update_user(user.id)
