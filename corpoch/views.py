from requests import Session

from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import redirect, render

from corpoch import settings
from corpoch.dbot.tasks import update_user

def null(request: HttpRequest):
  return redirect("home")

def home(request: HttpRequest):
	if request.method == "POST":
		try:
			del request.session["access_token"]
		except KeyError:
			pass
	return render(request, "home.html", context={"auth_url" : settings.AUTH_URL_DISCORD})

def auth(request: HttpRequest):
	from corpoch.models import DiscordUser, DiscordToken
	code = request.GET.get("code")
	if not code:
		if request.GET.get("error"):
			return redirect_discord_login(request)
		return redirect(settings.AUTH_URL_DISCORD)
	oauth = DiscordToken()
	try:
		oauth.login(code=code)
		user = OAuthUser(oauth.identity())
	except DiscordToken.AuthError:
		return redirect_discord_login(request)
	try:
		token = DiscordToken.objects.get(user__id=user.id)
		token.access_token = oauth.access_token
		token.refresh_token = oauth.refresh_token
		token.expires = oauth.expires
		token.save()
		oauth = token
	except DiscordToken.DoesNotExist:
		oauth.user, created = DiscordUser.objects.get_or_create(pk=user.id)
		if created:
			update_user(user.id)
		oauth.save()
	request.session["access_token"] = oauth.access_token
	request.session['user_id'] = user.id
	return redirect("user")


def redirect_discord_login(request: HttpRequest):
    """
    Clears stale OAuth session values before restarting Discord login

    :param HttpRequest request: Current browser request
    :return: Redirect to the configured Discord authorization URL"""
    request.session.pop("access_token", None)
    request.session.pop("user_id", None)
    return redirect(settings.AUTH_URL_DISCORD)


def user(request: HttpRequest):
	from corpoch.models import DiscordToken, DiscordUser
	if not request.session.get("access_token") or not request.session.get("user_id"):
		return redirect_discord_login(request)
	try:
		oauth = DiscordToken.objects.get(user__id=request.session.get('user_id'))
		oauth.login()
		context = { "user" : OAuthUser(oauth.identity()), "guilds" : OAuthGuilds(oauth.guilds()) }
		oauth.save()
	except (DiscordToken.DoesNotExist, DiscordToken.AuthError):
		return redirect_discord_login(request)

	discord_user = authenticate(request, user=context['user'])
	if not isinstance(discord_user, DiscordUser):
		discord_user = discord_user[0]
	login(request, discord_user, backend="corpoch.auth.DiscordBackend")
	context['internal_user'] = discord_user

	return render(request, "user.html", context=context)

def livematches(request: HttpRequest):
	from corpoch.models import Match
	matches = list(filter(lambda match: match.ongoing, Match.objects.all()))
	current_match_ids = ",".join([str(m.id) for m in matches])

	return render(request, "livematches.html", {
		'matches': matches,
		'current_match_ids': current_match_ids
	})

def update_livematches(request: HttpRequest):
	selected_ids = request.GET.getlist('selected_matches')
	client_match_ids = request.GET.get('current_match_ids', '')
	from corpoch.models import Match
	all_ongoing = list(filter(lambda match: match.ongoing, Match.objects.all()))
	current_match_ids = ",".join([str(m.id) for m in all_ongoing])

	matches_changed = (client_match_ids != current_match_ids)

	display_matches = all_ongoing
	if selected_ids and any(selected_ids):
		display_matches = [m for m in all_ongoing if str(m.id) in selected_ids]

	return render(request, 'partials/livematchesdata.html', {
		'matches': display_matches,
		'all_matches': all_ongoing,
		'selected_ids': selected_ids,
		'matches_changed': matches_changed,
		'current_match_ids': current_match_ids
	})

def privterms(request: HttpRequest):
	return render(request, 'privterms.html')

class OAuthUser:
	__default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"

	def __init__(self, user : dict) -> None:
		self.__user = user
		for k , v in self.__user.items():
			try:
				setattr(self, k, v)
			except AttributeError:
				continue

	@property
	def id(self):
		return self.__user['id']

	@property
	def avatar(self):
		return f"https://cdn.discordapp.com/avatars/{self.__user['id']}/{self.__user['avatar']}" if self.__user['avatar'] else self.__default_avatar

class Role:
	def __init__(self, role: dict) -> None:
		self.__role = role
		for k, v in self.__role.items():
			try:
				setattr(self, k , v)
			except AttributeError:
				continue

	def __repr__(self) -> str:
		return repr(self.__role)

	def __str__(self):
		return self.name

class OAuthGuilds:
	def __init__(self, guilds : list) -> None:
		self.__guilds = []
		from corpoch.models import Tournament
		self.__tournaments = []
		for guild in guilds:
			for tourney in Tournament.objects.all().filter(guild__id=guild['id']):
				self.__guilds.append(guild)
				self.__tournaments.append(tourney)
				
	def __iter__(self):
		return iter([Guild(guild) for guild in self.__guilds])

	def __repr__(self) -> str:
			return repr(self.__guilds)

class Guild:
	__default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"

	def __init__(self, guild : dict) -> None:
		self.__guild = guild
		for k, v in self.__guild.items():
			try:
				setattr(self, k , v)
			except AttributeError:
				continue

	def __repr__(self) -> str:
		return repr(self.__guild)

	@property
	def user_is_administrator(self):
		#Move this to be checking role grants admin from DB
		return self.__guild["permissions"] == '1099511627775'
	
	@property
	def roles(self) -> list:
		return list(Role(role) for role in self.__guild['roles'])

	@property
	def id(self):
		return self.__guild['id']

	@property
	def icon(self):
		return f"https://cdn.discordapp.com/icons/{self.__guild['id']}/{self.__guild['icon']}.png" if self.__guild['icon'] else self.__default_avatar
