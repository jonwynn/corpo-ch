from django.core.management.base import BaseCommand
from corpoch.models import DiscordUser

class Command(BaseCommand):
	help = 'Load CH Icons'
	def add_arguments(self, parser):
		parser.add_argument('-d', '--discord-id', type=int, help='Discord ID of user to make superadmin')

	def handle(self, *args, **options):
		did = options['discord_id']
		user = DiscordUser.objects.get(id=did)
		user.is_staff = True
		user.is_superuser = True
		user.save()
