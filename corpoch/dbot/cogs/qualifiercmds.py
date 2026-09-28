import base64, discord, io, json, os, pydantic, pytz

from discord.ext import commands
from discord.ui import *
from discord.enums import ComponentType, InputTextStyle
from django.db.models.functions import Now
from django.utils import timezone
from django.core.files.base import ContentFile

from corpoch.types import PlayerConfig, CH_Name
from corpoch.models import Tournament, Bracket, TournamentPlayer, Qualifier, QualifierSubmission, Chart, DiscordUser
from corpoch.providers import CHOpt, CHStegTool

class QualifierSelect(discord.ui.Select):
	def __init__(self, quali):
		self.quali = quali
		self.retOpts = {}

	async def init(self):
		qualis = []
		for qualifier in self.quali.qualifiers:
			self.retOpts[qualifier.id] = qualifier
			qualis.append(discord.SelectOption(label=str(qualifier.name), value=str(qualifier.id)))
		super().__init__(max_values=1, options=qualis, custom_id="bracket_sel")

	async def callback(self, interaction: discord.Interaction):
		qualifier = self.retOpts[self.values[0]]
		if qualifier.channel and qualifier.channel != self.quali.ctx.channel.id:
			await interaction.respond(f"Please run command in channel (https://discord.com/{self.quali.tourney.guild}/{qualifier.channel}) to submit!", ephemeral=True, delete_after=10)
		else:
			await interaction.response.defer(invisible=True)
			self.quali.qualifier = qualifier
			await self.quali.show()

class ScreenshotModal(discord.ui.DesignerModal):
	def __init__(self):
		self.screen = None
		file = discord.ui.Label("Screenshot of your qualifier run to upload", discord.ui.FileUpload(max_values=1, required=True))
		super().__init__(discord.ui.TextDisplay("Screenshot Submission"), file, title="Qualifier Screenshot")

	async def callback(self, interaction: discord.Interaction):
		self.screen = self.children[1].item.values[0]
		await interaction.respond("Processing, wait for embed to update", ephemeral=True, delete_after=10)

class QualiPlayerSel(discord.ui.Select):
	def __init__(self, quali):
		self.quali = quali
		opts = []
		for i, player in enumerate(self.quali.steg.output.players):
			opts.append(discord.SelectOption(label=player.profile_name, value=str(i)))
		super().__init__(max_values=1, options=opts, custom_id="bracket_sel")

	async def callback(self, interaction: discord.Interaction):
		#Purge all non-selected players from steg data
		self.quali.steg.output.players = [ self.quali.steg.output.players[int(self.values[0])] ]
		await interaction.response.defer(invisible=True)
		self.parent.children.remove(self)
		await self.quali.show()

