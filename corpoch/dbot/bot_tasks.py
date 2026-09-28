import importlib
import io
import logging
import warnings
import discord
from datetime import timedelta
from unicodedata import normalize, category
from re import sub

from asgiref.sync import sync_to_async
from discord import Embed, File, AppEmoji
from discord.ext import tasks
from discord.ext.commands import Bot

import django
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

@tasks.loop()
async def run_tasks(bot: Bot):
	django.db.close_old_connections()
	if len(bot.tasks) > 0:
		task, args, kwargs = bot.tasks.pop(0)
		try:
			await task(bot, *args, **kwargs)
			bot.dispatch("dbot_task_completed", task.__name__)
		except Exception as e:
			bot.dispatch("dbot_task_failed", task.__name__, args, kwargs, e)
			print(f"Failed to run task {task} {args} {kwargs} {e}")
	else:
		run_tasks.stop()
	django.db.close_old_connections()

async def set_group_role(bot, user_id, guild_id, role_id):
	print(f"Setting Group role {role_id} discord ID {user_id}")

	guild = bot.get_guild(guild_id)
	user = await guild.fetch_member(user_id)
	role = await guild.fetch_role(role_id)
	await user.add_roles(role)

async def add_emoji(bot, name, path):
	with open(path, "rb") as f:
		if len(name) < 2:
			name += "_"
		squashed_name = sub("[^\\w]", "_", "".join(c for c in normalize('NFD', name) if category(c) != 'Mn'))
		try:
			emoji = await bot.create_emoji(name=squashed_name, image=f.read())
		except discord.errors.HTTPException:
			foundEmoji = False
			for tst in await bot.fetch_emojis():
				if tst.name == squashed_name:
					emoji = tst 
					foundEmoji = True
					break
			if not foundEmoji:
				print(f"Error on creating/finding emoji {name}")
				return None
		return emoji

async def add_bot_emoji(bot, name, img_path=None):
	from corpoch.dbot.models import CHEmoji
	from corpoch.models import CHIcon

	if img_path:
		emoji = await add_emoji(bot, name, img_path)
		dbIcon = None
		name = name
	else:
		dbIcon = await CHIcon.objects.aget(name=name)
		emoji = await add_emoji(bot, dbIcon.name, dbIcon.img.path)
		name = None

	new = CHEmoji(id=emoji.id, icon=dbIcon, name=name)
	await new.asave()

async def reload_cog(bot, cog):
	try:
		print(f"Reloading cog: {cog}")
		bot.unload_extension(cog)
		bot.load_extension(cog)
	except Exception as e:
		print(f"Reloading cog: {cog} failed: {e}")

async def send_qualifier_discord_dms(bot, player, quali, req_subs, quali_end, guild, num_subs):
	print(f"Sending reminder to {player} for {quali}")
	guild = bot.get_guild(guild)
	try:
		user = await guild.fetch_member(player)
	except:
		print(f"Can't find user {player} in guild {guild}")
		return
	if user.can_send():
		outStr = f"Hey! I wanted to quick remind you that the {quali} qualifier deadline is coming up at <t:{int(quali_end.timestamp())}:f>!\n"
		outStr += f"You've only submitted {num_subs} out of {req_subs} times, and need to submit before the deadline!"
		await user.send(outStr)
	else:
		print(f"Can't DM {player}")

async def refresh_match_message(bot, match_id):
	from corpoch.models import Match
	from corpoch.dbot.cogs.tourneycmds import DiscordMatch
	match = await Match.objects.select_related().aget(id=match_id)
	if match.finished:
		print(f"Refreshing match view {match.id}")
		view = DiscordMatch(bot, uuid=match.id)
		await view.init()
		await view.finishMatch(view.msg)
	else:
		print(f"Refreshing on-going match view {match.id}")
		if bot.matches.get(match.id):
			await bot.matches[match.id].showTool()
		else:
			view = DiscordMatch(bot, uuid=match.id)
			await view.init()
			await view.showTool()

