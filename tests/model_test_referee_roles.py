"""Checks guild referee access with fake Discord replies and isolated records."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from django.test import TestCase

from corpoch.dbot.cogs.tourneycmds import DiscordMatch
from corpoch.dbot.models import Channels, Guilds, Roles
from corpoch.models import Bracket, DiscordUser, Tournament
from tests.model_test_upstream_fixes import load_bot_module


class RefereeRoleTests(TestCase):
    """Exercises selection, start authorization and complete membership refreshes."""

    def setUp(self):
        self.guild = Guilds.objects.create(id=8200, name="Fixture Guild")
        self.primary = Roles.objects.create(id=8201, guild=self.guild, name="Referee")
        self.additional = Roles.objects.create(id=8202, guild=self.guild, name="Collaborator")
        self.guild.ref_role = self.primary
        self.guild.save(update_fields=["ref_role"])
        self.guild.additional_ref_roles.add(self.additional)
        self.users = [
            DiscordUser.objects.create(id=8300 + index, global_name=f"Fixture User {index}")
            for index in range(4)
        ]
        self.other_guild = Guilds.objects.create(id=8400, name="Other Guild")
        self.foreign_role = Roles.objects.create(id=8401, guild=self.other_guild, name="Other Referee")
        self.deleted_role = Roles.objects.create(id=8203, guild=self.guild, name="Deleted Referee", deleted=True)
        for path in (
            "corpoch.dbot.cogs.tourneycmds.sync_to_async",
            "django.db.models.query.sync_to_async",
            "django.db.models.base.sync_to_async",
        ):
            boundary = patch(path, side_effect=self.create_immediate_adapter)
            boundary.start()
            self.addCleanup(boundary.stop)

    def create_immediate_adapter(self, function, **options):
        async def adapted(*args, **kwargs):
            return function(*args, **kwargs)

        return adapted

    def run_callback(self, callback, *args):
        coroutine = callback(*args)
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        self.fail("The callback requires an unmocked asynchronous operation.")

    def role_ids(self):
        return set(self.guild.configured_referee_roles().values_list("id", flat=True))

    def stored_referee_ids(self):
        return set(self.guild.referees.values_list("id", flat=True))

    def create_role_reply(self, role, members=(), administrator=False):
        return SimpleNamespace(
            id=role.id,
            name=role.name,
            permissions=SimpleNamespace(administrator=administrator),
            members=[SimpleNamespace(id=user.id, bot=False) for user in members],
        )

    def create_guild_reply(self, roles):
        return SimpleNamespace(
            name=self.guild.name,
            icon=None,
            chunk=AsyncMock(),
            fetch_roles=AsyncMock(return_value=roles),
            fetch_channels=AsyncMock(return_value=[]),
        )

    def refresh_membership(self, guild_reply, module=None):
        bot = SimpleNamespace(get_guild=Mock(return_value=guild_reply))
        if module is not None:
            with patch.object(module, "sync_to_async", side_effect=self.create_immediate_adapter):
                return self.run_callback(module.update_guild, bot, self.guild.id)
        with load_bot_module("bot_tasks") as tasks:
            with patch.object(tasks, "sync_to_async", side_effect=self.create_immediate_adapter):
                return self.run_callback(tasks.update_guild, bot, self.guild.id)

    def create_match_start(self, role_ids=(), administrator=False):
        tournament = Tournament(
            name="Fixture Cup", short_name="FIX", guild=self.guild, active=True,
        )
        tournament.save()
        channel = Channels.objects.create(id=8500, guild=self.guild, name="Fixture Match Channel")
        bracket = Bracket(tournament=tournament, name="Fixture Bracket", score_log=channel, is_active=True)
        bracket.save()
        member = SimpleNamespace(
            get_role=lambda role_id: role_id if role_id in role_ids else None,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        context = SimpleNamespace(
            guild=SimpleNamespace(id=self.guild.id),
            channel=SimpleNamespace(id=channel.id),
            user=member,
            respond=AsyncMock(),
        )
        match = DiscordMatch(SimpleNamespace(), message=context)
        match.showTool = AsyncMock()
        return match, context

    def test_configured_roles_combine_primary_and_additional_without_duplicates(self):
        self.guild.additional_ref_roles.add(self.primary)
        self.assertEqual(self.role_ids(), {self.primary.pk, self.additional.pk})

    def test_configured_roles_ignore_foreign_deleted_and_deleted_guild(self):
        self.guild.additional_ref_roles.add(self.foreign_role, self.deleted_role)
        self.guild.ref_role = self.foreign_role
        self.guild.save(update_fields=["ref_role"])
        self.assertEqual(self.role_ids(), {self.additional.pk})
        self.guild.deleted = True
        self.assertEqual(self.role_ids(), set())

    def test_primary_only_and_additional_only_and_empty_configuration(self):
        self.guild.additional_ref_roles.clear()
        self.assertEqual(self.role_ids(), {self.primary.pk})
        self.guild.ref_role = None
        self.guild.save(update_fields=["ref_role"])
        self.guild.additional_ref_roles.add(self.additional)
        self.assertEqual(self.role_ids(), {self.additional.pk})
        self.guild.additional_ref_roles.clear()
        self.assertEqual(self.role_ids(), set())

    def test_primary_role_can_start_match(self):
        match, context = self.create_match_start([self.primary.pk])
        self.assertTrue(self.run_callback(match.init))
        match.showTool.assert_awaited_once()
        context.respond.assert_not_awaited()

    def test_additional_role_can_start_match_without_primary(self):
        self.guild.ref_role = None
        self.guild.save(update_fields=["ref_role"])
        match, context = self.create_match_start([self.additional.pk])
        self.assertTrue(self.run_callback(match.init))
        match.showTool.assert_awaited_once()

    def test_unconfigured_member_cannot_start_match(self):
        match, context = self.create_match_start([self.foreign_role.pk])
        self.assertFalse(self.run_callback(match.init))
        context.respond.assert_awaited_once_with("You are not a ref for this tournament!", ephemeral=False)
        match.showTool.assert_not_awaited()

    def test_deleted_role_cannot_start_match(self):
        self.guild.additional_ref_roles.add(self.deleted_role)
        match, context = self.create_match_start([self.deleted_role.pk])
        self.assertFalse(self.run_callback(match.init))

    def test_existing_administrator_bypass_survives_empty_role_configuration(self):
        self.guild.ref_role = None
        self.guild.save(update_fields=["ref_role"])
        self.guild.additional_ref_roles.clear()
        match, context = self.create_match_start(administrator=True)
        self.assertTrue(self.run_callback(match.init))

    def test_admin_form_filters_roles_and_accepts_additional_only(self):
        with load_bot_module("admin") as module:
            form = module.GuildAdminForm(
                data={"id": self.guild.id, "ref_role": "", "additional_ref_roles": [self.additional.pk]},
                instance=self.guild,
            )
            for name in ("ref_role", "additional_ref_roles"):
                self.assertEqual(set(form.fields[name].queryset.values_list("id", flat=True)), {self.primary.pk, self.additional.pk})
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.guild.refresh_from_db()
        self.assertIsNone(self.guild.ref_role_id)
        self.assertEqual(self.role_ids(), {self.additional.pk})

    def test_admin_form_rejects_crafted_foreign_and_deleted_role_submissions(self):
        with load_bot_module("admin") as module:
            for invalid_role in (self.foreign_role, self.deleted_role):
                for field in ("ref_role", "additional_ref_roles"):
                    with self.subTest(role=invalid_role.pk, field=field):
                        data = {"id": self.guild.id, "ref_role": "", "additional_ref_roles": []}
                        data[field] = [invalid_role.pk] if field == "additional_ref_roles" else invalid_role.pk
                        form = module.GuildAdminForm(data=data, instance=self.guild)
                        self.assertFalse(form.is_valid())
                        self.assertIn(field, form.errors)

    def test_admin_form_requires_saved_guild_before_selecting_roles(self):
        with load_bot_module("admin") as module:
            form = module.GuildAdminForm()
            self.assertFalse(form.fields["ref_role"].queryset.exists())
            self.assertFalse(form.fields["additional_ref_roles"].queryset.exists())

    def test_sync_unions_roles_deduplicates_and_revokes_old_members_without_admin_grants(self):
        self.guild.referees.add(self.users[3])
        primary = self.create_role_reply(self.primary, self.users[:2])
        additional = self.create_role_reply(self.additional, self.users[1:3])
        additional.members.append(SimpleNamespace(id=8999, bot=True))
        self.refresh_membership(self.create_guild_reply([primary, additional]))
        self.assertEqual(self.stored_referee_ids(), {user.pk for user in self.users[:3]})
        self.assertFalse(DiscordUser.objects.filter(id=8999).exists())
        self.assertFalse(DiscordUser.objects.filter(is_staff=True).exists())
        self.assertFalse(DiscordUser.objects.filter(is_superuser=True).exists())
        self.assertFalse(self.guild.admins.exists())

    def test_sync_clears_when_configured_roles_are_missing_or_removed(self):
        self.guild.referees.add(self.users[0])
        self.refresh_membership(self.create_guild_reply([]))
        self.assertEqual(self.stored_referee_ids(), set())
        self.primary.refresh_from_db()
        self.assertTrue(self.primary.deleted)
        self.guild.referees.add(self.users[0])
        self.guild.ref_role = None
        self.guild.save(update_fields=["ref_role"])
        self.guild.additional_ref_roles.clear()
        self.refresh_membership(self.create_guild_reply([self.create_role_reply(self.primary, self.users)]))
        self.assertEqual(self.stored_referee_ids(), set())

    def test_sync_restores_visible_role_and_guild_and_preserves_admin_membership_logic(self):
        self.primary.deleted = True
        self.primary.save(update_fields=["deleted"])
        self.guild.deleted = True
        self.guild.save(update_fields=["deleted"])
        self.guild.additional_ref_roles.clear()
        admin_role = self.create_role_reply(self.additional, [self.users[3]], administrator=True)
        self.refresh_membership(self.create_guild_reply([
            self.create_role_reply(self.primary, [self.users[0]]), admin_role,
        ]))
        self.assertEqual(self.stored_referee_ids(), {self.users[0].pk})
        self.assertEqual(set(self.guild.admins.values_list("id", flat=True)), {self.users[3].pk})
        self.guild.refresh_from_db()
        self.primary.refresh_from_db()
        self.assertFalse(self.guild.deleted)
        self.assertFalse(self.primary.deleted)

    def test_failed_role_or_channel_lookup_does_not_replace_referees(self):
        self.guild.referees.add(self.users[3])
        for failed_call in ("chunk", "fetch_roles", "fetch_channels"):
            with self.subTest(failed_call=failed_call):
                reply = self.create_guild_reply([self.create_role_reply(self.primary, self.users[:2])])
                getattr(reply, failed_call).side_effect = RuntimeError("Fixture remote failure")
                with self.assertRaisesRegex(RuntimeError, "Fixture remote failure"):
                    self.refresh_membership(reply)
                self.assertEqual(self.stored_referee_ids(), {self.users[3].pk})

    def test_failed_new_user_lookup_does_not_partially_replace_referees(self):
        self.guild.referees.add(self.users[3])
        primary = self.create_role_reply(self.primary, self.users[:2])
        primary.members.append(SimpleNamespace(id=8998, bot=False))
        with load_bot_module("bot_tasks") as module:
            with patch.object(module, "update_user", new=AsyncMock(side_effect=RuntimeError("Fixture profile failure"))):
                with self.assertRaisesRegex(RuntimeError, "Fixture profile failure"):
                    self.refresh_membership(self.create_guild_reply([primary]), module)
        self.assertEqual(self.stored_referee_ids(), {self.users[3].pk})

    def test_sync_ignores_foreign_configured_role(self):
        self.guild.ref_role = self.foreign_role
        self.guild.save(update_fields=["ref_role"])
        self.guild.additional_ref_roles.clear()
        self.guild.additional_ref_roles.add(self.foreign_role)
        self.guild.referees.add(self.users[3])
        self.refresh_membership(self.create_guild_reply([self.create_role_reply(self.primary, self.users)]))
        self.assertEqual(self.stored_referee_ids(), set())

    def test_sync_does_not_overwrite_a_concurrent_primary_role_change(self):
        self.guild.referees.add(self.users[3])
        reply = self.create_guild_reply([self.create_role_reply(self.primary, self.users[:1])])

        async def change_primary_role():
            Guilds.objects.filter(pk=self.guild.pk).update(ref_role=None)
            return []

        reply.fetch_channels.side_effect = change_primary_role
        with self.assertRaisesRegex(RuntimeError, "Referee roles changed during the update"):
            self.refresh_membership(reply)
        self.guild.refresh_from_db()
        self.assertIsNone(self.guild.ref_role_id)
        self.assertEqual(self.stored_referee_ids(), {self.users[3].pk})

    def test_sync_rejects_membership_publication_after_additional_role_change(self):
        self.guild.referees.add(self.users[3])
        reply = self.create_guild_reply([
            self.create_role_reply(self.primary, self.users[:1]),
            self.create_role_reply(self.additional, self.users[1:2]),
        ])

        async def remove_additional_role():
            self.guild.additional_ref_roles.clear()
            return []

        reply.fetch_channels.side_effect = remove_additional_role
        with self.assertRaisesRegex(RuntimeError, "Referee roles changed during the update"):
            self.refresh_membership(reply)
        self.assertFalse(self.guild.additional_ref_roles.exists())
        self.assertEqual(self.stored_referee_ids(), {self.users[3].pk})

    def test_missing_channel_does_not_block_referee_revocation(self):
        self.guild.referees.add(self.users[3])
        channel = Channels.objects.create(id=8501, guild=self.guild, name="Removed Channel")
        reply = self.create_guild_reply([self.create_role_reply(self.primary, self.users[:1])])
        reply.fetch_channel = AsyncMock(side_effect=discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Fixture removed channel",
        ))
        self.refresh_membership(reply)
        channel.refresh_from_db()
        self.assertTrue(channel.deleted)
        self.assertEqual(self.stored_referee_ids(), {self.users[0].pk})

    def test_publication_locks_guild_and_rolls_back_partial_membership_failure(self):
        self.guild.referees.add(self.users[3])
        manager_type = type(self.guild.referees)
        original_set = manager_type.set

        def fail_after_replacement(manager, members):
            original_set(manager, members)
            raise RuntimeError("Fixture membership write failure")

        with load_bot_module("bot_tasks") as module:
            with (
                patch.object(Guilds.objects, "select_for_update", wraps=Guilds.objects.select_for_update) as lock,
                patch.object(manager_type, "set", new=fail_after_replacement),
                self.assertRaisesRegex(RuntimeError, "Fixture membership write failure"),
            ):
                module.publish_guild_referees(self.guild.pk, self.role_ids(), [self.users[0].pk])
            lock.assert_called_once_with()
        self.assertEqual(self.stored_referee_ids(), {self.users[3].pk})
