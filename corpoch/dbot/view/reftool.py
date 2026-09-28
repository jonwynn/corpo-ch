import logging
import uuid
from itertools import chain

import discord
from asgiref.sync import sync_to_async
from django.utils import timezone

from corpoch.match_actions import (
    assign_match_players,
    cancel_match,
    finalize_match,
    get_match_state_token,
    record_opening_action,
    record_round_winner,
    select_chart,
    undo_match_action,
)
from corpoch.match_publication import publish_round_evidence
from corpoch.types import StegScreenshotPlayerDummy
from corpoch.models import GroupSeed, Match
from corpoch.dbot.view.helpers import get_chart_emoji


def create_screenshot_tool():
    """Loads screenshot processing only when an upload requires it."""
    from corpoch.providers import CHStegTool

    return CHStegTool()


def publish_evidence_file(round_snapshot, filename, content, metadata, expected_state):
    """Stores a file before publishing evidence against the captured match state.

    :param MatchRound round_snapshot: Round captured before external processing
    :param str filename: Submitted attachment name
    :param object content: Open screenshot content
    :param object metadata: Validated screenshot metadata
    :param str expected_state: Captured sporting-state token
    :return: Freshly loaded match"""
    expected_screenshot = round_snapshot.screenshot.name or ""
    round_snapshot.screenshot.save(filename, content, save=False)
    screenshot_name = round_snapshot.screenshot.name
    try:
        return publish_round_evidence(
            round_snapshot.match_id, round_snapshot.pk, round_snapshot.chart_id,
            screenshot_name, metadata, expected_screenshot=expected_screenshot,
            expected_state=expected_state,
        )
    except Exception:
        # Only the newly stored, unpublished file is removed. Storage calls
        # remain outside the publication service's database transaction.
        round_snapshot.screenshot.storage.delete(screenshot_name)
        raise


def capture_evidence_context(match_id):
    """Loads evidence bindings before the first attachment or decoding await.

    :param str match_id: Official match identifier
    :return: Match, round snapshots and participant seeds"""
    match = Match.objects.select_related("group__bracket__tournament__config").get(pk=match_id)
    if not match.complete:
        raise ValueError("Finalize the match before uploading screenshots.")
    rounds = tuple(match.rounds.select_related("chart"))
    players = tuple(match.players.select_related("player"))
    return match, rounds, players

class MatchScreenModal(discord.ui.DesignerModal):
	def __init__(self, match):
		self.match = match
		self.screens = None
		file = discord.ui.Label("Match Screenshot Submission", discord.ui.FileUpload(max_values=min(self.match.rounds.count(), 10), required=True))
		super().__init__(discord.ui.TextDisplay("Screenshots"), file, title="Match Screenshots", custom_id="screenModal")

	async def callback(self, interaction: discord.Interaction):
		await interaction.respond("Processing, wait for embed to update", ephemeral=True, delete_after=10)
		self.screens = self.children[1].item.values
		self.stop()

class SeedSearchModal(discord.ui.DesignerModal):
	def __init__(self, match):
		self.match = match
		super().__init__(discord.ui.TextDisplay("Search for players.\nAccepts partial case-insensitive discord names."), title="Search Players", custom_id="searchModal")
		self.add_item(discord.ui.Label("Player 1 Search", discord.ui.InputText(placeholder="Discord Name", required=True, style=discord.InputTextStyle.short)))
		self.add_item(discord.ui.Label("Player 2 Search", discord.ui.InputText(placeholder="Discord Name", required=True, style=discord.InputTextStyle.short)))

	async def callback(self, interaction: discord.Interaction):
		query1 = self.match.group.seeding.select_related('player').all().filter(player__is_active=True, eliminated=False, player__name__icontains=self.children[1].item.value)
		query2 = self.match.group.seeding.select_related('player').all().filter(player__is_active=True, eliminated=False, player__name__icontains=self.children[2].item.value)

		if len(query1) < 1:
			await interaction.respond(f"Search `{self.children[1].item.value}` found no results.", ephemeral=True, delete_after=10)
		elif len(query2) < 1:
			await interaction.respond(f"Search `{self.children[2].item.value}` found no results.", ephemeral=True, delete_after=10)
		else:
			retList = list(chain(query1, query2))
			if len(retList) > 25:
				await interaction.respond("Search(es) too broad, please narrow your search.", ephemeral=True, delete_after=10)
			else:
				await interaction.response.defer(invisible=True)
				self.match.seeding_search = retList

		self.stop()

