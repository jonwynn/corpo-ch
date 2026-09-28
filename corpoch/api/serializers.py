from django_pydantic_field.rest_framework import SchemaField
from rest_framework import serializers

from corpoch import models as corpomodels
from corpoch.dbot import models as dbotmodels
from corpoch.types import StegScreenshot

class DiscordUserSerializer(serializers.HyperlinkedModelSerializer):
	class Meta:
		model = corpomodels.DiscordUser
		fields = ['id', 'global_name', 'avatar']

class DiscordChannelSerializer(serializers.ModelSerializer):
	class Meta:
		model = dbotmodels.Channels
		fields = '__all__'

class DiscordRoleSerializer(serializers.ModelSerializer):
	class Meta:
		model = dbotmodels.Roles
		fields = '__all__'

class DiscordGuildSerializer(serializers.ModelSerializer):
	class Meta:
		model = dbotmodels.Guilds
		fields = ("id", "name", "icon")

class TournamentSerializer(serializers.ModelSerializer):
	class Meta:
		model = corpomodels.Tournament
		fields = '__all__'

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		if self.root is not None:
			ret.pop('name')
			ret.pop('guild')
			ret.pop('active')
		return ret

class TournamentPlayerSerializer(serializers.ModelSerializer):
	user = DiscordUserSerializer()

	class Meta:
		model = corpomodels.TournamentPlayer
		fields = ['id', 'user', 'name', 'ch_name', 'tournament', 'is_active', 'config']
		depth = 2

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		if self.root is not None:
			ret.pop('name')
			ret.pop('tournament')
			ret.pop('config')
			ret.pop('is_active')
		return ret

class BracketRulesSerializer(serializers.ModelSerializer):
	class Meta:
		model = corpomodels.BracketRules
		fields = '__all__'

class BracketSerializer(serializers.ModelSerializer):
	tournament = TournamentSerializer()
	ruleset = BracketRulesSerializer()

	class Meta:
		model = corpomodels.Bracket
		fields = ['id', 'name', 'tournament', 'is_active', 'revealed', 'ruleset']

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		ret['full_name'] = str(instance)
		if self.root is not None:
			ret.pop('ruleset')
			ret.pop('is_active')
			ret.pop('revealed')
		return ret

class CHIconSerializer(serializers.ModelSerializer):
	class Meta:
		model = corpomodels.CHIcon
		fields = '__all__'

class ChartSerializer(serializers.ModelSerializer):
	icon = CHIconSerializer()
	brackets = BracketSerializer(many=True)

	class Meta:
		model = corpomodels.Chart
		fields = ['id', 'name', 'tournament_name', 'artist', 'album', 'charter', 'boss', 'tiebreaker', 'difficulty', 'instrument', 'modifiers', 'speed', 'category', 'brackets', 'md5', 'blake3', 'url', 'icon', 'sngfile']
		depth = 2

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		#ret['tournament_name'] = instance.tournament_name
		if self.root is not None:
			ret.pop('difficulty')
			ret.pop('charter')
			ret.pop('instrument')
			ret.pop('album')
			ret.pop('artist')
			ret.pop('brackets')
			ret.pop('name')
			ret.pop('speed')
			ret.pop('modifiers')
			ret.pop('blake3')
			ret.pop('url')
			ret.pop('icon')
			ret.pop('sngfile')
		return ret

class GroupSeedSerializer(serializers.ModelSerializer):
	player = TournamentPlayerSerializer()

	class Meta:
		model = corpomodels.GroupSeed
		fields = ['id', 'seed', 'group', 'player', 'eliminated']
		depth = 2

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		if self.root is not None:
			ret.pop('eliminated')
			ret.pop('group')
		return ret

class GroupSerializer(serializers.ModelSerializer):
	seeding = GroupSeedSerializer(many=True)
	bracket = BracketSerializer()

	class Meta:
		model = corpomodels.Group
		fields = ['id', 'name', 'bracket', 'seeding']
		depth = 1

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		ret['full_name'] = str(instance)
		if self.root is not None:
			ret.pop('seeding')
		return ret

class MatchBanSerializer(serializers.ModelSerializer):
	chart = ChartSerializer()
	player = TournamentPlayerSerializer()

	class Meta:
		model = corpomodels.MatchBan
		fields = ["num", 'chart', 'player']

class MatchRoundSerializer(serializers.ModelSerializer):
	steg = SchemaField(StegScreenshot)
	winner = TournamentPlayerSerializer()
	picked = TournamentPlayerSerializer()
	loser = TournamentPlayerSerializer()
	chart = ChartSerializer()

	class Meta:
		model = corpomodels.MatchRound
		fields = ["id", "num", "steg", "screenshot", "created", "chart", "match", "picked", "winner", "loser"]

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		if self.root is not None:
			ret.pop('match')
		return ret

class MatchSerializerLight(serializers.ModelSerializer):
	players = GroupSeedSerializer(many=True)
	#group = GroupSerializer()
	winner = TournamentPlayerSerializer()
	loser = TournamentPlayerSerializer()

	class Meta:
		model = corpomodels.Match
		fields = ['id', 'players', 'winner', 'loser', 'defer']

class MatchSerializer(serializers.ModelSerializer):
	players = GroupSeedSerializer(many=True)
	match_rounds = MatchRoundSerializer(many=True)
	match_bans = MatchBanSerializer(many=True)
	group = GroupSerializer()
	winner = TournamentPlayerSerializer()
	loser = TournamentPlayerSerializer()
	referee = DiscordUserSerializer()

	class Meta:
		model = corpomodels.Match
		fields = [
			"id", "players", "match_rounds", "match_bans", "group", "winner", "loser", "referee",
			"defer", "started_on", "ended_on", "complete", "finished", "submitted",
			"channel", "message", "exhibition", "rev_seeds",
		]

	def to_representation(self, instance):
		ret = super().to_representation(instance)
		ret.pop('channel')
		ret.pop('message')
		ret.pop('exhibition')
		return ret

class QualifierSerializer(serializers.ModelSerializer):
	class Meta:
		model = corpomodels.Qualifier
		fields = '__all__'

class QualifierSubmissionSerializer(serializers.ModelSerializer):
	steg = SchemaField(StegScreenshot)
	class Meta:
		model = corpomodels.QualifierSubmission
		fields = '__all__'
