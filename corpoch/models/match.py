import uuid
from itertools import chain

from django.db import models

from django_pydantic_field import SchemaField
from polymorphic.managers import PolymorphicForwardManyToOneDescriptor, PolymorphicReverseManyToOneDescriptor, Nullable
from polymorphic.models import PolymorphicModel

from corpoch import settings
from corpoch.types import CHART_CATEGORIES, StegScreenshot, CH_Name
from corpoch.dbot.view.helpers import build_stats_embed, build_full_stats_embed

from .tournament import TournamentPlayer
from .charts import Chart, BYOSChart

def steg_upload_dir(self, filename):
	return f"matches/{str(self.match.group).replace(' ', '').replace(":", "")}/{self.match.id}/{uuid.uuid1()}.{filename.split('.')[-1]}"

class MatchAbstract(models.Model):
	"""
	Represents a Match played for a Tournament. 
	"""
	id = models.CharField(primary_key=True, verbose_name="Match ID", max_length=40, default=uuid.uuid1, help_text="UUID for a match.")
	defer = models.BooleanField(verbose_name="Deferral Used", default=False, help_text="Was a deferral used.")
	started_on = models.DateTimeField(verbose_name="Match Start Time", auto_now_add=True, help_text="Match start time.")
	ended_on = models.DateTimeField(verbose_name="Match End Time", null=True, blank=True, help_text="Match end time.")
	complete = models.BooleanField(verbose_name="'Complete'", default=False, help_text="Match is finalized, but waiting for screenshots.")
	finished = models.BooleanField(verbose_name="Finished", default=False, help_text="Match is finished and has all screenshots/data.")
	submitted = models.BooleanField(verbose_name="GSheet", default=False, help_text="Match is uploaded to GSheet for Tournament.")
	channel = models.ForeignKey("dbot.Channels", verbose_name="Ref-Tool Discord Channel", on_delete=models.SET_NULL, null=True, blank=True, help_text="Discord Channel the reftool was ran in for a match.")
	message = models.BigIntegerField(verbose_name="Ref-Tool Discord Message ID", null=True, blank=True, help_text="Discord snowflake ID of the message for a match.")
	exhibition = models.BooleanField(default=False, help_text="Is a match an exhibition match (not an official match).")
	rev_seeds = models.BooleanField(default=False, help_text="Inverses the seed order. Meant to be used where rules may not have high seed first.") #ONLY tested for 2 players

	class Meta:
		app_label = 'corpoch'
		abstract = True

	@property
	def is_corp_cup(self):
		"""Reports whether this official match explicitly selects CORP Cup rules."""
		return isinstance(self, Match) and self.ruleset.tb_ruleset == "corp_cup"

	@property
	def ongoing(self):
		players = self.players.all()
		return self.finished == False and players.count() > 1

	@property
	def high_seed(self):
		if self.rev_seeds:
			return self.players.last()
		else:
			return self.players.first()

	@property
	def low_seed(self):
		players = self.players.all()

		if players.count() > 1:
			if self.rev_seeds:
				return players.first()
			else:
				return players.last()
		return None

	#Bans/Rounds are shorthands for all Ban/Match objects
	@property
	def bans(self):
		return self.match_bans.select_related().all()

	@property
	def rounds(self):
		return self.match_rounds.select_related().all()

	@property
	def current_round(self):
		if self.rounds.count() != 0:
			return self.rounds.latest()
		else:
			return None

	@property
	def previous_round(self):
		if self.rounds.count() > 1:
			return self.rounds.all().order_by('-num')[1]
		else:
			return None

	@property
	def high_seed_bans(self):
		if self.high_seed:
			return [ban for ban in self.bans if ban.player_id == self.high_seed.player_id]
		else:
			return []

	@property
	def low_seed_bans(self):
		if self.low_seed:
			return [ban for ban in self.bans if ban.player_id == self.low_seed.player_id]
		else:
			return []

	@property
	def rounds(self):
		return self.match_rounds.all()

	@property
	def ruleset(self):
		return self.group.bracket.ruleset

	@property
	def version(self):
		self.tournament.config.version

	@property
	def high_seed_score(self):
		if self.high_seed and self.low_seed:
			score = 0
			for round in self.rounds:
				if round.winner_id and round.winner_id == self.high_seed.player_id:
					score += 1
			return score
		else:
			return 0
		
	@property
	def low_seed_score(self):
		if self.high_seed and self.low_seed:
			score = 0
			for round in self.rounds:
				if round.winner_id and round.winner_id == self.low_seed.player_id:
					score += 1
			return score
		else:
			return 0

	def get_score(self, asint=False):
		if self.high_seed and self.low_seed:
			score1 = 0
			score2 = 0
			for round in self.rounds:
				if round.winner_id:
					if round.winner_id == self.high_seed.player_id:
						score1 += 1
					elif round.winner_id == self.low_seed.player_id:
						score2 += 1

			if asint:
				return [score1, score2]
			else:
				return f"{score1} - {score2}"
		else:
			if asint:
				return [0, 0]
			else:
				return "0 - 0"

	@property
	def score(self):
		return self.get_score()

	@property
	def score_int(self):
		return self.get_score(asint=True)

	@property
	def tiebreaker(self) -> bool:
		#Is match currently in tiebreaker state, is not if match played TB
		if self.score_int[0] == self.ruleset.wins_needed - 1 and self.score_int[1] == self.ruleset.wins_needed - 1:
			return True
		else:
			return False

	@property
	def full_name(self):
		return f"{self.tournament.short_name} - {self.bracket.name} - {self.group.name} - {self.short_name}"

	@property
	def short_name(self):
		outStr = ""
		if self.players.count() >= 1:
			outStr += f"{self.high_seed.player_ch_name}({self.high_seed.seed})"
		if self.players.count() == 2:
			outStr += f" vs {self.low_seed.player_ch_name}({self.low_seed.seed})"
		return outStr

	@property
	def short_name_no_seeds(self):
		outStr = ""
		if self.players.count() >= 1:
			outStr += f"{self.high_seed.player_ch_name}"
		if self.players.count() == 2:
			outStr += f" vs {self.low_seed.player_ch_name}"
		return outStr

	@property
	def picking_player(self):
		"""
		Returns the player that's currently "up" to select the next step in the match
		"""
		if self.is_corp_cup:
			from corpoch.match_actions import corp_picker_id
			player_id = corp_picker_id(self)
			return TournamentPlayer.objects.using(self._state.db or "default").get(pk=player_id) if player_id is not None else None
		if self.rounds.count() == 0 and self.bans.count() != self.ruleset.total_bans:
			if self.ruleset.ban_ruleset == "bansave":
				if self.bans.count() == 0 or self.bans.count() == 3:
					picked = self.high_seed.player
				else:
					picked = self.low_seed.player
			else:
				if self.bans.count() % self.ruleset.num_players == 0:
					if self.defer:
						picked = self.low_seed.player
					else:
						picked = self.high_seed.player
				else:
					if self.defer:
						picked = self.high_seed.player
					else:
						picked = self.low_seed.player
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'refdecide':
			picked = None
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'csc':
			picked = None
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'banpick':
			if self.bans.count() > self.ruleset.total_bans:
				picked = self.previous_round.loser
			else:
				picked = self.current_round.winner
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'bansave':
			if self.setlist_remaining.count() > 1 and self.bans.count() > self.ruleset.total_bans:
				picked = self.previous_round.loser
			else:
				picked = self.current_round.winner
		elif self.ruleset.pick_ruleset == "loserpicks":
			if self.rounds.count() == 1:
				if self.defer:
					picked = self.low_seed.player
				else:
					picked = self.high_seed.player
			else:
				picked = self.previous_round.loser
		else: #Alternate pick
			if self.rounds.count() == 1:
				if self.defer:
					prevPicked = self.low_seed.player
				else:
					prevPicked = self.high_seed.player
			else:
				prevPicked = self.previous_round.picked 
			if self.high_seed.player == prevPicked:
				picked = self.high_seed.player
			else:
				picked = self.low_seed.player
		return picked

	@property
	def player_saved(self):
		"""
		Has the current player used a save
		"""
		picked = self.picking_player

		if isinstance(self, Match):
			try:
				bans = self.match_bans.objects.filter(player=picked, save=True)
				return True
			except MatchBan.DoesNotExist:
				return False
		else: #Exhibition Match
			try:
				bans = self.exhibition_bans.objects.filter(player=picked, save=True)
				return True
			except ExhibitionMatchBan.DoesNotExist:
				return False

	@property
	def setlist(self):
		if self.group:
			return self.group.bracket.setlist
		else:
			return None

	@property
	def effective_bans(self):
		bans = self.bans
		for ban in bans:
			if ban.saved:
				bans = bans.exclude(chart=ban.chart)

		return bans

	@property
	def setlist_remaining(self):
		"""
		Returns the rest of the setlist that hasn't been played for the match
		"""
		if self.is_corp_cup:
			from corpoch.match_actions import corp_chart_choices
			return self.setlist.filter(pk__in=corp_chart_choices(self)).select_related("icon")
		#Ensure saves can't be "rebanned" in ban-phase of a match, otherwise keep "saved" songs available
		if self.ruleset.ban_ruleset == "bansave" and (not self.tiebreaker or self.bans.count() > self.ruleset.total_bans):
			bans = self.effective_bans.values_list("chart", flat=True)
		else:
			bans = self.bans.values_list('chart', flat=True)
		rounds = self.rounds.values_list('chart', flat=True)
		if self.tiebreaker:
			if self.ruleset.tb_ruleset == 'refdecide':
				charts = self.setlist.select_related('icon').exclude(pk__in=list(chain(bans, rounds)))
			elif self.ruleset.tb_ruleset == "banpick":
				charts = self.setlist.select_related('icon').filter(tiebreaker=True).exclude(pk__in=bans)
			elif self.ruleset.tb_ruleset == "bansave":
				charts = self.setlist.select_related('icon').exclude(pk__in=list(chain(bans, rounds)))
			else:
				charts = self.setlist.select_related('icon').filter(tiebreaker=True)
		else:
			if self.ruleset.boss_present and not self.ruleset.boss_active:
				charts = self.setlist.select_related('icon').filter(tiebreaker=False, boss=False).exclude(pk__in=list(chain(bans, rounds)))
			else:
				charts = self.setlist.select_related('icon').filter(tiebreaker=False).exclude(pk__in=list(chain(bans, rounds)))
		return charts

	def add_ban(self, player: TournamentPlayer, chart: Chart):
		if self.is_corp_cup:
			from corpoch.match_actions import get_match_state_token, record_opening_action
			record_opening_action(
				self.pk, player.pk, chart.pk,
				expected_state=get_match_state_token(self), using=self._state.db or "default",
			)
			self.refresh_from_db()
			return self.match_bans.latest("num")
		newBan = MatchBan(num=len(self.bans), player=player, chart=chart, match=self)
		newBan.save()
		if self.bans.count() == self.ruleset.total_bans or self.tiebreaker:
			self.add_round()

		return newBan

	def add_save(self, player: TournamentPlayer, chart: Chart):
		if self.is_corp_cup:
			from corpoch.match_actions import get_match_state_token, record_opening_action
			record_opening_action(
				self.pk, player.pk, chart.pk, saved=True,
				expected_state=get_match_state_token(self), using=self._state.db or "default",
			)
			self.refresh_from_db()
			return
		ban = self.add_ban(player, chart)
		ban.saved = True
		ban.save()

	def add_round(self):
		if self.is_corp_cup:
			from corpoch.match_actions import (
				advance_match_revision, create_pending_round, load_corp_context, locked_match,
			)
			with locked_match(self.pk, using=self._state.db or "default") as current:
				if create_pending_round(load_corp_context(current), self._state.db or "default"):
					advance_match_revision(current, self._state.db or "default")
			self.refresh_from_db()
			return
		chart = None
		if len(self.rounds) == 0:
			if self.defer and self.ruleset.ban_ruleset == "deferboth":
				picked = self.low_seed.player
			else:
				picked = self.high_seed.player
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'refdecide':
			picked = None
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'csc':
			fret, strum = 0, 0
			for rnd in self.rounds:
				if rnd.chart.category == "fret":
					fret += 1
				elif rnd.chart.category == "strum":
					strum += 1

			picked = None
			if strum < fret:
				chart = Chart.objects.get(category=CHART_CATEGORIES[3][0], tiebreaker=True, brackets=self.bracket)
			elif fret < strum:
				chart = Chart.objects.get(category=CHART_CATEGORIES[2][0], tiebreaker=True, brackets=self.bracket)
			else:
				chart = Chart.objects.get(category=CHART_CATEGORIES[1][0], tiebreaker=True, brackets=self.bracket)
		elif self.tiebreaker and self.ruleset.tb_ruleset == "bansave":
			if self.setlist_remaining.count() == 1:
				picked = None
				chart = self.setlist_remaining.get()
			else:
				picked = self.current_round.loser
		elif self.tiebreaker and self.ruleset.tb_ruleset == 'banpick':
			picked = self.current_round.loser
		elif self.ruleset.pick_ruleset == "loserpicks":
			picked = self.current_round.loser
		else:
			prevPicked = self.current_round.loser
			if self.high_seed.player == prevPicked:
				picked = self.high_seed.player
			else:
				picked = self.low_seed.player

		if len(self.rounds) > 0:
			self.current_round.save()
		if not self.finished:
			rnd = MatchRound(num=len(self.rounds) + 1, match=self, picked=picked, chart=chart)
			rnd.save()

	def remove_round(self):
		rnd = self.rounds[-1]
		if rnd.id:
			rnd.delete()

	def remove_ban(self):
		ban = self.bans.pop()
		if ban.id:
			ban.delete()

	def __str__(self):
		return self.full_name

	def complete_match(self):
		pass

	def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
		if update_fields is None and not self._state.adding:
			for rnd in self.rounds:
				if rnd.screenshot and (not rnd.steg or len(rnd.steg.players) == 0):
					from corpoch.providers import CHStegTool
					tool = CHStegTool()
					rnd.steg = tool.getStegInfoSync(rnd.screenshot)
		super().save(
			force_insert=force_insert, force_update=force_update,
			using=using, update_fields=update_fields,
		)