class DiscordQualifierView(discord.ui.View):
	def __init__(self, ctx):
		super().__init__(timeout = 360, disable_on_timeout=True)
		self.ctx = ctx
		self.qualifier = None
		self.qualifiers = []
		self.prev_subs = []
		self.tourney = None
		self.steg = None
		self.screen = None
		self.user = None

		cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancelBtn")
		cancel.callback = self.cancelBtn
		self.add_item(cancel)

		self.upload = discord.ui.Button(label="Upload Screenshot", custom_id="screenBtn")
		self.upload.callback = self.uploadBtn
		self.upload.disabled = True
		self.add_item(self.upload)

		self.submit = discord.ui.Button(label="Submit", style=discord.ButtonStyle.green, custom_id="submitBtn")
		self.submit.callback = self.submitBtn
		self.submit.disabled = True
		self.add_item(self.submit)

	async def init(self):
		await self.ctx.defer(ephemeral=True)
		try:
			self.tourney = await Tournament.objects.select_related('config').aget(guild=self.ctx.guild.id, active=True)
		except Tournament.DoesNotExist:
			await self.ctx.respond("There are no active tournaments running in this server at this time.", ephemeral=True)
			return

		async for qualifier in Qualifier.objects.select_related().all().filter(tournament=self.tourney, end_time__gte=timezone.now()):
			self.qualifiers.append(qualifier)

		await self.show(init=True)

	async def show(self, init=False):
		self.prev_subs = []
		embeds = []
		if self.qualifier == None and len(self.qualifiers) > 1:
			qualiSel = QualifierSelect(self)
			await qualiSel.init()
			self.add_item(qualiSel)
			embeds.append(self.buildQualiSelEmbed())
		elif self.qualifier == None and len(self.qualifiers) == 0:
			await self.ctx.respond("There are no active qualifiers running in this server at this time.", ephemeral=True)
			return
		elif self.qualifier == None:#Only one qualifier
				self.qualifier = self.qualifiers[0]
				self.qualifier.chart = await self.qualifier.charts.afirst()

		if self.qualifier:
			self.upload.disabled = False
			try:
				self.user = await DiscordUser.objects.aget(id=self.ctx.user.id)
			except DiscordUser.DoesNotExist:
				self.user = DiscordUser(id=self.ctx.user.id,
					global_name=self.ctx.user.global_name if self.ctx.user.global_name else self.ctx.user.display_name,
					avatar=self.ctx.user.display_avatar.url
				)
				await self.user.asave()
			try:
				self.ply = await TournamentPlayer.objects.aget(user=self.user, tournament=self.qualifier.tournament)
				async for qual in QualifierSubmission.objects.select_related().all().filter(player=self.ply, qualifier=self.qualifier).order_by("-submit_time"):
					self.prev_subs.append(qual)
			except TournamentPlayer.DoesNotExist:
				self.ply = TournamentPlayer(user=self.user, tournament=self.tourney, config=PlayerConfig(names_list=[]))

			self.num_subs = len(self.prev_subs)
			embeds.append(self.buildRulesEmbed())
		if self.num_subs > 0:
			embeds.append(self.buildSubmissionsEmbed())
		if self.steg:
			embeds.append(self.steg.steg_embed)
			if len(self.steg.output.players) > 1:
				embeds.append(self.buildPlySelEmbed())
				self.add_item(QualiPlayerSel(self))
			else:
				if self.ply.ch_name == "</Null>":
					embeds.append(self.buildNoticeEmbed())
				embeds.append(self.buildSubmitEmbed())
				self.submit.disabled = False

		if init:
			await self.ctx.respond(embeds=embeds, view=self)
		else:
			await self.ctx.edit(embeds=embeds, view=self)

	async def cancelBtn(self, interaction: discord.Interaction):
		await interaction.response.edit_message(content="Closing", embed=None, view=None, delete_after=1)
		self.stop()

	async def uploadBtn(self, interaction: discord.Interaction):
		print(f"QUALIFIER: {self.qualifier}: {self.ctx.user.display_name} is uploading a screenshot")
		modal = ScreenshotModal()
		await interaction.response.send_modal(modal=modal)
		await modal.wait()
		steg = CHStegTool()
		await steg.getStegInfo(modal.screen)
		if not steg.output:
			print(f"QUALIFIER: {self.ctx.user.display_name} upload not valid CH screenshot")
			await interaction.followup.send("Screenshot is not a valid in-game screenshot. Please use a screenshot taken with the select button on the results screen or from using auto-screenshots!", ephemeral=True, delete_after=10)
			return
		plySteg = []
		for i, ply in enumerate(steg.output.players):
			current_player_id = self.ply.id if (self.ply and self.ply.id) else None
			name_taken_by_other = False
			target_name = ply.profile_name

			other_players = TournamentPlayer.objects.filter(tournament=self.qualifier.tournament).exclude(user=self.user)

			async for other_ply in other_players:
				if not other_ply.config:
					continue

				try:
					if isinstance(other_ply.config, dict):
						other_config = PlayerConfig(**other_ply.config)
					else:
						other_config = other_ply.config

					for item in other_config.names_list:
						db_name = item.ch_name
						if db_name == target_name:
							name_taken_by_other = True
							break 
				except pydantic.ValidationError:
					continue

				if name_taken_by_other:
					break

			if name_taken_by_other:
				print(f"QUALIFIER: Removing player {ply.profile_name} already in tournament {self.tourney.short_name}")
				continue

			plySteg.append(ply)

		steg.output.players = plySteg
		auto_matched_players = [p for p in steg.output.players if p.profile_name in self.ply.ch_aliases]

		if auto_matched_players:
			print(f"QUALIFIER: Auto-matched uploader to known aliases: {[p.profile_name for p in auto_matched_players]}")
			steg.output.players = auto_matched_players
		try:
			playedChart = await self.qualifier.charts.aget(md5=steg.output.checksum)
		except Chart.DoesNotExist:
			print(f"QUALIFIER: {self.qualifier}: {self.ctx.user.display_name} uploaded screenshot with checksum {steg.output.checksum} that did not match any charts")
			await interaction.followup.send("Screenshot is not for the qualifier chart.", ephemeral=True, delete_after=10)
			await self.show()
			return

		if steg.output.game_version != self.tourney.config.version:
			print(f"QUALIFIER: {self.qualifier}: {self.ctx.user.display_name} screenshot version {steg.output.game_version} does not match tourney version {self.tourney.config.version}")
			await interaction.followup.send(f"Qualifier screenshot is not Clone Hero version {self.tourney.config.version}", ephemeral=True, delete_after=10)
		elif steg.output.playback_speed != playedChart.speed:
			print(f"QUALIFIER: {self.qualifier}: {self.ctx.user.display_name} screenshot speed {steg.output.playback_speed}% does not match speed of qualifier: {playedChart.speed}%")
			await interaction.followup.send(f"Uploaded screenshot speed ({steg.output.playback_speed}%) does not match speed of qualifier: {playedChart.speed}%", ephemeral=True, delete_after=10)
		elif set(steg.output.players[0].modifiers) != set(playedChart.modifiers_steg):
			await interaction.followup.send(f"Uploaded Screenshot has incorrect modifiers {steg.output.players[0].modifiers} does not match modifiers for qualifier: {playedChart.modifiers_steg}", ephemeral=True, delete_after=10)
			print(f"QUALIFIER: Screenshot player {steg.output.players[0].profile_name} has incorrect modifiers {steg.output.players[0].modifiers} for qualifier: {playedChart.modifiers_steg}")
		else:
			print(f"QUALIFIER: {self.ctx.user.display_name} screenshot {steg.img_name} accepted")
			self.screen = modal.screen
			self.steg = steg
		await self.show()

	async def submitBtn(self, interaction: discord.Interaction):
		print(f"QUALIFIER: {self.qualifier}: {self.ctx.user.display_name} submitted a screenshot")
		await interaction.response.defer()
		self.ply.name = self.ctx.user.display_name
		final_ch_name = self.steg.output.players[0].profile_name

		if self.ply:
			self.ply.ch_name = final_ch_name

		await self.user.asave()
		await self.ply.asave()
		quali = QualifierSubmission(player=self.ply, qualifier=self.qualifier, steg=self.steg.output)
		quali.screenshot.save(self.screen.filename, ContentFile(await self.screen.read()))
		await quali.asave()
		await self.ctx.interaction.delete_original_response()
		await interaction.followup.send(f"{self.ctx.user.mention} submitted a qualifier for {self.qualifier}!", ephemeral=not self.qualifier.output)

	def buildQualiSelEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0xFF8000)
		embed.title = "Multiple active qualifiers!"
		embed.add_field(name="Directions", value="Pick a qualifier to submit for.")
		return embed

	def buildPlySelEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0xFF8000)
		embed.title = "Multiple players in qualifier screenshot!"
		embed.add_field(name="Directions", value="In the drop-down below, pick which player you are.")
		return embed

	def buildNoticeEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0xEEFF00)
		embed.title = "Notices"
		embed.add_field(name="Player Name", value="The player-name in this submission will be used to track progress through this tournament\nYou will need to use it for all official matches (minus formatting/spaces)", inline=False)
		embed.add_field(name="Screenshots Notice", value="Matches for this tournament will be tracked using in-game taken screenshots.\nPlease ensure that you have automatic screenshots enabled!", inline=False)
		return embed

	def buildSubmitEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0xEEFF00)
		embed.title = "Submit"
		embed.add_field(name="Directions", value="If you agree to everything, hit submit to complete your submission!", inline=False)
		return embed

	def buildSubmissionsEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0x00FF00)
		embed.title = "Current submissions"
		retStr = ""
		if self.num_subs == 0:
			retStr += "No current submissions!"
		else:
			for i, sub in enumerate(self.prev_subs):
				retStr += f"Submission {i + 1}: {sub.steg.players[0].score}\n"
		embed.add_field(name="Scores", value=retStr, inline=False)
		return embed

	def buildRulesEmbed(self) -> discord.Embed:
		embed = discord.Embed(colour=0xFF2800)
		embed.title = "Qualifier Submission Rules"
		embed.add_field(name=f"{self.tourney.name} Rules", value=self.tourney.config.rules, inline=False)
		embed.add_field(name="Submissions Deadline", value=f"<t:{int(self.qualifier.end_time.timestamp())}>", inline=False)
		if self.qualifier.required_submissions > 1:
			subs_met = '✅' if self.qualifier.required_submissions <= self.num_subs else '❌'
			embed.add_field(name="Required Submissions", value=f"This qualifier requires {self.qualifier.required_submissions} submissions\n\nYou've submitted: {self.num_subs} time(s) {subs_met}")
		embed.add_field(name=f"Qualifier Rules", value=self.qualifier.rules, inline=False)
		if self.qualifier.form_link and self.qualifier.form_link != "":
			embed.add_field(name="Qualifier Form Link", value=f"[Link Here]({self.qualifier.form_link})", inline=False)
		embed.add_field(name="Qualifier Chart Link", value=f"[Link Here]({self.qualifier.chart.url})", inline=False)
		embed.add_field(name="Agreement", value="By submitting a qualifier, you are agreeing to these rules")
		return embed

class QualifierCmds(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	@commands.slash_command(name='qualifier', description='Submit a qualifier score for a tournament/bracket', integration_types={discord.IntegrationType.guild_install})
	async def qualifierSubmitCmd(self, ctx):
		if ctx.guild:
			view = DiscordQualifierView(ctx)
			await view.init()
		else:
			await ctx.respond("Can't run this command outside of a guild!", ephemeral=True)

def setup(bot):
	bot.add_cog(QualifierCmds(bot))