class BanSelect(discord.ui.Select):
	def __init__(self, match):
		self.match = match
		self.retOpts = {}
		self.expected_state = match.render_state
		self.match_id = match.matchDb.pk
		self.player_id = match.picking_player.pk

	async def init(self):
		opts = []
		charts = self.match.setlist_remaining

		for chart in charts:
			emoji = await get_chart_emoji(self.match.bot, chart)
			value = str(chart.pk) if self.match.is_corp_cup else chart.md5
			opts.append(discord.SelectOption(label=str(chart.tournament_name), description=chart.description, emoji=emoji, value=value))
			self.retOpts[value] = chart
		else:
			placeholder = f"{self.match.picking_player.ch_name} Bans"
		super().__init__(placeholder=placeholder, max_values=1, options=opts, custom_id="ban_sel")

	async def callback(self, interaction: discord.Interaction):
		chart = self.retOpts[self.values[0]]
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, record_opening_action, self.match_id,
				self.player_id, chart.pk, expected_state=self.expected_state,
			)
			return
		self.match.add_ban(self.match.picking_player, chart)
		await self.match.showTool(interaction)

class SongRoundSelect(discord.ui.Select):
	def __init__(self, match, disabled):
		self.match = match
		self.round = self.match.current_round
		self.dis = disabled
		self.retOpts = {}
		self.expected_state = match.render_state
		self.match_id = match.matchDb.pk
		picker = match.picking_player
		self.player_id = picker.pk if picker else None

	async def init(self):
		picked = self.match.picking_player
		if getattr(self.round, "selection_kind", None) == "automatic":
			selStr = "Automatic tiebreaker"
		elif picked:
			selStr = f"{picked.ch_name} Picks"
		else:
			selStr = "Pick Song"

		if self.round.chart:
			selStr += f" - {self.round.chart.tournament_name}"

		opts = []
		if self.match.setlist_remaining.count() == 0:
			#If tiebreaker is pre-determined, force that into the options ensuring opts isn't 0 long
			chart = self.match.current_round.chart
			emoji = await get_chart_emoji(self.match.bot, chart)
			value = str(chart.pk) if self.match.is_corp_cup else chart.md5
			self.retOpts[value] = chart
			opts.append(discord.SelectOption(label=chart.tournament_name, value=value, description=chart.description, emoji=emoji))
		else:
			async for chart in self.match.setlist_remaining:
				value = str(chart.pk) if self.match.is_corp_cup else chart.md5
				self.retOpts[value] = chart
				emoji = await get_chart_emoji(self.match.bot, chart)
				opts.append(discord.SelectOption(label=chart.tournament_name, value=value, description=chart.description, emoji=emoji))
		super().__init__(placeholder=selStr, max_values=1, options=opts, custom_id="roundsong_sel", disabled=self.dis)

	async def callback(self, interaction: discord.Integration):
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, select_chart, self.match_id,
				self.retOpts[self.values[0]].pk,
				player_id=self.player_id, expected_state=self.expected_state,
			)
			return
		self.round.chart = self.retOpts[self.values[0]]
		await self.round.asave(update_fields=["chart"])
		await self.match.showTool(interaction)

class PlayerRoundSelect(discord.ui.Select):
	def __init__(self, match, disabled):
		self.match = match
		self.round = self.match.current_round
		self.dis = disabled
		self.retOpts = {}
		self.expected_state = match.render_state
		self.match_id = match.matchDb.pk

	async def init(self):
		opts = []
		for seed in self.match.seeding:
			auuid = str(uuid.uuid1())
			self.retOpts[auuid] = seed
			opts.append(discord.SelectOption(label=f"{seed.player.ch_name} ({seed.seed})", value=auuid, description=f"@{seed.player.name}"))
		super().__init__(placeholder="Round Winner", max_values=1, options=opts, custom_id="roundwin_sel", disabled=self.dis)

	async def callback(self, interaction: discord.Integration):
		winner = self.retOpts[self.values[0]]
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, record_round_winner, self.match_id,
				winner.player_id, expected_state=self.expected_state,
			)
			return
		if winner == self.match.seeding[0]:
			self.round.loser = self.match.seeding[1].player
		else:
			self.round.loser = self.match.seeding[0].player
		self.round.winner = winner.player
		await self.round.asave(update_fields=["winner", "loser"])
		if not self.match.finished and (not self.match.tiebreaker or not self.match.ruleset.bannable_tb):
			self.match.add_round()
		elif self.match.ruleset.tb_ruleset == "bansave" and self.match.setlist_remaining.count() == 1:
			self.match.add_round() #Separate check for possible bansave ruleset ban-phase
		await self.match.showTool(interaction)