class Match(MatchAbstract):
	action_revision = models.PositiveBigIntegerField(
		default=0, editable=False,
		help_text="Invalidates delayed sporting actions after any match correction.",
	)
	tournament = models.ForeignKey("Tournament", related_name="matches", verbose_name="Tournament", on_delete=models.CASCADE, help_text="Tournament")
	players = models.ManyToManyField("GroupSeed", related_name="match_players", verbose_name="Players", blank=True, help_text="Players that participated in a match.")
	loser = models.ForeignKey("TournamentPlayer", related_name="matches_lost", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that lost the match.")
	winner = models.ForeignKey("TournamentPlayer", related_name="matches_won", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that won the match.")
	group = models.ForeignKey("Group", related_name='matches', verbose_name="Group", on_delete=models.CASCADE, help_text="Group the match was played for.")
	referee = models.ForeignKey("DiscordUser", related_name="matches_reffed", verbose_name="Referee", on_delete=models.SET_NULL, db_index=True, blank=True, null=True, help_text="Discord User for the refree of a match.")

	class Meta:
		verbose_name = "Match"
		verbose_name_plural = "Matches"
		ordering = ['-started_on']

	@property
	def bracket(self):
		return self.group.bracket

	@property
	def tournament(self):
		return self.group.bracket.tournament

class ExhibitionMatch(MatchAbstract):
	tournament = models.ForeignKey("Tournament", related_name="exhibition_matches", verbose_name="Tournament", on_delete=models.CASCADE, help_text="Tournament")
	players = models.ManyToManyField("DiscordUser", related_name="exhibition_players", verbose_name="Players", blank=True, help_text="Players that participated in an exhibition match.")
	loser = models.ForeignKey("DiscordUser", related_name="exhibitions_lost", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that lost the exhibition match.")
	winner = models.ForeignKey("DiscordUser", related_name="exhibitions_won", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that won the exhibition match.")
	bracket = models.ForeignKey("Bracket", related_name='exhibition_matches', verbose_name="Bracket", on_delete=models.CASCADE, help_text="Bracket the exhibition match was played for.")
	referee = models.ForeignKey("DiscordUser", related_name="exhibition_matches_reffed", verbose_name="Referee", on_delete=models.SET_NULL, db_index=True, blank=True, null=True, help_text="Discord User for the refree of a match.")

	class Meta:
		verbose_name = "Exhibition Match"
		verbose_name_plural = "Exhibition Matches"
		ordering = ['-started_on']

class MatchRoundAbstract(models.Model):
	"""
	Represents a round of a Match played for a Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID for a round.")
	num = models.PositiveIntegerField(blank=False, null=False, help_text="Round number of a specific match.")
	#w_points = models.PositiveIntegerField(verbose_name="Players", validators=[MinValueValidator(1), MaxValueValidator(5)], default=1)
	#l_points = models.PositiveIntegerField(verbose_name="Players", validators=[MinValueValidator(1), MaxValueValidator(5)], default=0)
	steg = SchemaField(StegScreenshot, verbose_name="Steg Data", null=True, blank=True, help_text="Clone Hero screenshot steg data.")
	screenshot = models.ImageField(upload_to=steg_upload_dir, verbose_name="Screenshot", null=True, blank=True, help_text="Screenshot for a match.")
	created = models.DateTimeField(verbose_name="Created Time", auto_now_add=True, null=True, blank=True, help_text="Timestamp the round was started.")
	chart : PolymorphicForwardManyToOneDescriptor[ Chart | BYOSChart, Chart ] = models.ForeignKey("Chart", verbose_name="Chart Played", null=True, blank=True, on_delete=models.SET_NULL, help_text="Chart that was played.")
	plays : PolymorphicReverseManyToOneDescriptor[ Chart | BYOSChart, Chart ]

	class Meta:
		app_label = 'corpoch'
		abstract = True

	def __str__(self):
		outStr = ""
		if self.picked:
			outStr += f"{self.picked} picks"
		if self.chart:
			outStr += f" {self.chart.name}"
		if self.winner:
			outStr += f" - {self.winner.ch_name} wins"
		return outStr

	def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
		if update_fields is None and self.screenshot and not self.steg:
			from corpoch.providers import CHStegTool
			tool = CHStegTool()
			self.steg = tool.getStegInfoSync(self.screenshot)
		super().save(
			force_insert=force_insert, force_update=force_update,
			using=using, update_fields=update_fields,
		)

	@property
	def is_tiebreaker(self) -> bool:
		if self.num == self.match.bracket.ruleset.num_rounds:
			return True
		else:
			return False

	@property
	def steg_embed(self):
		embed = build_stats_embed(self.steg, f"Match {self.match} - Round {self.num} Results")
		embed.set_thumbnail(url=f"https://{settings.BASE_URL}{self.screenshot.url}")
		embed.set_footer(text=embed.footer.text, icon_url = f"https://{settings.BASE_URL}{self.chart.icon.img.url}")
		return embed

	@property
	def full_steg_embed(self):
		embed = build_full_stats_embed(self.steg, f"Match {self.match} - Round {self.num} FULL Results")
		embed.set_thumbnail(url=f"https://{settings.BASE_URL}{self.screenshot.url}")
		embed.set_footer(text=embed.footer.text, icon_url = f"https://{settings.BASE_URL}{self.chart.icon.img.url}")
		return embed

class MatchRound(MatchRoundAbstract):
	selection_kind = models.CharField(
		max_length=16, default="unknown",
		choices=[("unknown", "Unknown"), ("player", "Player"), ("referee", "Referee"), ("automatic", "Automatic")],
		help_text="Verified origin of the surviving chart selection.",
	)
	match = models.ForeignKey(Match, related_name="match_rounds", verbose_name="Match ID", on_delete=models.CASCADE, null=True, blank=True, help_text="Match the round was played for.")
	picked = models.ForeignKey("TournamentPlayer", related_name="picks", verbose_name="Picker", on_delete=models.CASCADE, blank=True, null=True, help_text="Player that picked the chart played.")
	winner = models.ForeignKey("TournamentPlayer", related_name="rounds_won", verbose_name="Winner", null=True, blank=True, on_delete=models.SET_NULL, help_text="Winner of a round.")
	loser = models.ForeignKey("TournamentPlayer", related_name="rounds_lost", verbose_name="Loser", null=True, blank=True, on_delete=models.SET_NULL, help_text="Loser of a round.")

	class Meta:
		verbose_name = "Group Match Round"
		verbose_name_plural = "Group Match Rounds"
		ordering = ['num']
		get_latest_by = 'num'

class ExhibitionMatchRound(MatchRoundAbstract):
	match = models.ForeignKey(ExhibitionMatch, related_name="exhibition_rounds", verbose_name="Match ID", on_delete=models.CASCADE, null=True, blank=True, help_text="Match the round was played for.")
	picked = models.ForeignKey("DiscordUser", related_name="exhibition_picks", verbose_name="Picker", on_delete=models.CASCADE, blank=True, null=True, help_text="Player that picked the chart played.")
	winner = models.ForeignKey("DiscordUser", related_name="exhibition_rounds_won", verbose_name="Winner", null=True, blank=True, on_delete=models.SET_NULL, help_text="Winner of a round.")
	loser = models.ForeignKey("DiscordUser", related_name="exhibition_rounds_lost", verbose_name="Loser", null=True, blank=True, on_delete=models.SET_NULL, help_text="Loser of a round.")

	class Meta:
		verbose_name = "Exhibition Match Round"
		verbose_name_plural = "Exhibition Match Rounds"
		ordering = ['num']
		get_latest_by = 'num'

class MatchBanAbstract(models.Model):
	"""
	Represents a player ban for a Match played in a Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID for a ban.")
	num = models.PositiveIntegerField(blank=False, null=False, help_text="Order in which a ban was picked.")
	created = models.DateTimeField(verbose_name="Created Time", auto_now_add=True, null=True, blank=True, help_text="Timestamp a ban was chosen.")
	saved = models.BooleanField(verbose_name="Save", default=False, help_text="Was this a save (for Ban-Save ruleset)")
	chart : PolymorphicForwardManyToOneDescriptor[ Chart | BYOSChart, Chart ] = models.ForeignKey("Chart", verbose_name="Chart Banned", null=True, blank=True, on_delete=models.SET_NULL, help_text="Chart that was banned.")
	bans : PolymorphicReverseManyToOneDescriptor[ Chart | BYOSChart, Chart ]

	class Meta:
		app_label = 'corpoch'
		abstract = True

	def __str__(self):
		return str(self.chart.name)

	@property
	def get_player_ch_name(self):
		return str(self.player.ch_name)

class MatchBan(MatchBanAbstract):
	action_phase = models.CharField(
		max_length=16, default="unknown",
		choices=[("unknown", "Unknown"), ("opening", "Opening"), ("tiebreaker", "Tiebreaker")],
		help_text="Verified phase of the surviving ban or save.",
	)
	match = models.ForeignKey(Match, related_name="match_bans", verbose_name="Match ID", on_delete=models.CASCADE, null=True, blank=True, help_text="Match the round was played for.")
	player = models.ForeignKey("TournamentPlayer", related_name="player_bans", verbose_name="Player", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that chose a ban.")

	class Meta:
		verbose_name = "Match Ban"
		verbose_name_plural = "Match Bans"
		ordering = ['num']
		get_latest_by = 'num'

class ExhibitionMatchBan(MatchBanAbstract):
	match = models.ForeignKey(ExhibitionMatch, related_name="exhibition_bans", verbose_name="Match ID", on_delete=models.CASCADE, null=True, blank=True, help_text="Match the round was played for.")
	player = models.ForeignKey("DiscordUser", related_name="exhibition_bans", verbose_name="Player", null=True, blank=True, on_delete=models.SET_NULL, help_text="Player that chose a ban.")

	class Meta:
		verbose_name = "Exhibition Match Ban"
		verbose_name_plural = "Exhibition Match Bans"
		ordering = ['num']
		get_latest_by = 'num'
