import math, uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django_pydantic_field import SchemaField
from django.utils import timezone

from corpoch.types import CH_VERSIONS, TB_RULESETS, PICK_RULESETS, BAN_RULESETS, StegScreenshot, PlayerConfig, CH_Name

def quali_upload_dir(self, filename):
	return f"qualifiers/{str(self.qualifier).replace(' ', '').replace(':', '')}/{self.id}/{uuid.uuid1()}.{filename.split('.')[-1]}"

class Tournament(models.Model):
	"""
	Represents a Clone Hero Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID of a tournament.")
	guild = models.ForeignKey("dbot.Guilds", verbose_name="Discord Guild", db_index=True, on_delete=models.SET_NULL, null=True, help_text="Discord Guild the tournament is running in.")
	name = models.CharField(verbose_name="Name", max_length=128, default="New Tournament", help_text="Full name of the tournament.")
	short_name = models.CharField(verbose_name="Short Name", max_length=16, default="NT1", help_text="Short/abbreviated name for this tournament.")
	role = models.ForeignKey("dbot.Roles", verbose_name="Participant Role", on_delete=models.SET_NULL, null=True, blank=True, db_index=True, help_text="Discord Role to assign to players in this tournament.")
	active = models.BooleanField(verbose_name="In-Progress", default=False, help_text="Is tournament active/running.")

	class Meta:
		verbose_name = "Tournament"
		verbose_name_plural = "Tournaments"
		app_label = 'corpoch'

	def __str__(self):
		return self.name

	def active_players(self):
		return self.players.filter(active=True)

	def has_revealed_setlist(self) -> bool:
		for bracket in self.brackets.all():
			if bracket.revealed:
				return True
		return False

	def save(self):
		is_new = self.pk is None
		super().save()
		TournamentConfig.objects.get_or_create(tournament=self) if is_new else None

class TournamentConfig(models.Model):
	"""
	Represents a configuration for a Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID of a config.")
	tournament = models.OneToOneField(Tournament, related_name="config", verbose_name="Tournament Configuration", on_delete=models.CASCADE, help_text="Tournament a configuration is for.")
	rules = models.TextField(verbose_name="Rules", max_length=1024, default="Some rules go here", help_text="Rules shown to players.")
	gsheet = models.URLField(verbose_name="Match Reporting Google Sheet", null=True, blank=True, help_text="GSheet URL to post match results to.")
	version = models.CharField(verbose_name="Clone Hero Version", choices=CH_VERSIONS, max_length=32, default=CH_VERSIONS[0][0], help_text="Clone Hero verison the tournament is using.")
	byos = models.BooleanField(verbose_name="Bring Your Own Song Rules", default=False, help_text="Are BYOS charts being used")

	class Meta:
		verbose_name = "Configuration"
		verbose_name_plural = "Configurations"
		app_label = 'corpoch'

	def __str__(self):
		return f"{self.tournament.name}"

