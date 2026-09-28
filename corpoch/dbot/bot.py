import discord, django, django.db, logging, random, sys, time

from discord.ext import commands, tasks
from django.apps import apps
from django.utils import timezone
from kombu import Connection, Consumer, Queue
from kombu.utils.limits import TokenBucket
from redis import asyncio as aioredis
from socket import timeout

from corpoch.dbot import bot_tasks, tasks as sync_tasks
from corpoch.dbot import settings
from corpoch import __version__ as version

logger = logging.getLogger(__name__)

class CorpoDbot(commands.Bot):
	def __init__(self):
		self.log = logging.getLogger(__name__)
		random.seed()
		sys.stdout.reconfigure(line_buffering = True)
		sys.stderr.reconfigure(line_buffering = True)
		print("--- Pre-startup ---")
		django.setup()
		intents = discord.Intents.default()
		intents.members = True
		self.client = super().__init__(intents=intents, chunk_guilds_at_startup=False)
		self.redis = self.loop.run_until_complete(aioredis.from_url(settings.CELERY_BROKER_URL, encoding="utf-8", decode_responses=True))
		self.message_connection = Connection(settings.CELERY_BROKER_URL)
		self.message_consumer = Consumer(self.message_connection, [Queue("corpoch.dbot")], callbacks=[self.on_queue_message])
		self.tasks = []
		self.matches = {}
		self.done_startup = False
		print(f"redis pool started {settings.CELERY_BROKER_URL}")

		for cog in settings.COGS_ENABLED:
			self.load_extension(f'corpoch.dbot.cogs.{cog}')
			print(f'Cog loaded: {cog}')

		self.owners = []
		self.proofCalls = None

	def run(self):
		print(f"--- Starting up at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} ---")
		print('Logging into discord')
		try:
			super().run(settings.BOT_TOKEN, reconnect=True)
		except discord.PrivilegedIntentsRequired as e:
			print("Unable to login to discord - missing discord.intents.members privledge - Sleeping then exiting")
			print(f"    Please visit https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents")
			time.sleep(5)
			sys.exit(1)
		print(f"--- Shutting down at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} ---")
		sys.exit(0)

	def on_queue_message(self, body, message):
		self.tasks.append((getattr(bot_tasks, message.headers["task"].replace("corpoch.dbot.tasks.", ''), False), body[0], body[1]))
		message.ack()

	async def on_interaction(self, interaction):
		try:
			django.db.close_old_connections()
			await self.process_application_commands(interaction)
		except Exception as e:
			logger.error(f"Interaction Failed {e}", stack_info=True)
		django.db.close_old_connections()

	async def retrieve_owners(self):
		print("Retrieving bot owners.")
		app = await self.application_info()
		to_check = []
		if app.team:
			for mem in app.team.members:
				owner = await self.fetch_user(mem.id)
				if not owner:
					print(f"  Can't get user object for team member {str(mem.name)} id {mem.id}")
				else:
					print(f"  Loaded owner: {str(owner.name)} id {owner.id}")
					self.owners.append(owner)
					to_check.append(owner.id)
		else:
			self.owners = [app.owner]
			to_check = [app.owner.id]
			print(f"  Loaded owner: {str(app.owner.name)} id {app.owner.id}")

		from corpoch.models import DiscordUser
		for owner in to_check:
			int_user, created = DiscordUser.objects.get_or_create(id=owner)
			if created:
				sync_tasks.update_user(owner) #Call non-async to run after done startup

		async for user in DiscordUser.objects.all().filter(pk__in=to_check, is_superuser=False):
			print(f"  Marking owner {user} as superuser/staff")
			user.is_superuser = True
			user.is_staff = True
			await user.asave()

	@tasks.loop(seconds=1.0)
	async def poll_queue(self):
		django.db.close_old_connections()
		message_avail = True
		while message_avail:
			try:
				with self.message_consumer:
					self.message_connection.drain_events(timeout=0.01)
			except timeout:
				message_avail = False
		if not bot_tasks.run_tasks.is_running():
			bot_tasks.run_tasks.start(self)
		django.db.close_old_connections()

	@tasks.loop(seconds=300)
	async def switch_status(self):
		django.db.close_old_connections()
		from corpoch.models import Match, QualifierSubmission, MatchRound
		matches = Match.objects.all().filter(complete=False)
		if matches.count() > 0:
			rand = random.randint(0, matches.count())
			if matches.count() == 1 and rand == 1:
				rand = 0
			try:
				activity = discord.Activity(name=f"{matches[rand].tournament.short_name} - {matches[rand].short_name} {matches[rand].score}", type=discord.ActivityType(3))
			except:
				activity = discord.Game(f"TTFAF")
		else:
			rand = random.randint(0, 2)
			if rand == 0:
				activity = discord.Game(f"{Match.objects.all().count()} Tracked Matches")
			elif rand == 1:
				activity = discord.Game(f"{MatchRound.objects.all().count()} Tracked Match Rounds")
			elif rand == 2:
				activity = discord.Game(f"{QualifierSubmission.objects.all().count()} Tracked Qualifier Submissions")
		await self._bot.change_presence(status=discord.Status.online, activity=activity)
		django.db.close_old_connections()

	async def close(self):
		self.switch_status.stop()
		self.poll_queue.stop()
		bot_tasks.run_tasks.stop()
		await super().close()

	async def on_ready(self):
		if not self.done_startup:
			print(f"Logged in as {self.user.name}#{self.user.discriminator} id {self.user.id} v{version}")
			await self.retrieve_owners()
			print("Loading on-going matches")
			from corpoch.dbot.cogs.tourneycmds import DiscordMatch
			from corpoch.models import Match
			async for match in Match.objects.exclude(channel=None).filter(finished=False):
				if not match.message:
					continue
				print(f"Got ongoing match {match.id}")
				try:
					view = DiscordMatch(self._bot, uuid=match.id)
					await view.init()
					self.matches[match.id] = view
				except Exception:
					self.matches.pop(match.id, None)
					self.log.exception("Unable to restore match %s; continuing startup.", match.id)

			if not bot_tasks.run_tasks.is_running():
				print("Starting tasks")
				self.message_consumer.consume(no_ack=False)
				self.poll_queue.start()
				self.switch_status.start()

			print('------Done with Startup------')
			self.done_startup = True

if __name__ == "__main__":
	bot = CorpoDbot()
	bot.run()
