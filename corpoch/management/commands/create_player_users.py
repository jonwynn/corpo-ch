from django.core.management.base import BaseCommand
from corpoch.models import DiscordUser, TournamentPlayer, Tournament
import corpoch.dbot.tasks 

class Command(BaseCommand):
	help = 'Create DiscordUsers from TournamentPlayers'

	def add_arguments(self, parser):
		parser.add_argument('-d', '--discord-id', type=int, help='Discord Guild ID of Tournament to make Users from TournamentPlayers for')

	def handle(self, *args, **options):
		gid = options['discord_id']
		if gid:
			tourney = Tournament.objects.get(active=True, guild__id=gid)
			objs = TournamentPlayer.objects.all().filter(tournament=tourney, is_active=True)
		else:
			objs = TournamentPlayer.objects.all()

		for ply in objs:
			try:
				user = DiscordUser.objects.get(id=ply.user)
			except DiscordUser.DoesNotExist:
				user = DiscordUser(id=ply.user)
				print(f"Creating User {ply.user} - {ply.name} + sending task to update info from discord")
				user.save()
				corpoch.dbot.tasks.update_user(user.id)
