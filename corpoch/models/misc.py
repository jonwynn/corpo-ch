import datetime
from requests import RequestException, Session

from django.db import models, transaction
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.utils import timezone

from encrypted_fields.fields import EncryptedJSONField, EncryptedTextField
from solo.models import SingletonModel

from corpoch.managers import DiscordOAuth2Manager

class GSheetAPI(SingletonModel):
	api_key = EncryptedJSONField(null=False, blank=True, default=dict)
	singleton_instance_id = 1

	class Meta:
		verbose_name = "Google Sheets API"
		app_label = 'corpoch'

	def __str__(self):
		return "Google Sheets"

	def name(self):
		if self.api_key:
			return self.api_key.get('client_email')
		else:
			return "None"

	name.short_description = "Service Account Name"
	sa_name = property(name)

class DiscordUser(AbstractUser):
	"""
	Represents a Discord User. 
	"""
	objects = DiscordOAuth2Manager()
	id = models.BigIntegerField(primary_key=True, unique=True, help_text="Discord snowflake ID of user.")
	global_name = models.CharField(max_length=255, null=True, blank=True, help_text="Global or display name used on the account.")
	public_flags = models.IntegerField(null=True, blank=True, help_text="Discord account badge/flag's.")
	flags = models.IntegerField(null=True, blank=True)
	avatar = models.CharField(max_length=255, null=True, blank=True, help_text="URL of users discord avatar.")
	locale = models.CharField(max_length=255, null=True, blank=True, help_text="Users's Discord locale")
	mfa_enabled = models.BooleanField(default=False, help_text="Does user have MFA enabled for their Discord account.")
	last_login = models.DateTimeField(null=True, blank=True, help_text="User's last login time.")

	username = None
	USERNAME_FIELD = 'id'
	REQUIRED_FIELDS = ()

	class Meta:
		verbose_name = "Discord User"
		verbose_name_plural = "Discord Users"
		app_label = 'corpoch'

	def __str__(self):
		if self.global_name:
			return self.global_name
		else:
			return str(self.id)

