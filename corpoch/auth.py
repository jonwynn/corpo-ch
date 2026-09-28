from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.utils import timezone

class DiscordBackend(ModelBackend):
	def authenticate(self, request, user):
		UserModel = get_user_model()
		try:
			check_user = UserModel.objects.get(id=user.id)
		except UserModel.DoesNotExist:
			check_user = UserModel.objects.create_new_discord_user(user)
		else:
			if not self.user_can_authenticate(check_user):
				return None
			check_user.global_name = user.global_name if user.global_name else user.display_name
			check_user.avatar = user.avatar
			check_user.public_flags = user.public_flags
			check_user.flags = user.flags
			check_user.locale = user.locale
			check_user.mfa_enabled = user.mfa_enabled
			check_user.last_login = timezone.now()
			check_user.save()
		return check_user if self.user_can_authenticate(check_user) else None

	def get_user(self, user_id):
		UserModel = get_user_model()
		try:
			user = UserModel.objects.get(id=user_id)
		except UserModel.DoesNotExist:
			return None
		return user if self.user_can_authenticate(user) else None