class Bracket(models.Model):
	"""
	Represents a Bracket for a Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID of a bracket.")
	name = models.CharField(verbose_name="Bracket Name", max_length=128, default="New Bracket", help_text="Name of the bracket")
	tournament = models.ForeignKey(Tournament, related_name="brackets", on_delete=models.CASCADE, verbose_name="Tournament", help_text="Tournament this bracket is for.")
	score_log = models.ForeignKey("dbot.Channels", verbose_name="Score Log Channel", on_delete=models.SET_NULL, null=True, blank=True)
	is_active = models.BooleanField(verbose_name="Bracket Active", default=False, help_text="Is bracket active/matches are allowed to start.")
	revealed = models.BooleanField("Setlist Revealed", default=False, help_text="Is the setlist revealed. If True, the charts for a setlist are made available.")
	role = models.ForeignKey("dbot.Roles", verbose_name="Bracket Role", null=True, on_delete=models.SET_NULL, blank=True, db_index=True, help_text="Discord role to assign to players in this bracket.")

	class Meta:
		verbose_name = "Bracket"
		app_label = 'corpoch'

	@property
	def short_name(self):
		return self.name

	def __str__(self):
		return f"{self.tournament.short_name} - {self.name}"

	def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
		is_new = self.pk is None
		super().save()
		BracketRules.objects.create(bracket=self) if is_new or not self.ruleset else None

class BracketRules(models.Model):
	"""
	Represents a Ruleset for a Tournament Bracket. 
	"""
	bracket = models.OneToOneField(Bracket, primary_key=True, related_name="ruleset", on_delete=models.CASCADE, verbose_name="Bracket Rules", null=False, help_text="Bracket this ruleset is for.")
	num_players = models.PositiveIntegerField(verbose_name="Players", validators=[MinValueValidator(2), MaxValueValidator(4)], default=2, help_text="Number of players in a match.")
	num_bans = models.IntegerField(verbose_name="Bans Per-Player", validators=[MinValueValidator(1), MaxValueValidator(4)], default=1, help_text="Number of bans per-player in a match.")
	num_rounds = models.PositiveIntegerField(verbose_name="Best Of", validators=[MinValueValidator(3), MaxValueValidator(25)], default=7, help_text="Maximum number of rounds per-match.")
	boss_active = models.BooleanField(verbose_name="Boss Songs Active", default=False, help_text="Are 'Boss' songs allowed to be picked.")
	boss_bannable = models.BooleanField(verbose_name="Boss Songs Bannable", default=False, help_text="Are 'Boss' songs bannable.")
	ban_ruleset = models.CharField(verbose_name="Match Bans Ruleset", choices=BAN_RULESETS, max_length=32, default=BAN_RULESETS[0][0], help_text="Ruleset to determine how bans work.")
	pick_ruleset = models.CharField(verbose_name="'Who Picks' Ruleset", choices=PICK_RULESETS, max_length=32, default=PICK_RULESETS[0][0], help_text="Ruleset to determine who picks the next song for a round.")
	tb_ruleset = models.CharField(verbose_name="Tiebreaker Ruleset", choices=TB_RULESETS, max_length=32, default=TB_RULESETS[0][0], help_text="Ruleset to determine how tiebreakers player.")
	seed_inversions = models.BooleanField(verbose_name="Match Seed Inversions", default=False, help_text="Allow seeding inversions for certain rulesets.")
	byos_group_only = models.BooleanField(verbose_name="Pick from Group BYOS Only", default=True, help_text="Limit BYOS charts to only those in same group instead of bracket.")
	byos_picks = models.IntegerField(verbose_name="BYOS Picks per match", validators=[MinValueValidator(1), MaxValueValidator(2)], default=1, help_text="Number of BYOS chart picks a player gets per-match.")

	class Meta:
		verbose_name = "Bracket Rules"
		verbose_name_plural = "Bracket Rules"
		app_label = 'corpoch'

	@property
	def wins_needed(self):
		return int(math.ceil(self.num_rounds / 2))

	def clean(self):
		"""Validates the explicitly selected CORP Cup numeric and chooser settings."""
		super().clean()
		if self.tb_ruleset == "corp_cup":
			from corpoch.match_rules import validate_corp_cup_profile
			try:
				validate_corp_cup_profile(
					self.num_players, self.num_rounds, self.num_bans,
					self.ban_ruleset, self.pick_ruleset, self.num_rounds + 4,
				)
				if self.seed_inversions:
					raise ValueError("CORP Cup does not permit seed inversions.")
			except ValueError as error:
				raise ValidationError(str(error)) from error

	@property
	def total_bans(self) -> int:
		return self.num_bans * self.num_players

	@property
	def boss_present(self):
		if len(self.bracket.setlist.filter(boss=True)) > 0:
			return True
		else:
			return False

	@property
	def byos_enabled(self):
		return self.bracket.tournament.config.byos

	#Maybe additionally add a match function pointing here for dynamic tb bans?
	@property 
	def bannable_tb(self) -> bool:
		if self.tb_ruleset == TB_RULESETS[2][0] or self.tb_ruleset == TB_RULESETS[4][0]: #Banpick or Bansave
			return True
		else:
			return False

	@property
	def pickable_tb(self) -> bool:
		"""
		Is the tie-breaker chart pickable
		"""
		if self.tb_ruleset == "corp_cup":
			return True
		if self.tb_ruleset == TB_RULESETS[0][0] or self.tb_ruleset == TB_RULESETS[1][0] or self.tb_ruleset[4][0]: # Preserve legacy rules until separately verified.
			return False
		else:
			return True

	def __str__(self):
		return f"{self.bracket}"

class Group(models.Model):
	"""
	Represents a Group in a Bracket for a Tournament. 
	"""
	id = models.AutoField(primary_key=True, db_index=True, help_text="The ID of the Group")
	name = models.CharField(verbose_name="Group Name", max_length=8, default="A", help_text="Group Name, full name constructed from tournament.short_name + bracket.name + group.name")
	role = models.ForeignKey("dbot.Roles", verbose_name="Group Role", on_delete=models.SET_NULL, null=True, blank=True, db_index=True, help_text="Discord role from Tournament Guild to be assigned to group members",)
	bracket = models.ForeignKey(Bracket, related_name="groups", verbose_name="Bracket", on_delete=models.CASCADE, help_text="Bracket this Group belongs to")

	class Meta:
		verbose_name = "Group"
		verbose_name_plural = "Groups"
		app_label = 'corpoch'

	@property
	def tournament(self) -> Tournament:
		return self.bracket.tournament

	@property
	def active_players(self) -> list:
		return self.players.objects.filter(is_active=True)

	def __str__(self):
		return f"{self.tournament.short_name} - {self.bracket.name} - {self.name}"

class TournamentPlayer(models.Model):
	"""
	Represents a Player (Tied to a DiscordUser) in a Tournament. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID of the Player")
	user = models.ForeignKey("corpoch.DiscordUser", related_name="tournaments", verbose_name="User", on_delete=models.SET_NULL, db_index=True, blank=True, null=True, help_text="Discord User for Player")
	name = models.CharField(verbose_name="Guild Discord Name", max_length=128, null=True, blank=True, help_text="Display name of user in Tournament Guild")
	tournament = models.ForeignKey(Tournament, related_name="players", verbose_name="Tournament", on_delete=models.CASCADE, help_text="Tournament player signed up for.")
	is_active = models.BooleanField(verbose_name="Player Active", default=False, help_text="Is player active. False means DNF/Did not enter.")
	config = SchemaField(PlayerConfig, verbose_name="Player Configuration", blank=True, help_text="JSON Configuration feild for player")

	class Meta:
		verbose_name = "Player"
		verbose_name_plural = "Players"
		app_label = 'corpoch'

	def __str__(self):
		return self.ch_name

	#This needs to be checked - probably not used/not right
	@property
	def brackets(self):
		return self.tournament.brackets.objects.select_related('player').filter(players__id=self.id)

	@property
	def ch_aliases(self) -> list[str]:
		"""Returns a list of all Clone Hero names associated with this player."""
		if not self.config:
			return []
		try:
			return [item.ch_name for item in self.config.names_list]
		except pydantic.ValidationError:
			return []

	@property
	def ch_name(self) -> str:
		"""
		Retrieves the primary Clone Hero name from the Pydantic config.
		"""
		if not self.config:
			return "</Null>"

		try:
			config_obj = PlayerConfig(**self.config) if isinstance(self.config, dict) else self.config
			names_list = config_obj.names_list

			if not names_list:
				return "</Null>"

			for item in names_list:
				if item.is_primary:
					return item.ch_name

			return names_list[0].ch_name

		except pydantic.ValidationError:
			return "</Null>"

	@property
	def mention(self) -> str:
		"""Returns a discord formatted mention string to @ a user"""
		return f"<@{self.user.id}>"

	@ch_name.setter
	def ch_name(self, new_name: str):
		"""
		Sets a new primary Clone Hero name. Appends it to the list if it doesn't exist,
		and ensures all other names are no longer marked as primary.
		"""
		if not new_name:
			new_name = "</Null>"

		clean_name = new_name

		if not self.config:
			config_obj = PlayerConfig(names_list=[])
		else:
			try:
				config_obj = PlayerConfig(**self.config) if isinstance(self.config, dict) else self.config
			except pydantic.ValidationError:
				config_obj = PlayerConfig(names_list=[])

		names_list = config_obj.names_list
		name_already_exists = False

		for item in names_list:
			if item.ch_name == clean_name:
				item.is_primary = True
				name_already_exists = True
			else:
				item.is_primary = False

		if not name_already_exists:
			names_list.append(CH_Name(ch_name=clean_name, is_primary=True))

		config_obj.names_list = names_list
		self.config = config_obj

	def check_ch_name(self, name_to_find: str) -> bool:
		if not self.config or not self.config.names_list:
			return False

		return any(item.ch_name == name_to_find for item in self.config.names_list)