class BracketSelect(discord.ui.Select):
	def __init__(self, match):
		self.match = match
		self.retOpts = {}

	async def init(self):
		brackets = []
		async for bracket in self.match.tourney.brackets.select_related('ruleset').all().filter(is_active=True):
			self.retOpts[bracket.name] = bracket
			brackets.append(discord.SelectOption(label=bracket.name))
		super().__init__(max_values=1, options=brackets, custom_id="bracket_sel")

	async def callback(self, interaction: discord.Integration):
		self.match.bracket = self.retOpts[self.values[0]]
		await self.match.showTool(interaction)

class GroupSelect(discord.ui.Select):
	def __init__(self, match):
		self.match = match
		self.retOpts = {}

	async def init(self):
		groups = []
		async for group in self.match.bracket.groups.select_related().all():
			self.retOpts[group.name] = group
			groups.append(discord.SelectOption(label=group.name))
		super().__init__(max_values=1, options=groups, custom_id="group_sel")

	async def callback(self, interaction: discord.Integration):
		group = self.retOpts[self.values[0]]
		self.match.matchDb = Match(id=uuid.uuid1(), group=group)
		print(f"REF: {self.match.referee.global_name} starting match {self.match.matchDb}")
		await self.match.showTool(interaction)

class PlayerSelect(discord.ui.Select):
	def __init__(self, match):
		self.match = match
		self.retOpts = {}
		self.expected_state = match.render_state
		self.match_id = match.matchDb.pk

	async def init(self):
		seeding = []
		seeds = self.match.group.seeding.select_related('player').all().filter(player__is_active=True, eliminated=False)
		if len(seeds) > 25:
			seeds = self.match.seeding_search

		for seed in seeds:
			self.retOpts[str(seed.user.id)] = seed
			seeding.append(discord.SelectOption(label=str(seed), value=str(seed.user.id), description=f"@{seed.player.name}"))
		plys = self.match.ruleset.num_players
		super().__init__(placeholder="Players", min_values=plys, max_values=plys, options=seeding, custom_id="player_sel")

	async def callback(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, assign_match_players, self.match_id,
				[self.retOpts[value].pk for value in self.values],
				expected_state=self.expected_state,
			)
			return
		self.values.sort(key=lambda ply: self.retOpts[ply].seed)
		for ply in self.values:
			self.match.seeding_mgr.add(self.retOpts[ply])
		await self.match.showTool(interaction)

class RoundReview:
	def __init__(self, match: Match, msg, screen, steg, round_snapshot=None, expected_state=None):
		self.match = match
		self.msg = msg
		self.screen = screen
		self.steg = steg
		self.round = round_snapshot or self.match.rounds.all().select_related('chart').get(chart__md5=self.steg.checksum)
		self.expected_state = expected_state or get_match_state_token(match)
		if len(self.steg.players) < self.match.ruleset.num_players:
			self.reason = 'Player Disconnect'
		else:
			self.reason = ""

	async def attachment(self) -> discord.File:
		return await self.screen.to_file()

	@property
	def embed(self) -> discord.Embed:
		embed = discord.Embed(colour=0x3FFF33)
		embed.title = f"Problem Round"
		if len(self.steg.players) < self.match.ruleset.num_players:
			embed.add_field(name="Image name", value=self.screen.filename, inline=False)
			embed.add_field(name="Played Chart", value=self.round.chart.tournament_name, inline=False)
			outStr = ""
			for ply in self.steg.players:
				outStr += f"{ply.profile_name}\n"
			embed.add_field(name="Found Players", value=outStr, inline=False)
			embed.set_thumbnail(url=f"attachment://{self.screen.filename}")

		embed.add_field(name="Issue", value=self.reason, inline=False)
		return embed

	async def fix(self):
		metadata = self.steg.model_copy(deep=True)
		players = list(metadata.players)
		if len(self.steg.players) < self.match.ruleset.num_players:
			missing = self.match.players.all()
			for seed in self.match.players.all():
				for check in self.steg.players:
					if seed.player.check_ch_name(check.profile_name):
						missing = missing.exclude(player=seed.player)

			for seed in missing:
				players.append(StegScreenshotPlayerDummy(profile_name=seed.player.ch_name, error_reason=self.reason))

		metadata.players = players
		screen = await self.attachment()
		try:
			self.match = await sync_to_async(publish_evidence_file)(
				self.round, screen.filename, screen.fp, metadata, self.expected_state,
			)
		finally:
			screen.close()
		await self.msg.delete()
		return self.match

