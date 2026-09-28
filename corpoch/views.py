from django.conf import settings
from django.contrib.auth import authenticate, login
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, transaction
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from corpoch.dbot.tasks import update_user
from corpoch.discord_oauth import (
    consume_discord_attempt,
    create_discord_authorization_url,
)

def null(request: HttpRequest):
  return redirect("home")

def home(request: HttpRequest):
	if request.method == "POST":
		try:
			del request.session["access_token"]
		except KeyError:
			pass
	return render(request, "home.html", context={"auth_url": reverse("discord_auth_start")})

@require_GET
def auth(request: HttpRequest):
    """
    Consumes a browser-bound callback before exchanging a Discord code

    :param HttpRequest request: Discord callback request
    :return: Login continuation or a local recovery page"""
    from corpoch.models import DiscordUser, DiscordToken

    if not any(key in request.GET for key in ("code", "error", "state")):
        return discord_auth_response(redirect("discord_auth_start"))
    try:
        returned_state = request.GET.get("state") if len(request.GET.getlist("state")) == 1 else None
        valid_state = consume_discord_attempt(request, returned_state)
    except DatabaseError:
        return discord_login_error(request, status=503)
    if not valid_state:
        return discord_login_error(request)
    code = request.GET.get("code")
    if "error" in request.GET or not code or len(request.GET.getlist("code")) != 1:
        return discord_login_error(request)
    oauth = DiscordToken()
    try:
        oauth.login(code=code)
        user = OAuthUser(oauth.identity())
    except DiscordToken.AuthError:
        return discord_login_error(request)
    try:
        with transaction.atomic():
            account, created = DiscordUser.objects.get_or_create(pk=user.id)
            token, unused_token_created = DiscordToken.objects.select_for_update().get_or_create(
                user=account,
                defaults={
                    "access_token": oauth.access_token,
                    "refresh_token": oauth.refresh_token,
                    "expires": oauth.expires,
                    "scopes": oauth.scopes,
                },
            )
            token.access_token = oauth.access_token
            token.refresh_token = oauth.refresh_token
            token.expires = oauth.expires
            token.scopes = oauth.scopes
            token.save(update_fields=["access_token", "refresh_token", "expires", "scopes"])
            oauth = token
    except DatabaseError:
        return discord_login_error(request, status=503)
    if created and getattr(settings, "DISCORD_PROFILE_SYNC_ENABLED", True):
        update_user(user.id)
    request.session["access_token"] = oauth.access_token
    request.session["user_id"] = user.id
    return discord_auth_response(redirect("user"))


@require_GET
def auth_start(request: HttpRequest):
    """
    Starts a fresh Discord authorization attempt from a local route

    :param HttpRequest request: Browser login request
    :return: Discord authorization redirect or a configuration recovery page"""
    try:
        destination = create_discord_authorization_url(request)
    except (ImproperlyConfigured, DatabaseError):
        return discord_login_error(request, status=503)
    return discord_auth_response(redirect(destination))


def discord_auth_response(response):
    """
    Prevents caching and forwarding callback codes through referrer headers

    :param HttpResponse response: Login response
    :return: Response with authentication privacy headers"""
    patch_cache_control(response, no_store=True, private=True, max_age=0)
    response["Referrer-Policy"] = "no-referrer"
    return response


def discord_login_error(request: HttpRequest, status=400):
    """
    Offers a deliberate retry without repeating a failed external redirect

    :param HttpRequest request: Failed login request
    :param int status: Failure status for the local recovery page
    :return: Local recovery page without external error details"""
    request.session.pop("access_token", None)
    request.session.pop("user_id", None)
    request.session.pop("discord_oauth_state", None)
    if status == 503:
        message = "Discord login is temporarily unavailable. Try again later or contact tournament staff."
    elif status == 403:
        message = "This Discord account cannot sign in. Contact tournament staff."
    else:
        message = "Discord login could not be completed. Select Discord Login to try again."
    return discord_auth_response(render(
        request,
        "home.html",
        {"auth_url": reverse("discord_auth_start"), "discord_login_error": message},
        status=status,
    ))


def redirect_discord_login(request: HttpRequest):
    """
    Clears stale OAuth session values before returning to local login recovery

    :param HttpRequest request: Current browser request
    :return: Local recovery page with a fresh login link"""
    return discord_login_error(request)


@require_GET
def user(request: HttpRequest):
    """
    Authenticates a stored Discord identity before creating the website session

    :param HttpRequest request: Browser login continuation
    :return: Account page or local recovery page"""
    from corpoch.models import DiscordToken, DiscordUser

    if not request.session.get("access_token") or not request.session.get("user_id"):
        return redirect_discord_login(request)
    try:
        oauth = DiscordToken.objects.get(user__id=request.session.get("user_id"))
        oauth.login()
        context = {"user": OAuthUser(oauth.identity()), "guilds": OAuthGuilds(oauth.guilds())}
        request.session["access_token"] = oauth.access_token
    except (DiscordToken.DoesNotExist, DiscordToken.AuthError):
        return redirect_discord_login(request)

    discord_user = authenticate(request, user=context["user"])
    if not isinstance(discord_user, DiscordUser):
        return discord_login_error(request, status=403)
    login(request, discord_user, backend="corpoch.auth.DiscordBackend")
    context["internal_user"] = discord_user
    return discord_auth_response(render(request, "user.html", context=context))

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
		self.__user = dict(user)
		for field, default in {
			"global_name": None,
			"username": None,
			"public_flags": 0,
			"flags": 0,
			"locale": "",
			"mfa_enabled": False,
			"avatar": None,
		}.items():
			if self.__user.get(field) is None:
				self.__user[field] = default
		self.__user["display_name"] = (
			self.__user["global_name"]
			or self.__user["username"]
			or self.__user.get("display_name")
			or str(self.__user["id"])
		)
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
		return f"https://cdn.discordapp.com/avatars/{self.__user['id']}/{self.__user['avatar']}" if self.__user.get('avatar') else self.__default_avatar

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
