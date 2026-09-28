from celery import Celery, shared_task, Task
from celery.schedules import crontab
from django.db import close_old_connections, connection
from django.utils import timezone

from corpoch.dbot import tasks
from corpoch.dbot.models import Guilds
from corpoch.models import Tournament, TournamentPlayer, Qualifier, QualifierSubmission, Match, DiscordUser, Chart, CHIcon, DiscordToken
from corpoch.providers import GSheets, EncoreClient
from corpoch.utils.snghandler import SNGHandler

app = Celery()

@app.task
def upload_qualifiers_gsheet():
	close_old_connections()
	qualis = QualifierSubmission.objects.all().filter(submitted=False)
	sheet = GSheets()
	sheet.login()
	print(f"GSHEETS: Running gsheets upload for unsubmitted qualifiers")
	for quali in qualis:
		print(f"GSHEETS: Uploading ({quali}) to sheet")
		sheet.set_submission(quali)
		sheet.submit_qualifier()
	close_old_connections()

@app.task
def upload_completed_match_gsheet():
	close_old_connections()
	matches = Match.objects.all().filter(finished=True).filter(submitted=False)
	sheet = GSheets()
	sheet.login()
	print(f"GSHEETS: Running gsheets upload for completed matches")
	for match in matches:
		if len(match.players.all()) > 0 and match.tournament.config.gsheet:
			from corpoch.match_actions import get_match_state_token
			from corpoch.match_publication import publish_export_status
			expected_state = get_match_state_token(match)
			print(f"GSHEETS: Uploading completed match {match.id} to tourney config sheet")
			sheet.set_submission(match)
			sheet.submit_completed()
			publish_export_status(match.pk, expected_state=expected_state)
	close_old_connections()

@app.task
def send_qualifier_discord_dms(qualifier_id, *args, **kwargs):
	close_old_connections()
	qualifier = Qualifier.objects.get(id=qualifier_id)
	for ply in TournamentPlayer.objects.all().filter(tournament=qualifier.tournament):
		try:
			submissions = QualifierSubmission.objects.all().filter(player=ply)
		except QualifierSubmission.DoesNotExist:
			print("No qualifiers... continuing")
			continue
		if len(submissions) < qualifier.required_submissions and submissions.count() != 0:
			print(f"Sending Qualifier submission reminder to {ply}")
			tasks.send_qualifier_discord_dms(ply, str(qualifier), qualifier.required_submissions, qualifier.end_time, qualifier.tournament.guild, len(submissions))
	close_old_connections()

@app.task
def update_all_guilds():
	close_old_connections()
	for guild in Guilds.objects.all().filter(deleted=False):
		tasks.update_guild(guild.id)
	close_old_connections()

@app.task
def update_all_users():
	close_old_connections()
	for user in DiscordUser.objects.all():
		tasks.update_user(user.id)
	close_old_connections()

@app.task
def update_gsheet(submission_id, *args, **kwargs):
	close_old_connections()
	try:
		sub = Match.objects.get(id=submission_id)
	except Match.DoesNotExist:
		sub = None
	try:
		if not sub:
			sub = QualifierSubmission.objects.get(id=submission_id)
	except QualifierSubmission.DoesNotExist:
		sub = None
	try:
		if not sub:
			sub = Tournament.objects.get(id=submission_id)
	except Tournament.DoesNotExist:
		print(f"Did not find qualifier submission, match, our tournament ID for {submission_id}")
		return #Probably want to throw exception?

	sheet = GSheets()
	sheet.login()
	sheet.set_submission(sub)
	if isinstance(sub, Match):
		sheet.update_match()
	elif isinstance(sub, QualifierSubmission):
		sheet.update_qualifier()
	elif isinstance(sub, Tournament):
		sheet.submit_players()
	close_old_connections()

@app.task
def submit_final_sheet(qualifier_id, *args, **kwargs):
	close_old_connections()
	sheet = GSheets(fin=True)
	sheet.login()
	quali = Qualifier.objects.get(id=qualifier_id)
	print(f"Submitting final scores for qualifier {quali}")
	for ply in TournamentPlayer.objects.all().filter(tournament=quali.tournament):
		objs = QualifierSubmission.objects.all().filter(player=ply)
		subs = sorted(objs, key=lambda i: i.steg.players[0].score)
		if len(subs) > 0 and len(subs) >= quali.required_submissions:
			print(f"Submitting Final Score for {ply} - {subs[-1].steg.players[0].score}")
			sheet.set_submission(subs[-1])
			sheet.submit_qualifier()
		else:
			print(f"Player {ply} did not meet submission requirements!")

	close_old_connections()

@app.task
def encore_import(chart_id, *args, **kwargs):
	close_old_connections()
	chartdb = Chart.objects.get(id=chart_id)
	encore = EncoreClient()
	search = encore.search(chartdb.encore_search_query)
	i = 0
	if len(search.data) == 0:
		print(f"Chart {chartdb.name} encore lookup with query {chartdb.encore_search_query} failed with {search}")
		return
	if len(search.data) > 1:
		print(f"Chart {chartdb.name} returned multiple results")
		for j, cht in enumerate(search.data):
			if chartdb.blake3 == cht.md5:
				i = j
				break

	encoreChart = search.data[i]
	chartdb.sngfile.save(f"{encoreChart.name}.sng", encore.download_from_chart(encoreChart))
	chartdb.save()
	close_old_connections()

@app.task
def chart_songini_import(chart_id, *kargs, **kwargs):
	close_old_connections()
	chart = Chart.objects.get(id=chart_id)
	print(f"Updating chart {chart.id} from song.ini")
	song = SNGHandler(chart.sngfile.open(mode='rb').read())
	songini = song.songini_model
	chart.name = songini.name
	chart.artist = songini.artist
	chart.album = songini.album
	chart.genre = songini.genre
	chart.charter = songini.charter
	chart.md5 = song.md5
	try:
		chart.icon = CHIcon.objects.get(name=songini.icon)
	except CHIcon.DoesNotExist:
		chart.icon = CHIcon.objects.get(name="ch_default_icon")
	chart.save()
	close_old_connections()

@app.task
def update_oauth_tokens():
	close_old_connections()
	print("OAUTH TOKENS: Refreshing Discord OAuth tokens")
	tokens = DiscordToken.objects.all()
	for token in tokens:
		try:
			token.login()
			token.update_code()
			token.save()
		except DiscordToken.AuthError as e:
			print(f"OAUTH TOKENS: Deleting token for user {token.user.global_name} - {e}")
			token.delete()