class DiscordToken(models.Model):
    id = models.AutoField(primary_key=True, help_text="Internal ID of a token.")
    access_token = EncryptedTextField(max_length=255)
    refresh_token = EncryptedTextField(max_length=255)
    scopes = EncryptedTextField(max_length=64, default='identify guilds')
    user = models.OneToOneField(DiscordUser, null=True, on_delete=models.CASCADE, related_name="token")
    expires = models.DateTimeField(verbose_name="Expiry Time", default=timezone.now, help_text="Token expiry time.")

    class Meta:
        verbose_name = "Discord Token"
        verbose_name_plural = "Discord Tokens"
        app_label = 'corpoch'

    class AuthError(Exception):
        def __init__(self, msg) -> None:
            super().__init__(msg)

    class InvalidGrantError(AuthError):
        """Identifies an OAuth grant explicitly rejected as revoked or expired."""

    @property
    def __auth_header(self):
        return (settings.BOT_ID, settings.BOT_SECRET)

    @property
    def __oauth_header(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def login(self, code=None) -> None:
        self.__session = Session()
        self.__base_url = "https://discord.com/api/v10"
        self.__content_header = {'Content-Type': 'application/x-www-form-urlencoded'}
        if not code:
            if not self.access_token or not self.refresh_token:
                raise self.AuthError("Discord login credentials are incomplete.")
        else:
            self.exchange_code({
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.REDIRECT_URI,
            })

    def request_json(self, method, path, **arguments):
        """
        Reads a bounded Discord response without exposing credentials in errors

        :param str method: Session HTTP method
        :param str path: Discord API path
        :param dict arguments: Request headers and form values
        :return: Decoded JSON response"""
        try:
            response = getattr(self.__session, method)(
                f"{self.__base_url}{path}",
                timeout=(5, 15),
                allow_redirects=False,
                **arguments,
            )
        except RequestException:
            raise self.AuthError("Discord authentication request failed.") from None
        if response.status_code != 200:
            if path == "/oauth2/token" and response.status_code == 400:
                try:
                    payload = response.json()
                except ValueError:
                    raise self.AuthError("Discord returned an invalid authentication response.") from None
                if isinstance(payload, dict) and payload.get("error") == "invalid_grant":
                    raise self.InvalidGrantError("Discord login credentials were revoked or expired.")
            raise self.AuthError("Discord authentication request was rejected.")
        try:
            return response.json()
        except ValueError:
            raise self.AuthError("Discord returned an invalid authentication response.") from None

    def exchange_code(self, data):
        """
        Exchanges an authorization code or refresh token and stores valid values

        :param dict data: OAuth grant form values"""
        payload = self.request_json(
            "post", "/oauth2/token", data=data,
            headers=self.__content_header, auth=self.__auth_header,
        )
        self.update_tokens(payload)
        if self.id:
            self.save(update_fields=["access_token", "refresh_token", "scopes", "expires"])

    def refresh_access_token(self, expires_before, remove_invalid_grant=False):
        """
        Serializes renewal against the stored token's latest credentials

        :param datetime expires_before: Latest expiry requiring renewal
        :param bool remove_invalid_grant: Remove a confirmed revoked scheduled grant"""
        if not self.pk:
            if self.expires <= expires_before:
                self.exchange_refresh_token()
            return

        invalid_grant = None
        database = self._state.db or "default"
        with transaction.atomic(using=database):
            try:
                stored = type(self).objects.using(database).select_for_update().get(pk=self.pk)
            except type(self).DoesNotExist:
                raise self.AuthError("Stored Discord login credentials are unavailable.") from None
            for field in ("access_token", "refresh_token", "scopes", "expires"):
                setattr(self, field, getattr(stored, field))
            if self.expires > expires_before:
                return
            try:
                self.exchange_refresh_token()
            except self.InvalidGrantError as failure:
                if not remove_invalid_grant:
                    raise
                stored.delete()
                invalid_grant = failure
        # Raise after the transaction so removal of a confirmed invalid grant commits.
        if invalid_grant is not None:
            raise invalid_grant

    def exchange_refresh_token(self):
        """Exchanges the current refresh token after the caller locks its row."""
        if not self.refresh_token:
            raise self.AuthError("Discord login credentials are incomplete.")
        self.exchange_code({
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
        })

    def update_code(self) -> None:
        """Renews tokens within two days of expiry for the scheduled task."""
        self.refresh_access_token(
            timezone.now() + datetime.timedelta(days=2), remove_invalid_grant=True,
        )

    def update_tokens(self, payload):
        """
        Validates the complete token response before replacing stored values

        :param dict payload: Decoded Discord token response"""
        valid_strings = isinstance(payload, dict) and all(
            isinstance(payload.get(field), str) and payload[field].strip()
            for field in ("access_token", "refresh_token", "scope")
        )
        if not valid_strings:
            raise self.AuthError("Discord returned incomplete login credentials.")
        seconds = payload.get("expires_in")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise self.AuthError("Discord returned an invalid token expiry.")
        try:
            expires = timezone.now() + datetime.timedelta(seconds=seconds)
        except OverflowError:
            raise self.AuthError("Discord returned an invalid token expiry.") from None
        self.access_token = payload["access_token"]
        self.refresh_token = payload["refresh_token"]
        self.scopes = payload["scope"]
        self.expires = expires

    def refresh_expired_token(self):
        """Renews an expired browser token without refreshing every page view."""
        self.refresh_access_token(timezone.now())

    def validate_identity(self, payload):
        """
        Checks the identifier needed by the application's stored relationships

        :param dict payload: Discord user or guild response"""
        identity = payload.get("id") if isinstance(payload, dict) else None
        if not (
            isinstance(identity, str) and identity.isascii()
            and identity.isdigit() and len(identity) <= 19
            and 0 < int(identity) < 2 ** 63
        ):
            raise self.AuthError("Discord returned an invalid account response.")

    def identity(self) -> dict:
        self.refresh_expired_token()
        payload = self.request_json("get", "/users/@me", headers=self.__oauth_header)
        self.validate_identity(payload)
        for field in ("avatar", "global_name", "username"):
            if payload.get(field) is not None and not isinstance(payload[field], str):
                raise self.AuthError("Discord returned an invalid account response.")
        return payload

    def guilds(self) -> list:
        self.refresh_expired_token()
        payload = self.request_json("get", "/users/@me/guilds", headers=self.__oauth_header)
        if not isinstance(payload, list):
            raise self.AuthError("Discord returned an invalid guild response.")
        for guild in payload:
            self.validate_identity(guild)
        return payload
