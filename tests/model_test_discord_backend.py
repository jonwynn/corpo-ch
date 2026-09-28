"""Checks account status and database failures at the Discord login boundary."""

from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from corpoch.auth import DiscordBackend
from corpoch.models import DiscordUser


class DiscordBackendTests(TestCase):
    """Keeps disabled accounts and failed writes from becoming signed-in users."""

    def setUp(self):
        self.backend = DiscordBackend()
        self.identity = SimpleNamespace(
            id=8910,
            global_name="Updated player",
            display_name="Fixture player",
            avatar=None,
            public_flags=0,
            flags=0,
            locale="en-US",
            mfa_enabled=False,
        )

    def test_active_account_receives_profile_update_and_authenticates(self):
        account = DiscordUser.objects.create(id=self.identity.id, global_name="Old name")
        authenticated = self.backend.authenticate(None, self.identity)
        self.assertEqual(authenticated, account)
        account.refresh_from_db()
        self.assertEqual(account.global_name, "Updated player")
        self.assertIsNotNone(account.last_login)
        self.assertEqual(self.backend.get_user(account.pk), account)

    def test_disabled_account_is_not_updated_or_loaded_as_authenticated(self):
        account = DiscordUser.objects.create(
            id=self.identity.id, global_name="Old name", is_active=False,
        )
        self.assertIsNone(self.backend.authenticate(None, self.identity))
        self.assertIsNone(self.backend.get_user(account.pk))
        account.refresh_from_db()
        self.assertEqual(account.global_name, "Old name")
        self.assertIsNone(account.last_login)

    def test_profile_write_error_propagates_instead_of_returning_account(self):
        account = DiscordUser.objects.create(id=self.identity.id, global_name="Old name")
        with patch.object(DiscordUser, "save", side_effect=DatabaseError("fixture write failure")):
            with self.assertRaisesRegex(DatabaseError, "fixture write failure"):
                self.backend.authenticate(None, self.identity)
        account.refresh_from_db()
        self.assertEqual(account.global_name, "Old name")

    def test_lookup_failure_is_not_masked_by_an_unbound_return(self):
        with patch.object(DiscordUser.objects, "get", side_effect=DatabaseError("fixture read failure")):
            with self.assertRaisesRegex(DatabaseError, "fixture read failure"):
                self.backend.authenticate(None, self.identity)

    def test_new_account_creation_failure_propagates(self):
        with patch.object(
            DiscordUser.objects, "create_new_discord_user",
            side_effect=DatabaseError("fixture creation failure"),
        ):
            with self.assertRaisesRegex(DatabaseError, "fixture creation failure"):
                self.backend.authenticate(None, self.identity)
        self.assertFalse(DiscordUser.objects.exists())

    def test_new_account_uses_existing_manager_contract(self):
        account = DiscordUser(id=self.identity.id, global_name="New fixture")
        with patch.object(DiscordUser.objects, "create_new_discord_user", return_value=account) as create:
            self.assertIs(self.backend.authenticate(None, self.identity), account)
        create.assert_called_once_with(self.identity)

    def test_removed_account_cannot_restore_authenticated_session(self):
        self.assertIsNone(self.backend.get_user(self.identity.id))
