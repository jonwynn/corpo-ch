import discord
from asgiref.sync import sync_to_async
from discord.ext import commands
from django.db import transaction

from corpoch import settings
from corpoch.match_actions import MatchActionError, get_match_state_token, load_corp_context
from corpoch.match_publication import finish_match_evidence
from corpoch.models import Bracket, Match, Tournament
from corpoch.dbot.view.reftool import DiscordMatchView

class DiscordMatch():
	def __init__(self, bot, message=None, uuid=None, exhibition=False):
		self.bot = bot
		self.msg = message
		self.guild = message.guild if message else None
		self.referee = message.user if hasattr(message, 'user') else None
		self.channel = message.channel if hasattr(message, 'channel') else None
		self.tourney = None
		self.bracket = None
		self.seeding_search = []
		self.screen_review = []
		self.matchDb = uuid
		self.exhibition = exhibition
		self.confirm_cancel = False
		self.player_input = False
		self.render_state = None

	async def init(self) -> bool:
		if self.matchDb:
			self.load_match()
			#Finish loading async
			self.msg = await self.channel.fetch_message(self.matchDb.message)
			self.referee = await self.guild.fetch_member(self.matchDb.referee.id)
			await self.showTool(self.msg)
			return True
		try:
			self.tourney = await Tournament.objects.select_related().aget(guild=self.msg.guild.id, active=True)
		except Tournament.DoesNotExist:
			await self.msg.respond("No active tourney - running exhibition mode not supported now", ephemeral=True)
			return False

		ref_role = self.referee.get_role(self.tourney.guild.ref_role.id)
		if not ref_role and not self.referee.guild_permissions.administrator:
			await self.msg.respond("You are not a ref for this tournament!", ephemeral=False)
			return False
		try:
			self.bracket = await Bracket.objects.select_related("ruleset").aget(score_log__id=self.msg.channel.id, is_active=True)
		except Bracket.DoesNotExist: 
			await self.msg.respond("Channel is not a score log channel or no brackets are currently active.", ephemeral=True)
			return False
		except Bracket.MultipleObjectsReturned:
			pass

		if isinstance(self.msg, discord.ApplicationContext):
			await self.msg.respond("Setting up")
			return True
		else:
			await self.showTool(self.msg)
			return True

	async def finishMatch(self, interaction):
		print(f"Finishing match {self.matchDb.id}")
		try:
			self.matchDb = await sync_to_async(finish_match_evidence)(self.matchDb.pk)
		except (ValueError, Match.DoesNotExist) as error:
			await self.send_action_notice(interaction, str(error))
			await self.showTool(interaction)
			return
		embeds = [await self.genMatchEmbed()]
		shared_url = f"https://{settings.BASE_URL}/gallery/"
		async for rnd in self.matchDb.rounds.select_related():
			embed = discord.Embed(url=shared_url)
			embed.set_image(url=f"https://{settings.BASE_URL}{settings.MEDIA_URL}{rnd.screenshot}")
			embeds.append(embed)
		await interaction.edit(embeds=embeds[:10], view=None)
		self.bot.matches.pop(self.matchDb.id, None)

	async def send_action_notice(self, interaction, message):
		"""Reports a rejected or outdated interaction without changing results."""
		if interaction.response.is_done():
			await interaction.followup.send(message, ephemeral=True, delete_after=10)
		else:
			await interaction.response.send_message(message, ephemeral=True, delete_after=10)

	async def apply_match_action(self, interaction, action, *args, **kwargs):
		"""Publishes one validated transition, then redraws from the saved match."""
		accepted = True
		try:
			self.matchDb = await sync_to_async(action)(*args, **kwargs)
		except ValueError as error:
			accepted = False
			await self.send_action_notice(interaction, str(error))
		except Match.DoesNotExist:
			await self.send_action_notice(interaction, "This match no longer exists.")
			await interaction.edit_original_response(content="Match no longer available.", embeds=[], view=None)
			return False
		await self.showTool(interaction)
		return accepted

	async def clear_missing_match(self, interaction, is_message=False, is_context=False):
		"""Removes controls for a match deleted by another supported action."""
		if self.matchDb:
			self.bot.matches.pop(self.matchDb.pk, None)
		self.matchDb = None
		content = {"content": "Match no longer available.", "embeds": [], "view": None}
		if is_message:
			await interaction.edit(**content)
		elif is_context:
			await interaction.interaction.edit_original_response(**content)
		else:
			await interaction.edit_original_response(**content)

	async def showTool(self, interaction=None):
		files = []
		is_message = isinstance(interaction, discord.Message)
		is_ctx = hasattr(interaction, 'interaction') and hasattr(interaction, 'command')

		if interaction:
			if is_message:
				self.msg = interaction
			elif is_ctx:
				if not interaction.interaction.response.is_done():
					await interaction.defer()
				self.msg = await interaction.interaction.original_response()
			else:
				if not interaction.response.is_done():
					await interaction.response.defer()
				self.msg = interaction.message
			try:
				await sync_to_async(self.save_match)()
			except Match.DoesNotExist:
				await self.clear_missing_match(interaction, is_message, is_ctx)
				return
		else:
			interaction = self.msg #Live reload
			is_message = True
			try:
				await sync_to_async(self.load_match)()
			except Match.DoesNotExist:
				await self.clear_missing_match(interaction, is_message=True)
				return
		
		self.render_state = await sync_to_async(get_match_state_token)(self.matchDb) if self.matchDb else None
		recovery_error, recovery_allowed = await sync_to_async(self.load_recovery_status)()
		view = DiscordMatchView(self, recovery_error=recovery_error, recovery_allowed=recovery_allowed)
		await view.init()
		if recovery_error:
			embed = discord.Embed(title="Match history needs staff review", description=recovery_error)
			if recovery_allowed:
				instruction = "Reopen the recorded result first." if self.complete else "Remove the last recorded action to discard invalid history. Earlier records are preserved."
			else:
				instruction = "Correct the match configuration or player assignments in the administration page."
			embed.add_field(name="Recovery", value=instruction, inline=False)
			embed.set_footer(text=f"Match ID: {self.matchDb.pk}")
			embeds = [embed]
		else:
			embeds = [await self.genMatchEmbed()]
		if self.matchDb and self.complete and not recovery_error:
			embeds.append(await self.genScreenEmbed())
			for issue in self.screen_review:
				embeds.append(issue.embed)
				if issue.attachment:
					files.append(await issue.attachment())

		if is_message:
			await interaction.edit(embeds=embeds, content=None, view=view, files=files, attachments=[])
		elif is_ctx:
			await interaction.interaction.edit_original_response(embeds=embeds, content=None, view=view, files=files, attachments=[])
		else:
			await interaction.edit_original_response(embeds=embeds, content=None, view=view, files=files, attachments=[])

	def load_recovery_status(self):
		"""Keeps staff correction controls available when strict history is invalid."""
		if not self.is_corp_cup or self.matchDb.players.count() != 2:
			return None, False
		try:
			load_corp_context(self.matchDb)
		except MatchActionError as error:
			try:
				context = load_corp_context(self.matchDb, validate_history=False)
			except MatchActionError:
				return str(error), False
			return str(error), bool(context.rounds or context.actions or self.complete)
		return None, False

	def load_match(self):
		if isinstance(self.matchDb, str):
			self.matchDb = Match.objects.select_related().get(pk=self.matchDb)
			print(f"Reattached to on-going match {self.matchDb}")
		else:
			self.matchDb = Match.objects.select_related().get(pk=self.matchDb.id)
			print(f"Refreshing current match {self.matchDb}")
		self.channel = self.bot.get_channel(self.matchDb.channel.id)
		self.guild = self.channel.guild
		self.bracket = self.matchDb.group.bracket

	def save_match(self):
		if self.group:
			metadata = {
				"message": self.msg.id if self.msg else None,
				"channel_id": self.channel.id if self.channel else None,
				"referee_id": self.referee.id if self.referee else None,
			}
			with transaction.atomic():
				if self.matchDb._state.adding:
					for field, value in metadata.items():
						setattr(self.matchDb, field, value)
					self.matchDb.save(force_insert=True)
				else:
					current = Match.objects.select_for_update().get(pk=self.matchDb.pk)
					missing = {
						field: value for field, value in metadata.items()
						if getattr(current, field) is None and value is not None
					}
					if missing:
						Match.objects.filter(pk=current.pk).update(**missing)
			self.matchDb = Match.objects.select_related("group__bracket__ruleset").get(pk=self.matchDb.pk)
			self.bracket = self.matchDb.group.bracket
			self.bot.matches[self.matchDb.id] = self

	@property
	def is_corp_cup(self):
		return bool(self.matchDb and self.matchDb.is_corp_cup)

	def add_ban(self, player, chart):
		self.matchDb.add_ban(player, chart)

	def add_round(self):
		self.matchDb.add_round()

	def add_save(self, player, chart):
		self.matchDb.add_save(player, chart)

	def remove_round(self):
		if self.current_round.id:
			self.current_round.delete()
		return self.current_round #return new current round

	def remove_ban(self):
		if self.bans.latest().id:
			self.bans.latest().delete()

	@property
	def bans(self):
		if self.matchDb:
			return self.matchDb.bans.select_related('chart', 'player').all()
		else:
			return []

	@property
	def complete(self) -> bool:
		if isinstance(self.matchDb, Match):
			return self.matchDb.complete
		else:
			return False

	@property
	def current_round(self):
		if self.matchDb:
			return self.matchDb.current_round
		else:
			return None

	@property
	def defer(self):
		if self.matchDb:
			return self.matchDb.defer
		else:
			return False

	@property
	def effective_bans(self):
		return self.matchDb.effective_bans

	@property
	def finished(self) -> bool:
		if not self.matchDb:
			return False
		if self.score[0] == self.ruleset.wins_needed or self.score[1] == self.ruleset.wins_needed:
			return True
		else:
			return False

	@property
	def formatted_bans(self):
		bans1 = self.matchDb.high_seed_bans if not self.defer else self.matchDb.low_seed_bans
		bans2 = self.matchDb.low_seed_bans if not self.defer else self.matchDb.high_seed_bans
		ply1 = self.matchDb.high_seed if not self.defer else self.matchDb.low_seed
		ply2 = self.matchDb.low_seed if not self.defer else self.matchDb.high_seed
		bantb = None
		if len(bans1) > self.ruleset.num_bans:
			bantb = bans1.pop()
		elif len(bans2) > self.ruleset.num_bans:
			bantb = bans2.pop()
		outStr = self.format_bans_player(ply1, bans1)
		outStr += self.format_bans_player(ply2, bans2)
		if bantb:
			outStr += f"***TIEBREAKER BAN***\n{bantb.player.ch_name} bans {bantb.chart.tournament_name}"
		return outStr

	def format_bans_player(self, seed, bans):
		outStr = f"**{seed.player_ch_name} Bans{"/Saves" if self.ruleset.ban_ruleset == "bansave" else ""}**\n"
		for i in range(0, self.ruleset.num_bans):
			try:
				outStr += f"{bans[i].num + 1} - {bans[i].chart.tournament_name}{" - SAVED" if bans[i].saved else ""}\n"
			except IndexError:
				outStr += "--\n"
		return outStr

	@property
	def formatted_rounds(self):
		outStr = ""
		for i, rnd in enumerate(self.rounds):
			if i == self.ruleset.num_rounds - 1:
				outStr += "**TIEBREAKER**\n"

			outStr += f"{('`' + rnd.picked.ch_name + '` picks ') if rnd.picked else 'Played Chart: '}{rnd.chart.tournament_name if rnd.chart else '---'}"
			if rnd.winner:
				outStr += f" - `{rnd.winner}` wins!"
			outStr+= "\n"
		if (self.matchDb and self.matchDb.finished):
			outStr += f"\n**`{self.matchDb.winner}` WINS!**"
		return outStr

	@property
	def group(self):
		if self.matchDb:
			return self.matchDb.group
		else:
			return None

	@property
	def picking_player(self):
		return self.matchDb.picking_player

	@property
	def rev_seeds(self):
		return self.matchDb.rev_seeds

	@property
	def rounds(self):
		if self.matchDb:
			return self.matchDb.rounds.all()
		else:
			return []

	@property
	def ruleset(self):
		if self.bracket:
			return self.bracket.ruleset
		else:
			return None

	@property
	def score(self) -> list:
		return self.matchDb.score_int

	@property
	def score_str(self) -> str:
		return self.matchDb.score

	@property
	def seeding(self):
		if self.matchDb:
			if self.matchDb.rev_seeds:
				return self.matchDb.players.all().reverse()
			else:
				return self.matchDb.players.all()
		else:
			return []

	@property
	def seeding_mgr(self):
		if self.matchDb:
			return self.matchDb.players
		else:
			return None

	@property
	def setlist(self) -> list:
		if self.bracket:
			return self.bracket.setlist
		else:
			return None

	@property
	def setlist_remaining(self):
		if self.matchDb:
			return self.matchDb.setlist_remaining
		else:
			return []

	@property
	def tiebreaker(self) -> bool:
		return self.matchDb.tiebreaker

	async def genScreenEmbed(self):
		embed = discord.Embed(colour=0xFFFF66)
		embed.title = "Upload screenshots"
		embed.add_field(name="Directions", value="Players/Refs for this match - click upload screenshots and submit. List will update as valid ones are found.", inline=False)
		noneStr = ""
		validStr = ""
		for rnd in self.rounds:
			if rnd.steg and len(rnd.steg.players) > 0:
				validStr += f"{rnd.chart.name}\n"
			else:
				noneStr += f"{rnd.chart.name}\n"
		embed.add_field(name="Valid Screenshots Submitted", value=validStr, inline=False)
		embed.add_field(name="Screenshots Missing", value=noneStr, inline=False)
		return embed

	async def genMatchEmbed(self):
		embed = discord.Embed(colour=0x3FFF33)
		embed.set_author(name=f"Ref: {self.referee.display_name}", icon_url=self.referee.display_avatar.url)

		if not self.bracket:
			embed.title = f"{self.tourney.short_name}"
			embed.add_field(name="Bracket Select", value=f"Select which bracket the match is for", inline=False)
		elif not self.group:
			embed.title = f"{self.bracket.name}"
			embed.add_field(name="Group Select", value=f"Select which group the match is for", inline=False)
		elif len(self.seeding) < self.ruleset.num_players:
			embed.title = f"{self.group}"
			if len(self.group.seeding.all().filter(player__is_active=True, eliminated=False)) > 25 and len(self.seeding_search) < 2:
				embed.add_field(name="Player Select", value=f"Group too large for select. Click search to find players.", inline=False)
			else:
				embed.add_field(name="Player Select", value=f"Select which players the match is for", inline=False)
		else:
			embed.title = f"{self.group}\n{self.matchDb.short_name}"
			embed.add_field(name="Match VS", value=f"{self.matchDb.high_seed.mention} vs {self.matchDb.low_seed.mention}")
			embed.add_field(name="Score", value=self.score_str, inline=False)
			if self.defer:
				embed.add_field(name="Deferral", value=f"{self.matchDb.high_seed.player.ch_name} has deferred.")
			if len(self.bans) < self.ruleset.total_bans:
				embed.add_field(name="Bans", value=f"{self.formatted_bans}\nSelect next ban", inline=False)
			elif self.ruleset.tb_ruleset == 'banpick' and len(self.rounds) == self.ruleset.num_rounds:
				if len(self.bans) < self.ruleset.total_bans + 1:
					embed.add_field(name="Bans", value=f"{self.formatted_bans}\nSelect next ban", inline=False)
				else:
					embed.add_field(name="Bans", value=self.formatted_bans, inline=False)
			else:
				embed.add_field(name="Bans", value=self.formatted_bans, inline=False)
		if len(self.rounds) > 0:
			embed.add_field(name="Rounds", value=self.formatted_rounds, inline=False)
		if self.matchDb:
			embed.set_footer(text=f"Match ID: {self.matchDb.id}")
		return embed

class TourneyCmds(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	tourney = discord.SlashCommandGroup('tourney','Clone Hero Tournament Commands')

	#@tourney.command(name="exhibition", description="Reftool for Exhibition Matches", integration_types={discord.IntegrationType.guild_install})
	#async def discordExhibMatchCmd(self, ctx):
	#	pass

	@tourney.command(name='match',description='Match reporting done within discord', integration_types={discord.IntegrationType.guild_install})
	async def discordMatchCmd(self, ctx):
		match = DiscordMatch(self.bot, message=ctx)
		if await match.init():
			await match.showTool(ctx)

def setup(bot):
	bot.add_cog(TourneyCmds(bot))
