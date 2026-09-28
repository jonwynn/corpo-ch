import datetime
from requests import Session

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone

from encrypted_fields.fields import EncryptedJSONField, EncryptedTextField
from solo.models import SingletonModel

from corpoch import settings
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
	#This needs an expiry date field to trigger refreshes - refresh tokens need to be handled

	class Meta:
		verbose_name = "Discord Token"
		verbose_name_plural = "Discord Tokens"
		app_label = 'corpoch'

	class AuthError(Exception):
		def __init__(self, msg) -> None:
			super().__init__(msg)

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
		self.__auth = { "client_id": settings.BOT_ID, "client_secret": settings.BOT_SECRET }
		if not code:
			if not self.access_token or not self.refresh_token:
				raise self.AuthError("Access/Refresh token not set and code is none!")
			else:
				self.__data = { "grant_type": "refresh_token", "refresh_token": self.refresh_token }
		else:
			self.__data = { "grant_type": "authorization_code",	"code": code, "redirect_uri": settings.REDIRECT_URI }
			self.__exchange_code()
	
	def __exchange_code(self):
		response = self.__session.post(f"{self.__base_url}/oauth2/token", data=self.__data, headers=self.__content_header, auth=self.__auth_header)
		if response.status_code == 200:
			self.__update(response.json())
			if self.id:
				self.save()
			return
		raise self.AuthError(f"Failed to connect to discord API {response.json()}")
	
	def update_code(self) -> str:
		if self.expires < timezone.now() + datetime.timedelta(days=2):
			self.__exchange_code()

	def __update(self, json: dict):
		self.access_token = json["access_token"]
		self.refresh_token = json["refresh_token"]
		self.scopes = json['scope']
		self.expires = timezone.now() + datetime.timedelta(seconds=json['expires_in'])

	def identity(self) -> dict:
		if not self.access_token:
			self.__exchange_code()

		response =  self.__session.get(f"{self.__base_url}/users/@me", headers=self.__oauth_header)
		if response.status_code == 200:
			return response.json()
		raise self.AuthError("Failed to connect to discord API")

	def guilds(self) -> list:
		if not self.access_token:
			self.__exchange_code()

		response = self.__session.get(f"{self.__base_url}/users/@me/guilds", headers=self.__oauth_header)
		if response.status_code == 200:
			return response.json()
		raise self.AuthError("Failed to connect to discord API")