def publish_guild_referees(guild_id, expected_role_ids, referee_user_ids):
	"""Publishes membership while holding the guild row used by admin edits."""
	from corpoch.dbot.models import Guilds

	with transaction.atomic():
		guild = Guilds.objects.select_for_update().get(pk=guild_id)
		if set(guild.configured_referee_roles().values_list('id', flat=True)) != expected_role_ids:
			raise RuntimeError('Referee roles changed during the update. Run Update Discord Info again.')
		guild.referees.set(referee_user_ids)

async def update_guild(bot, guild_id):
	guild = None
	try:
		guild = bot.get_guild(guild_id)
	except discord.Forbidden:
		pass

	from corpoch.dbot.models import Guilds
	dbguild = Guilds.objects.get(id=guild_id)
	if not guild:
		print(f"Guild {guild_id} is no longer visible - marking deleted")
		dbguild.deleted = True
		await dbguild.asave()
		return

	await guild.chunk()
	dbguild.name = guild.name
	dbguild.deleted = False
	if guild.icon:
		dbguild.icon = guild.icon.url

	from corpoch.dbot.models import Roles
	roles = await guild.fetch_roles()
	Roles.objects.filter(guild_id=guild_id).exclude(id__in=[role.id for role in roles]).update(deleted=True)

	from corpoch.models import DiscordUser
	admin_roles = []
	for role in roles:
		theRole, created = Roles.objects.get_or_create(id=role.id, guild=dbguild)
		theRole.name = role.name
		theRole.deleted = False
		if role.permissions.administrator:
			admin_roles.append(role)
		await theRole.asave()

	referee_role_ids = set(dbguild.configured_referee_roles().values_list('id', flat=True))
	referee_users = {}
	for role in roles:
		if role.id in referee_role_ids:
			for mem in role.members:
				if mem.bot or mem.id in referee_users:
					continue
				user, created = DiscordUser.objects.get_or_create(id=mem.id)
				if created:
					await update_user(bot, user.id)
				referee_users[user.id] = user
	dbguild.admins.clear()
	for role in admin_roles:
		for mem in role.members:
			if mem.bot:
				continue
			user, created = DiscordUser.objects.get_or_create(id=mem.id)
			if created:
				await update_user(bot, user.id)
			dbguild.admins.add(user)

	from corpoch.dbot.models import Channels
	for channel in Channels.objects.all().filter(guild__id=guild_id):
		try:
			gchannel = await guild.fetch_channel(channel.id)
		except (discord.Forbidden, discord.NotFound):
			channel.deleted = True
		else:
			channel.name = gchannel.name
		finally:
			await channel.asave()

	for channel in await guild.fetch_channels():
		from corpoch.dbot.models import Channels
		theChannel, created = Channels.objects.get_or_create(id=channel.id, guild=dbguild)
		theChannel.name = channel.name
		await theChannel.asave()

	await dbguild.asave(update_fields=['name', 'icon', 'deleted'])
	# Replace membership only after every remote lookup succeeds. Empty or
	# removed role configurations must also revoke previously stored referees.
	await sync_to_async(publish_guild_referees)(guild_id, referee_role_ids, list(referee_users))

async def update_user(bot, user_id):
	from corpoch.models import DiscordUser
	dbuser = DiscordUser.objects.get(id=user_id)
	try:
		duser = await bot.fetch_user(dbuser.id)
	except discord.NotFound:
		print(f"User {user_id} is no longer visible")
		return

	dbuser.username = duser.global_name if duser.global_name else duser.display_name
	dbuser.global_name = duser.global_name if duser.global_name else duser.display_name
	dbuser.avatar = duser.display_avatar.url
	for ply in dbuser.tournaments.all().filter(is_active=True):
		guild = bot.get_guild(ply.tournament.guild.id)
		if guild:
			try:
				member = await guild.fetch_member(dbuser.id)
			except discord.NotFound:
				continue
			else:
				ply.name = member.display_name if member.display_name else member.global_name
				await ply.asave()
	await dbuser.asave()
