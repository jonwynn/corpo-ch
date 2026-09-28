from django.db import models

class CHEmoji(models.Model):
	"""
	Represents a Discord Emoji used with a Chart Icon.
	"""
	id = models.BigIntegerField(verbose_name="AppEmoji ID", db_index=True, primary_key=True, help_text="Discord snowflake ID of the AppEmoji.")
	icon = models.ForeignKey('corpoch.CHIcon', related_name="discord", verbose_name="Emote ID", null=True, blank=True, default=-1, on_delete=models.CASCADE, help_text="Associated CHIcon.")
	name = models.CharField(max_length=16, null=True, blank=True, help_text="Name for emoji (optional)")

	class Meta:
		verbose_name = "Chart Icon"
		verbose_name_plural = "Chart Icons"

	@property
	def mention(self):
		if self.name:
			return f"<:{self.name}:{self.id}>"
		else:
			return f"<:{self.icon.name}:{self.id}>"

class Guilds(models.Model):
	"""
	Represents a Discord Guild a Tournament is ran in.
	"""
	id = models.BigIntegerField(primary_key=True, help_text="Discord snowflake ID for a Guild.")
	name = models.CharField(max_length=100, null=True, blank=True, help_text="Name of a Discord Guild.")
	icon = models.CharField(max_length=255, null=True, blank=True, help_text="The avatar for a Guild.")
	deleted = models.BooleanField(default=False, help_text="Is deleted/not visible by bot.")
	ref_role = models.ForeignKey("Roles", related_name="role_ref", verbose_name="Discord Ref Role", on_delete=models.SET_NULL, null=True, blank=True, help_text="Discord Role for referee's to start matches.")
	additional_ref_roles = models.ManyToManyField('Roles', related_name='additional_ref_guilds', verbose_name='Additional Discord Ref Roles', blank=True, help_text='Additional roles in this guild that can start matches. Any configured referee role is sufficient.')
	admins = models.ManyToManyField('corpoch.DiscordUser', related_name="guilds_admin", verbose_name="Tournament Guild Admins", help_text="Admin users for this guild.", blank=True)
	referees = models.ManyToManyField('corpoch.DiscordUser', related_name="guilds_referee", verbose_name="Tournament Guild Referee", help_text="Referee users for this guild", blank=True)

	class Meta:
		verbose_name = 'Guild'
		verbose_name_plural = 'Guilds'

	def __str__(self):
		return str(self.name)

	def configured_referee_roles(self):
		"""Returns active referee roles belonging to this guild."""
		if not self.pk or self.deleted:
			return Roles.objects.none()
		return Roles.objects.filter(guild_id=self.pk, deleted=False).filter(
			models.Q(pk=self.ref_role_id) | models.Q(additional_ref_guilds=self),
		).distinct()

class Channels(models.Model):
	"""
	Represents a channel in a Discord Guild for a Tournament. 
	"""
	id = models.BigIntegerField(primary_key=True, help_text="Discord snowflake ID of a channel.")
	guild = models.ForeignKey(Guilds, on_delete=models.CASCADE, help_text="Guild this channel is in.")
	name = models.CharField(max_length=100, null=True, blank=True, help_text="Name of a discord channel.")
	deleted = models.BooleanField(default=False, help_text="Is deleted/not visible by bot.")

	def __str__(self):
		return f'#{self.name}'

	class Meta:
		verbose_name = 'Channel'
		verbose_name_plural = 'Channels'

class Roles(models.Model):
	"""
	Represents a role in a Discord Guild for a Tournament. 
	"""
	id = models.BigIntegerField(primary_key=True, help_text="Discord snowflake ID of a role.")
	guild = models.ForeignKey(Guilds, on_delete=models.CASCADE, help_text="Guild this channel is in.")
	name = models.CharField(max_length=100, null=True, blank=True, help_text="Name of a discord role.")
	deleted = models.BooleanField(default=False, help_text="Is deleted/not visible by bot.")

	class Meta:
		verbose_name = 'Role'
		verbose_name_plural = 'Roles'

	def __str__(self):
		return str(self.name)
