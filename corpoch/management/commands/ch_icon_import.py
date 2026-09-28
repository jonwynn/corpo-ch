import requests, tarfile, io, re, time, os, shutil
from django.conf import settings
from django.core.management.base import BaseCommand
from corpoch.models import CHIcon, Chart
from corpoch.dbot.models import CHEmoji
import corpoch.dbot.tasks 

class Command(BaseCommand):
	help = 'Load CH Icons'

	def add_arguments(self, parser):
		parser.add_argument('-f', '--flush', action="store_true", help='Discord Guild ID of Tournament to make Users from TournamentPlayers for')
		parser.add_argument('-c', '--crowns', action="store_true", help='Import only PF/C crown assets')

	def handle_crowns(self):
		print("Adding crown assets")
		assets = [f"{settings.PROJECT_HOME}/corpoch/static/pfcd.png", f"{settings.PROJECT_HOME}/corpoch/static/pfcg.png", f"{settings.PROJECT_HOME}/corpoch/static/fc.png"]
		for asset in assets:
			name = asset.split('/')[-1].split('.')[0]
			print(f"Adding app emoji for {name} {asset}")
			corpoch.dbot.tasks.add_bot_emoji(name, asset)

	def handle(self, *args, **options):
		flush = options['flush']
		crowns = options['crowns']
		if flush:
			print(f"Flushing DB of CHEmotes/Icons and re-importing")
			for chart in Chart.objects.all():
				chart.ch_icon = None
				chart.save()
			for emote in CHEmoji.objects.all():
				emote.delete()
			for icon in CHIcon.objects.all():
				icon.delete()
			shutil.rmtree(f"{settings.MEDIA_ROOT}chicons")

		self.handle_crowns()
		if crowns:
			return

		ch_default_icon_path = f"./corpoch/static/ch_default_icon.png"
		if not os.path.isfile(ch_default_icon_path):
			print(f"Error: File at {ch_default_icon_path} was not found.")
		else:
			try:
				icon = CHIcon.objects.get(name="ch_default_icon")
				print(f"Icon exists in DB. Ensuring dbot appemoji exists")
			except CHIcon.DoesNotExist:
				icon = None

			if not icon:
				print(f"Creating corpoch icon ch_default_icon")
				icon = CHIcon(name="ch_default_icon")
				icon.img.save("ch_default_icon.png", open(ch_default_icon_path, 'rb'))
				icon.save()

			try:
				emoji = CHEmoji.objects.get(icon=icon)
			except CHEmoji.DoesNotExist:
				emoji = None

			if not emoji:
				print(f"Queueing bot task to create emoji ch_default_icon")
				corpoch.dbot.tasks.add_bot_emoji("ch_default_icon")

		response = requests.get('https://gitlab.com/api/v4/projects/25065576/repository/archive.tar.gz?path=public/icons')
		tar = tarfile.open(fileobj=io.BytesIO(response.content), mode='r:gz')
		members=tar.getmembers()
		for member in members:
			if member.name.lower().endswith('.png'):
				filename = re.sub(".*\\/(.*)png", "\\1png", member.name, flags=re.IGNORECASE)
				extracted_file = tar.extractfile(member)
				name = filename.replace(".png", "")
				print(f"Setting up icon {filename}")
				try:
					icon = CHIcon.objects.get(name=name)
					print(f"Icon exists in DB. ensuring dbot icon exists")
				except CHIcon.DoesNotExist:
					icon = None

				if not icon:
					print(f"Creating corpoch icon {name}")
					icon = CHIcon(name=name)
					icon.img.save(filename, extracted_file)
					icon.save()

				try:
					emoji = CHEmoji.objects.get(icon=icon)
				except CHEmoji.DoesNotExist:
					emoji = None

				if not emoji:
					print(f"Queueing bot task to create emoji {name} ")
					corpoch.dbot.tasks.add_bot_emoji(name)