class DiscordMatchView(discord.ui.View):
	def __init__(self, match, recovery_error=None, recovery_allowed=False):
		super().__init__(timeout = None)
		self.log = logging.getLogger(__name__)
		self.match = match
		self.recovery_error = recovery_error
		self.recovery_allowed = recovery_allowed
		self.referee = match.referee
		self.current_round = match.current_round
		self.expected_state = match.render_state
		self.match_id = match.matchDb.pk if match.matchDb else None
		picker = match.picking_player if match.matchDb and len(match.seeding) == 2 and not match.complete and not recovery_error else None
		self.picker_id = picker.pk if picker else None
		self.saved_chart_id = match.bans.last().chart_id if match.matchDb and match.bans.exists() else None

		self.cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancelBtn")
		self.cancel.callback = self.cancelBtn

		self.back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, custom_id="backBtn")
		if len(self.match.seeding) == 0:
			self.back.disabled = True
		self.back.callback = self.backBtn

		self.review_items = {}

		self.defer = discord.ui.Button(label="Defer", style=discord.ButtonStyle.secondary, custom_id="deferBtn")
		self.defer.callback = self.deferBtn

		self.seed_swap = discord.ui.Button(label="Swap Seeding", style=discord.ButtonStyle.secondary, custom_id="seedSwapBtn")
		self.seed_swap.callback = self.seedSwapBtn

		if self.match.matchDb and self.match.ruleset.ban_ruleset == "bansave" and self.match.bans.count() > 0:
			label = f"Save {self.match.bans.last().chart}"[:75]				
			self.save = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id="saveBtn")
			self.save.callback = self.saveBtn

		self.search = discord.ui.Button(label="Player Select", style=discord.ButtonStyle.secondary, custom_id="searchBtn")
		self.search.callback = self.searchBtn

		if self.match.player_input:
			label = "Player Input ✅"
		else:
			label = "Player input ❌"
		self.plyin = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id="plyinBtn")
		self.plyin.callback = self.plyinBtn

		self.upload = discord.ui.Button(label="Upload Screenshots", style=discord.ButtonStyle.secondary, custom_id="uploadBtn")
		self.upload.callback = self.uploadBtn

		self.submit = discord.ui.Button(label='Submit Match', style=discord.ButtonStyle.green, custom_id="submitBtn")
		self.submit.callback = self.submitBtn
		self.submit.disabled = True
		self.is_uploading = False

	async def setup_round_player_sels(self):
		sngDis = True if self.match.current_round.chart else False
		sngSel = SongRoundSelect(self.match, sngDis)
		sngSel.expected_state = self.expected_state
		plyDis = True if not self.match.current_round.chart else False
		plySel = PlayerRoundSelect(self.match, plyDis)
		plySel.expected_state = self.expected_state
		await sngSel.init()
		await plySel.init()
		self.add_item(sngSel)
		self.add_item(plySel)

	async def init(self):
		if self.recovery_error:
			self.back.label = "Reopen recorded match" if self.match.complete else "Remove last recorded action"
			self.back.disabled = not self.recovery_allowed
			self.add_item(self.back)
			if not self.match.complete:
				self.add_item(self.cancel)
			return
		if self.match.complete:
			if self.match.is_corp_cup:
				self.back.label = "Reopen match"
				self.add_item(self.back)
			self.add_item(self.upload)
			for i, item in enumerate(self.match.screen_review):
				button = discord.ui.Button(label=f"Approve {i + 1}", style=discord.ButtonStyle.secondary, custom_id=f"reviewBtn_{i}")
				button.callback = self.reviewBtn
				self.review_items[i] = item
				self.add_item(button)
		else:
			self.add_item(self.cancel)

		if not self.match.bracket:
			sel = BracketSelect(self.match)
			await sel.init()
			self.add_item(sel)
		elif not self.match.group:
			self.add_item(self.back)
			sel = GroupSelect(self.match)
			await sel.init()
			self.add_item(sel)
		elif len(self.match.seeding) < self.match.ruleset.num_players:
			self.add_item(self.back)
			if len(self.match.group.seeding.select_related('player').all().filter(eliminated=False, player__is_active=True)) > 25:
				self.add_item(self.search)
			if len(self.match.group.seeding.select_related('player').all().filter(eliminated=False, player__is_active=True)) <= 25 or len(self.match.seeding_search) > 1: 
				sel = PlayerSelect(self.match)
				sel.expected_state = self.expected_state
				await sel.init()
				self.add_item(sel)
		elif len(self.match.bans) < self.match.ruleset.total_bans:
			self.add_item(self.back)
			self.add_item(self.plyin)

			if 'defer' in self.match.ruleset.ban_ruleset and len(self.match.bans) == 0:
				self.add_item(self.defer)
			if not self.match.is_corp_cup and self.match.ruleset.seed_inversions and self.match.bans.count() == 0:
				self.add_item(self.seed_swap)
			if self.match.ruleset.ban_ruleset == "bansave" and not self.match.bans.filter(player=self.match.picking_player, saved=True).exists():
				if self.match.bans.count() == 1 or self.match.bans.count() == 3:
					self.add_item(self.save)

			sel = BanSelect(self.match)
			sel.expected_state = self.expected_state
			await sel.init()
			self.add_item(sel)
		elif not self.match.complete:
			self.add_item(self.back)
			self.add_item(self.plyin)
			self.add_item(self.submit)
			if not self.match.finished and not self.match.tiebreaker:
				await self.setup_round_player_sels()
			elif not self.match.finished and self.match.tiebreaker:
				if self.match.ruleset.bannable_tb:
					if len(self.match.bans) == self.match.ruleset.total_bans and (not self.match.ruleset.tb_ruleset == "bansave" or self.match.setlist_remaining.count() > 1):
						sel = BanSelect(self.match)
						await sel.init()
						self.add_item(sel)
					else:
						await self.setup_round_player_sels()
				elif self.match.ruleset.tb_ruleset == 'csc':
					sel = PlayerRoundSelect(self.match, False)
					await sel.init()
					self.add_item(sel)
				else:
					await self.setup_round_player_sels()
			else:
				self.submit.disabled = False

	async def interaction_check(self, interaction: discord.Interaction):
		caller = interaction.custom_id
		if self.match.is_corp_cup and self.expected_state:
			try:
				current = await Match.objects.aget(pk=self.match_id)
				current_state = await sync_to_async(get_match_state_token)(current)
			except Match.DoesNotExist:
				await self.match.send_action_notice(interaction, "This match no longer exists.")
				return False
			if current_state != self.expected_state:
				await self.match.send_action_notice(interaction, "The match changed. The controls have been refreshed.")
				await self.match.showTool(interaction)
				return False
		if interaction.user in self.match.bot.owners:
			return True
		if interaction.user.id == self.match.referee.id:
			return True
		if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
			return True
		if self.recovery_error:
			await self.match.send_action_notice(interaction, "Only tournament staff can correct recorded match history.")
			return False
		try:
			player = await self.match.matchDb.players.aget(player__user__id=interaction.user.id)
		except GroupSeed.DoesNotExist:
			await interaction.response.send_message("You are not the ref for, nor a player in this match!", ephemeral=True, delete_after=10)
			return False
		if self.match.complete:#If match is complete and player is part of match
			if caller == "uploadBtn":
				return True
			await self.match.send_action_notice(interaction, "Only tournament staff can review or reopen recorded results.")
			return False
		if self.match.player_input and (caller == "roundsong_sel" or caller == "ban_sel" or caller == "saveBtn"):
			if self.match.picking_player and self.match.picking_player.user.id == interaction.user.id:
				return True
			else:
				await interaction.response.send_message("Not your turn to pick!", ephemeral=True, delete_after=10)
				return False
		elif not self.match.player_input:
			await interaction.response.send_message("Player input is disabled!", ephemeral=True, delete_after=10)
			return False
		else: #match.player_input is on but is from a caller object that isn't allowed for player input
			await interaction.response.send_message("Selector/Button not allowed for player input!!", ephemeral=True, delete_after=10)
			return False

	async def plyinBtn(self, interaction: discord.Interaction):
		self.match.player_input = not self.match.player_input
		await self.match.showTool(interaction)

	async def backBtn(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, undo_match_action, self.match_id,
				expected_state=self.expected_state,
			)
			return
		if self.match.rounds.count() > 0:
			if self.current_round.is_tiebreaker:
				if self.current_round.winner:
					self.current_round.winner = None
					self.current_round.loser = None
				elif self.match.ruleset.pickable_tb and self.current_round.chart:
					self.current_round.chart = None
				else:
					self.current_round = self.match.remove_round()
					if self.match.ruleset.bannable_tb:
						if self.match.ruleset.tb_ruleset != "bansave" or self.match.ruleset.total_bans < self.match.bans.count():
							self.match.remove_ban()
					else:
						self.current_round.winner = None
			elif self.current_round.winner:
				self.current_round.winner = None
				self.current_round.loser = None
			elif self.current_round.chart and (not self.match.tiebreaker or self.match.ruleset.pickable_tb):
				self.current_round.chart = None
			else:
				self.current_round = self.match.remove_round()
				if self.current_round:
					self.current_round.winner = None
			if self.match.rounds.count() > 0:
				await self.current_round.asave(update_fields=["chart", "winner", "loser"])
			else: #If we removed the last round, also remove a ban
				self.match.remove_ban()
		elif self.match.rounds.count() == 0 and self.match.bans.count() > 0:
			self.match.remove_ban()
		elif self.match.seeding.count() > 0 and self.match.bans.count() == 0:
			self.match.seeding_mgr.clear()

		await self.match.showTool(interaction)

	async def cancelBtn(self, interaction: discord.Interaction):
		if self.match.confirm_cancel:
			if self.match.is_corp_cup:
				try:
					await sync_to_async(cancel_match)(self.match_id, expected_state=self.expected_state)
				except ValueError as error:
					await self.match.send_action_notice(interaction, str(error))
					await self.match.showTool(interaction)
					return
			await interaction.response.edit_message(content="Closing", embed=None, view=None, delete_after=10)
			if self.match.matchDb and not self.match.is_corp_cup:
				await self.match.matchDb.adelete()
			self.match.bot.matches.pop(self.match_id, None)
			self.stop()
		else:
			self.match.confirm_cancel = True
			await interaction.response.send_message(content="Are you sure you want to cancel? Click cancel again to confirm", ephemeral=True, delete_after=10)

	async def deferBtn(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.send_action_notice(interaction, "CORP Cup does not permit deferral.")
			return
		self.match.matchDb.defer = not self.match.defer
		await self.match.matchDb.asave(update_fields=["defer"])
		await self.match.showTool(interaction)

	async def seedSwapBtn(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.send_action_notice(interaction, "CORP Cup uses the recorded higher seed first.")
			return
		self.match.matchDb.rev_seeds = not self.match.matchDb.rev_seeds
		await self.match.matchDb.asave(update_fields=["rev_seeds"])
		await self.match.showTool(interaction)

	async def saveBtn(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, record_opening_action, self.match_id,
				self.picker_id, self.saved_chart_id, saved=True,
				expected_state=self.expected_state,
			)
			return
		self.match.add_save(self.match.picking_player, self.match.bans.last().chart)
		await self.match.showTool(interaction)

	async def searchBtn(self, interaction: discord.Interaction):
		modal = SeedSearchModal(self.match)
		await interaction.response.send_modal(modal)
		await modal.wait()
		await self.match.showTool(interaction)

	async def uploadBtn(self, interaction: discord.Interaction):
		if self.is_uploading:
			await self.match.send_action_notice(interaction, "A screenshot upload is already in progress.")
			return
		try:
			source_match, rounds, seeds = await sync_to_async(capture_evidence_context)(self.match_id)
		except (ValueError, Match.DoesNotExist) as error:
			await self.match.send_action_notice(interaction, str(error))
			return
		if not rounds:
			await self.match.send_action_notice(interaction, "There are no recorded rounds to submit.")
			return
		if all(round_record.screenshot for round_record in rounds):
			await self.match.finishMatch(interaction)
			return
		expected_state = self.expected_state
		self.is_uploading = True
		try:
			modal = MatchScreenModal(source_match)
			await interaction.response.send_modal(modal)
			await modal.wait()
			if not modal.screens:
				return
			for screen in modal.screens:
				tool = create_screenshot_tool()
				try:
					steg = await tool.getStegInfo(screen)
					matches = [row for row in rounds if row.chart and row.chart.md5 == steg.checksum]
					if len(matches) != 1:
						raise ValueError("The screenshot must identify exactly one recorded round.")
					round_snapshot = matches[0]
					chart = round_snapshot.chart
					if round_snapshot.screenshot:
						raise ValueError("This round already has a screenshot.")
					if chart.speed != steg.playback_speed:
						raise ValueError("The screenshot playback speed does not match the chart.")
					if steg.game_version != source_match.tournament.config.version:
						raise ValueError("The screenshot game version does not match the tournament.")
					matched_players = set()
					for player in steg.players:
						participants = [seed.player_id for seed in seeds if seed.player.check_ch_name(player.profile_name)]
						if len(participants) != 1 or participants[0] in matched_players:
							raise ValueError("Screenshot players must identify distinct match participants.")
						matched_players.add(participants[0])
					if any(set(player.modifiers) != set(chart.modifiers_steg) for player in steg.players):
						raise ValueError("Screenshot modifiers do not match the selected chart.")
					if len(steg.players) > source_match.ruleset.num_players:
						raise ValueError("The screenshot has too many players.")
					if len(steg.players) < source_match.ruleset.num_players:
						message = await interaction.followup.send(
							"The screenshot has missing players and needs referee review.",
						)
						self.match.screen_review.append(RoundReview(
							source_match, message, screen, steg,
							round_snapshot=round_snapshot, expected_state=expected_state,
						))
						continue
					with open(tool.img_path, "rb") as content:
						self.match.matchDb = await sync_to_async(publish_evidence_file)(
							round_snapshot, screen.filename, content, steg, expected_state,
						)
				except (ValueError, Match.DoesNotExist) as error:
					await interaction.followup.send(f"{screen.filename}: {error}", ephemeral=True, delete_after=10)
				except Exception:
					self.log.exception("Screenshot processing failed for match %s", self.match_id)
					await interaction.followup.send(
						f"{screen.filename}: screenshot processing failed. Please retry.",
						ephemeral=True, delete_after=10,
					)
		finally:
			self.is_uploading = False
		await self.match.finishMatch(interaction)

	async def reviewBtn(self, interaction: discord.Interaction):
		index = int(interaction.custom_id.split('_')[-1])
		reviewed = self.review_items.get(index)
		if reviewed is None or reviewed not in self.match.screen_review:
			await self.match.send_action_notice(interaction, "This screenshot review has already been handled.")
			return
		await interaction.response.defer()
		try:
			self.match.matchDb = await reviewed.fix()
		except (ValueError, Match.DoesNotExist) as error:
			await self.match.send_action_notice(interaction, str(error))
			await self.match.showTool(interaction)
			return
		self.match.screen_review.remove(reviewed)
		await self.match.finishMatch(interaction)

	async def submitBtn(self, interaction: discord.Interaction):
		if self.match.is_corp_cup:
			await self.match.apply_match_action(
				interaction, finalize_match, self.match_id,
				expected_state=self.expected_state,
			)
			return
		self.match.matchDb.winner = self.match.current_round.winner
		self.match.matchDb.loser = self.match.current_round.loser
		self.match.matchDb.ended_on = timezone.now()
		self.match.matchDb.complete = True
		await self.match.matchDb.asave(update_fields=["winner", "loser", "ended_on", "complete"])
		await self.match.showTool(interaction)