class GroupSeed(models.Model):
	"""
	Represents a Seeding for a player in a Tournament Group. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID for this seed")
	seed = models.PositiveIntegerField(blank=False, null=False, help_text="Player seed within this group")
	group = models.ForeignKey(Group, related_name="seeding", verbose_name="Group Seeding", null=True, on_delete=models.CASCADE, help_text="Group this seed is for")
	player = models.ForeignKey(TournamentPlayer, related_name="group_seeding", verbose_name="Group Seed", null=True, on_delete=models.SET_NULL, help_text='Player with this seeding in the group')
	eliminated = models.BooleanField(verbose_name="Eliminated", default=False, help_text="Has player been eliminted from bracket (playoffs).")

	class Meta:
		verbose_name = "Seed Placement"
		verbose_name_plural = "Seed Placements"
		ordering = ['seed']
		app_label = 'corpoch'

	def __str__(self):
		# Fallback text if there is no player assigned to this seed yet
		player_name = self.player.ch_name if self.player else "Unassigned"
		return f"{player_name} ({self.seed})"

	@property
	def full_name(self):
		return f"{self.group.tournament.short_name} - {self.group.bracket.name} - Group {self.group.name} - Seed {self.seed}"

	@property
	def mention(self) -> str:
		"""Returns a discord formatted mention string to @ a user"""
		return f"<@{self.player.user.id}>"

	@property
	def player_ch_name(self):
		return self.player.ch_name if self.player else "</Null>"

	@property
	def seed_num(self):
		"""Returns the seed placement"""
		return str(self.seed)

	@property
	def user(self):
		"""Returns the associated DiscordUser"""
		return self.player.user if self.player else None

	def check_ch_name(self, testname):
		if not self.player:
			return False
		return self.player.check_ch_name(testname)

class Qualifier(models.Model):
	"""
	Represents a Seeding for a player in a Tournament Group. 
	"""
	id = models.AutoField(primary_key=True, help_text="Internal ID of this Qualifier")
	tournament = models.ForeignKey(Tournament, related_name='qualifier', verbose_name="Tournament", on_delete=models.CASCADE, help_text="Tournament this qualifer is for.")
	bracket = models.ForeignKey(Bracket, related_name='qualifier', verbose_name="Bracket", blank=True, null=True, on_delete=models.CASCADE, help_text="Specific bracket in a tournament this qualifier is for.")
	charts = models.ManyToManyField("corpoch.Chart", related_name="qualifier", verbose_name="Qualifier Chart(s)", help_text="Chart(s) that this qualifier uses.")
	limit_submissions = models.BooleanField(verbose_name="Limit Submissions to # Required", default=False, help_text="Limit players to single qualifier submission.")
	required_submissions = models.PositiveIntegerField(verbose_name="Required Submissions", default=1, help_text="If limit_submissions=False, the number of submissions required for a player to qualify.")
	form_link = models.URLField(verbose_name="Google Form Link", null=True, blank=True, help_text="Google Form link to show players while submitting their qualifier.")
	end_time = models.DateTimeField(verbose_name="End Time", default=timezone.now, help_text="UTC Time this qualifier ends. +2 hours from this time, the results are allowed in the API.")
	rules = models.TextField(verbose_name="Rules", max_length=1024, default="Placeholder rules", help_text="Rules shown to players for this qualifier.")
	channel = models.ForeignKey("dbot.Channels", verbose_name="Submission Discord Channel", on_delete=models.SET_NULL, db_index=True, blank=True, null=True, help_text="Discord Channel users will submit scores in.")
	gsheet = models.URLField(verbose_name="Submissions Google Sheet", null=True, blank=True, help_text="Google Sheet to submit qualifier scores to.")
	output = models.BooleanField(verbose_name="Msg On Submission", default=True, help_text="Publically post in discord when user submits a qualifier.")

	class Meta:
		verbose_name = "Qualifier"
		verbose_name_plural = "Qualifiers"
		app_label = 'corpoch'

	def __str__(self):
		if self.bracket:
			return f"{self.tournament.short_name} - {self.bracket.name}"
		else:
			return f"{self.tournament.short_name}"

class QualifierSubmission(models.Model):
	"""
	Represents a Submission for a qualifier for a Tournament or Bracket. 
	"""
	id = models.CharField(primary_key=True, verbose_name="Qualifier ID", max_length=40, default=uuid.uuid1, help_text="UUID of the submission.")
	player = models.ForeignKey(TournamentPlayer, related_name="qualifiers", verbose_name="Submittor", on_delete=models.CASCADE, help_text="The Player that submitted a qualifier.")
	submit_time = models.DateTimeField(verbose_name="Submission Time", auto_now_add=True, help_text="Submission timestamp.")
	screenshot = models.ImageField(upload_to=quali_upload_dir, verbose_name="Screenshot", null=True, blank=True, help_text="The screenshot for a submission.")
	qualifier = models.ForeignKey(Qualifier, related_name='submissions', verbose_name="Tournament Qualifier", on_delete=models.CASCADE, help_text="Qualifier that a submission was sent for.")
	steg = SchemaField(StegScreenshot, verbose_name="Steg Data", null=True, blank=True, help_text="Clone Hero screenshot steg data.")
	submitted = models.BooleanField(verbose_name="Uploaded to GSheet", default=False, help_text="Is submission sent to associated GSheet.")

	class Meta:
		verbose_name = "Qualifier Submission"
		verbose_name_plural = "Qualifier Submissions"
		app_label = 'corpoch'

	def __str__(self):
		return f"{self.player.ch_name} - {self.qualifier.tournament.name} {self.qualifier.bracket.name if self.qualifier.bracket else ''} Qualifier"

	@property
	def score(self):
		if self.steg.players:
			return self.steg.players[0].score
		else:
			return '-'

	@property
	def display_profile_name(self) -> str:
		"""
		Always returns the current primary ch_name of the player, 
		acting as a dynamic reference.
		"""
		return self.player.ch_name

	def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
		if self.screenshot and not self.steg:
			from corpoch.providers import CHStegTool
			tool = CHStegTool()
			self.steg = tool.getStegInfoSync(self.screenshot)
			for i, ply in enumerate(self.steg.players):
				if not self.player.check_ch_name(ply.profile_name):
					self.steg.players.pop(i)
		super().save()

#Potential class for a "Series" of tournaments - just needs to be a list of tournaments for ogranization
#class TournamentSeries(models.Model):
#	id = models.PositiveIntegerField(blank=False, null=False)
