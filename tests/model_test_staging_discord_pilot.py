"""Checks DEV gateway scope, authorization and cleanup without Discord access."""

import asyncio
from concurrent.futures import Future
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import discord
from django.test import SimpleTestCase

from corpoch.match_actions import StaleMatchAction
from staging.configuration import StagingConfigurationError
from staging.discord_pilot import acquire_pilot_lock, create_pilot_bot, run_database_call, run_gateway


class StagingDiscordPilotTests(SimpleTestCase):
    """Runs real client callbacks with detached data and mocked external awaits."""

    def setUp(self):
        self.snapshot = {
            "match_id": "local-viewer-pilot", "token": "a" * 64,
            "account_id": 123, "guild_id": 456, "role_ids": (789, 790),
        }
        self.bot_id = 321
        self.channel_id = 654
        self.member = SimpleNamespace(
            id=123, bot=False, pending=False, roles=[SimpleNamespace(id=789)],
        )
        self.guild = SimpleNamespace(id=456, me=SimpleNamespace(id=self.bot_id), fetch_member=AsyncMock(return_value=self.member))
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = self.channel_id
        self.channel.guild = self.guild
        self.channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True,
        )
        self.read_snapshot = self.enterContext(patch(
            "staging.discord_fixture.build_control_snapshot", return_value=self.snapshot,
        ))
        self.apply_action = self.enterContext(patch(
            "staging.discord_fixture.apply_control_action", return_value={**self.snapshot, "token": "b" * 64},
        ))
        self.revoke = self.enterContext(patch("staging.viewer_fixture.revoke_fixture_access"))
        self.create_controls = self.enterContext(patch(
            "staging.discord_controls.create_controls", new_callable=AsyncMock,
            return_value=(Mock(), Mock()),
        ))
        self.enterContext(patch("asgiref.sync.sync_to_async", side_effect=self.create_immediate_adapter))
        self.database_call = self.enterContext(patch("staging.discord_pilot.run_database_call", side_effect=self.call_inline))
        self.wait_for = self.enterContext(patch("staging.discord_pilot.asyncio.wait_for", side_effect=self.wait_inline))
        self.enterContext(patch("discord.client._get_event_loop", return_value=SimpleNamespace(create_future=Future)))
        self.bot = create_pilot_bot(self.snapshot, self.bot_id, self.channel_id)
        self.initial_registered = self.bot.registered
        self.bot.registered = True
        self.enterContext(patch.object(type(self.bot), "user", new_callable=PropertyMock, return_value=SimpleNamespace(id=self.bot_id, bot=True)))
        self.enterContext(patch.object(type(self.bot), "guilds", new_callable=PropertyMock, return_value=[self.guild]))
        self.bot.get_guild = Mock(return_value=self.guild)
        self.bot.fetch_channel = AsyncMock(return_value=self.channel)
        self.bot.register_commands = AsyncMock()
        self.bot.process_application_commands = AsyncMock()
        self.bot.close = AsyncMock()
        self.interaction = SimpleNamespace(
            guild_id=456, channel_id=self.channel_id, application_id=self.bot_id,
            data={"name": "viewer-pilot"},
            user=SimpleNamespace(id=123, bot=False),
            response=SimpleNamespace(
                is_done=Mock(return_value=False), defer=AsyncMock(), send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )
        self.interaction.response.defer.side_effect = self.mark_response_done

    def mark_response_done(self, **options):
        self.interaction.response.is_done.return_value = True

    def create_immediate_adapter(self, function, **options):
        async def adapted(*arguments, **keywords):
            return function(*arguments, **keywords)

        return adapted

    def call_inline(self, function, *arguments, **options):
        return function(*arguments, **options)

    async def wait_inline(self, awaitable, timeout):
        self.assertIn(timeout, (15, 30))
        return await awaitable

    def run_callback(self, callback, *arguments, **options):
        coroutine = callback(*arguments, **options)
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        self.fail("The pilot attempted an unmocked asynchronous operation.")

    def test_client_disables_automatic_sync_and_limits_command_to_dev_guild(self):
        self.assertFalse(self.bot.auto_sync_commands)
        self.assertFalse(self.initial_registered)
        self.assertTrue(self.bot.intents.guilds and self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences or self.bot.intents.message_content)
        self.assertEqual(self.bot.pending_application_commands, [self.bot.pilot_command])
        self.assertEqual(self.bot.pilot_command.name, "viewer-pilot")
        self.assertEqual(self.bot.pilot_command.guild_ids, [456])
        self.assertNotIn("integration_types", self.bot.pilot_command.to_dict())
        self.assertEqual(self.bot.allowed_mentions.to_dict()["parse"], [])
        self.bot.register_commands.assert_not_awaited()

    def test_wrong_destination_never_dispatches_responds_or_reads_membership(self):
        for field in ("guild_id", "channel_id", "application_id"):
            with self.subTest(field=field):
                original = getattr(self.interaction, field)
                setattr(self.interaction, field, 999)
                self.assertFalse(self.run_callback(self.bot.authorize, self.interaction))
                self.run_callback(self.bot.on_interaction, self.interaction)
                self.run_callback(self.bot.notify, self.interaction, "Unavailable")
                setattr(self.interaction, field, original)
        self.bot.process_application_commands.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()
        self.interaction.response.defer.assert_not_awaited()
        self.interaction.response.send_message.assert_not_awaited()
        self.interaction.followup.send.assert_not_awaited()
        self.database_call.assert_not_called()

    def test_wrong_user_or_bot_cannot_apply_component_action(self):
        for user in (SimpleNamespace(id=124, bot=False), SimpleNamespace(id=123, bot=True)):
            self.interaction.user = user
            self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.guild.fetch_member.assert_not_awaited()
        self.apply_action.assert_not_called()
        self.create_controls.assert_not_awaited()
        self.assertEqual(self.interaction.response.send_message.await_count, 2)
        for call in self.interaction.response.send_message.await_args_list:
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict()["parse"], [])

    def test_authorization_reads_fresh_member_and_stored_local_access(self):
        self.member.roles = [SimpleNamespace(id=790)]
        self.assertTrue(self.run_callback(self.bot.authorize, self.interaction))
        self.bot.get_guild.assert_called_once_with(456)
        self.guild.fetch_member.assert_awaited_once_with(123)
        self.read_snapshot.assert_called_once_with(123, 456)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, invisible=True)
        self.revoke.assert_not_called()

    def test_removed_role_revokes_only_local_sample_access_and_denies_action(self):
        self.member.roles = [SimpleNamespace(id=456)]
        self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.revoke.assert_called_once_with()
        self.read_snapshot.assert_not_called()
        self.apply_action.assert_not_called()
        self.create_controls.assert_not_awaited()
        self.interaction.followup.send.assert_awaited_once()

    def test_pending_bot_or_wrong_member_identity_revokes_local_access(self):
        for changes in ({"pending": True}, {"bot": True}, {"id": 999}):
            self.guild.fetch_member.return_value = SimpleNamespace(**{**vars(self.member), **changes})
            with self.subTest(changes=changes):
                self.assertFalse(self.run_callback(self.bot.authorize, self.interaction))
        self.assertEqual(self.revoke.call_count, 3)
        self.read_snapshot.assert_not_called()

    def test_missing_member_revokes_but_temporary_rest_failures_do_not(self):
        response = SimpleNamespace(status=404, reason="Not Found")
        self.guild.fetch_member.side_effect = discord.NotFound(response, "Fixture member absent")
        self.assertFalse(self.run_callback(self.bot.authorize, self.interaction))
        self.revoke.assert_called_once_with()
        self.revoke.reset_mock()
        for failure in (
            discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), "Fixture outage"),
            asyncio.TimeoutError(),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.guild.fetch_member.side_effect = failure
                self.assertFalse(self.run_callback(self.bot.authorize, self.interaction))
        self.revoke.assert_not_called()
        self.read_snapshot.assert_not_called()

    def test_disabled_local_access_denies_without_discord_role_mutation(self):
        self.read_snapshot.side_effect = StagingConfigurationError("Fixture local access unavailable")
        self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.apply_action.assert_not_called()
        self.revoke.assert_not_called()
        self.create_controls.assert_not_awaited()

    def test_startup_registers_one_command_without_deleting_existing_commands(self):
        self.bot.registered = False
        with redirect_stdout(io.StringIO()) as output:
            self.run_callback(self.bot.on_ready)
            self.run_callback(self.bot.on_ready)
        self.bot.register_commands.assert_awaited_once_with(
            commands=[self.bot.pilot_command], guild_id=456,
            method="individual", force=False, delete_existing=False,
        )
        self.bot.fetch_channel.assert_awaited_once_with(self.channel_id)
        self.guild.fetch_member.assert_awaited_once_with(123)
        self.assertTrue(self.bot.registered)
        self.assertFalse(self.bot.failed)
        self.bot.close.assert_not_awaited()
        self.assertEqual(output.getvalue().count("READY:"), 1)

    def test_startup_refuses_wrong_bot_or_extra_guild_before_registering(self):
        self.bot.registered = False
        mismatches = (
            ("user", SimpleNamespace(id=999, bot=True)),
            ("guilds", [self.guild, SimpleNamespace(id=999)]),
            ("guilds", []),
        )
        for field, value in mismatches:
            self.bot.failed = False
            with self.subTest(field=field), patch.object(type(self.bot), field, new_callable=PropertyMock, return_value=value):
                with redirect_stdout(io.StringIO()):
                    self.run_callback(self.bot.on_ready)
                self.assertTrue(self.bot.failed)
        self.bot.register_commands.assert_not_awaited()
        self.bot.fetch_channel.assert_not_awaited()
        self.assertEqual(self.bot.close.await_count, 3)

    def test_startup_refuses_foreign_channel_or_missing_required_permission(self):
        self.bot.registered = False
        foreign_channel = Mock(spec=discord.TextChannel)
        foreign_channel.guild = SimpleNamespace(id=999)
        for channel in (SimpleNamespace(guild=self.guild), foreign_channel, self.channel):
            self.bot.failed = False
            self.bot.fetch_channel.return_value = channel
            if channel is self.channel:
                channel.permissions_for.return_value.embed_links = False
            with self.subTest(channel_type=type(channel).__name__), redirect_stdout(io.StringIO()):
                self.run_callback(self.bot.on_ready)
        self.bot.register_commands.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()
        self.assertEqual(self.bot.close.await_count, 3)

    def test_failed_or_closed_session_does_not_retry_command_registration(self):
        self.bot.registered = False
        for failed, closed in ((True, False), (False, True)):
            self.bot.failed = failed
            with self.subTest(failed=failed, closed=closed), patch.object(self.bot, "is_closed", return_value=closed):
                self.run_callback(self.bot.on_ready)
        self.bot.fetch_channel.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()
        self.bot.register_commands.assert_not_awaited()

    def test_dispatch_never_automatically_synchronizes_commands(self):
        self.run_callback(self.bot.on_interaction, self.interaction)
        self.bot.process_application_commands.assert_awaited_once_with(self.interaction, auto_sync=False)
        self.bot.register_commands.assert_not_awaited()

    def test_unregistered_or_failed_session_cannot_authorize_components(self):
        for registered, failed in ((False, False), (True, True)):
            self.bot.registered, self.bot.failed = registered, failed
            with self.subTest(registered=registered, failed=failed):
                self.assertFalse(self.run_callback(self.bot.authorize, self.interaction))
                self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.apply_action.assert_not_called()
        self.guild.fetch_member.assert_not_awaited()
        self.interaction.response.defer.assert_not_awaited()

    def test_unregistered_session_or_other_command_is_not_dispatched(self):
        self.bot.registered = False
        self.run_callback(self.bot.on_interaction, self.interaction)
        self.bot.registered = True
        for name in ("another-command", None):
            self.interaction.data = {"name": name}
            self.run_callback(self.bot.on_interaction, self.interaction)
        self.bot.process_application_commands.assert_not_awaited()

    def test_perform_reauthorizes_and_renders_updated_controls_with_captured_values(self):
        self.run_callback(
            self.bot.perform, self.interaction, "pick", "a" * 64, chart_id=21, player_id=11,
        )
        self.guild.fetch_member.assert_awaited_once_with(123)
        self.read_snapshot.assert_called_once_with(123, 456)
        self.apply_action.assert_called_once_with("pick", "a" * 64, 123, 456, chart_id=21, player_id=11)
        self.create_controls.assert_awaited_once_with(
            self.apply_action.return_value, self.bot.authorize, self.bot.perform, self.bot.notify,
        )
        self.interaction.edit_original_response.assert_awaited_once()
        self.assertIs(self.bot.active_view, self.create_controls.return_value[0])

    def test_opening_controls_requires_authorization_and_does_not_change_match(self):
        self.run_callback(self.bot.open_controls, SimpleNamespace(interaction=self.interaction))
        self.guild.fetch_member.assert_awaited_once_with(123)
        self.assertEqual(self.read_snapshot.call_count, 2)
        self.apply_action.assert_not_called()
        self.create_controls.assert_awaited_once_with(self.snapshot, self.bot.authorize, self.bot.perform, self.bot.notify)
        self.interaction.edit_original_response.assert_awaited_once()

    def test_stale_action_reports_failure_then_renders_fresh_controls(self):
        self.apply_action.side_effect = StaleMatchAction("Fixture stale token")
        self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.assertEqual(self.read_snapshot.call_count, 2)
        self.create_controls.assert_awaited_once_with(self.snapshot, self.bot.authorize, self.bot.perform, self.bot.notify)
        self.interaction.followup.send.assert_awaited_once()
        self.assertIn("match changed", self.interaction.followup.send.await_args.args[0])
        self.revoke.assert_not_called()

    def test_failed_action_does_not_render_a_successful_update(self):
        self.apply_action.side_effect = StagingConfigurationError("Fixture graph changed")
        self.run_callback(self.bot.perform, self.interaction, "undo", "a" * 64)
        self.create_controls.assert_not_awaited()
        self.interaction.edit_original_response.assert_not_awaited()
        self.interaction.followup.send.assert_awaited_once()

    def test_replacing_or_failing_control_message_stops_the_correct_view(self):
        previous = Mock()
        self.bot.active_view = previous
        new_view = self.create_controls.return_value[0]
        self.run_callback(self.bot.show_controls, self.interaction, self.snapshot)
        previous.stop.assert_called_once_with()
        new_view.stop.assert_not_called()
        failed_view = Mock()
        self.create_controls.return_value = (failed_view, Mock())
        self.interaction.edit_original_response.side_effect = RuntimeError("Fixture response failed")
        with self.assertRaises(RuntimeError):
            self.run_callback(self.bot.show_controls, self.interaction, self.snapshot)
        failed_view.stop.assert_called_once_with()
        self.assertIs(self.bot.active_view, new_view)

    def test_close_stops_active_controls_and_closes_only_this_client(self):
        view = Mock()
        self.bot.active_view = view
        with patch.object(discord.Bot, "close", new_callable=AsyncMock) as close_client:
            self.run_callback(type(self.bot).close, self.bot)
        view.stop.assert_called_once_with()
        close_client.assert_awaited_once_with()
        self.assertFalse(self.bot.registered)

    def test_gateway_pins_authenticated_bot_before_connecting_and_always_closes(self):
        for identity in (SimpleNamespace(id=999, bot=True), SimpleNamespace(id=self.bot_id, bot=False)):
            client = SimpleNamespace(user=identity, login=AsyncMock(), connect=AsyncMock(), close=AsyncMock(), failed=False)
            with self.subTest(identity=vars(identity)), patch("staging.discord_pilot.create_pilot_bot", return_value=client):
                with self.assertRaises(StagingConfigurationError):
                    self.run_callback(run_gateway, self.snapshot, self.bot_id, self.channel_id, "fixture-private-token")
            client.login.assert_awaited_once_with("fixture-private-token")
            client.connect.assert_not_awaited()
            client.close.assert_awaited_once_with()
        for failed in (False, True):
            client = SimpleNamespace(
                user=SimpleNamespace(id=self.bot_id, bot=True), login=AsyncMock(), connect=AsyncMock(),
                close=AsyncMock(), failed=failed,
            )
            with patch("staging.discord_pilot.create_pilot_bot", return_value=client):
                self.assertEqual(
                    self.run_callback(run_gateway, self.snapshot, self.bot_id, self.channel_id, "fixture-private-token"),
                    int(failed),
                )
            client.connect.assert_awaited_once_with(reconnect=True)
            client.close.assert_awaited_once_with()

    def test_gateway_login_or_connection_failure_still_closes_client(self):
        for failing_method in ("login", "connect"):
            client = SimpleNamespace(
                user=SimpleNamespace(id=self.bot_id, bot=True), login=AsyncMock(), connect=AsyncMock(),
                close=AsyncMock(), failed=False,
            )
            getattr(client, failing_method).side_effect = RuntimeError("Fixture connection failed")
            with self.subTest(method=failing_method), patch("staging.discord_pilot.create_pilot_bot", return_value=client):
                with self.assertRaises(RuntimeError):
                    self.run_callback(run_gateway, self.snapshot, self.bot_id, self.channel_id, "fixture-private-token")
            client.close.assert_awaited_once_with()

    def test_database_boundary_closes_connection_after_success_or_error(self):
        for failure in (None, RuntimeError("Fixture database failure")):
            operation = Mock(return_value={"detached": True}, side_effect=failure)
            events = []
            with (
                patch("django.db.close_old_connections", side_effect=lambda: events.append("before")),
                patch("django.db.connection.close", side_effect=lambda: events.append("after")),
            ):
                if failure:
                    with self.assertRaises(RuntimeError):
                        run_database_call(operation, 123, guild_id=456)
                else:
                    self.assertEqual(run_database_call(operation, 123, guild_id=456), {"detached": True})
            operation.assert_called_once_with(123, guild_id=456)
            self.assertEqual(events, ["before", "after"])

    @skipUnless(sys.platform == "linux", "The explicit DEV gateway runs under Linux.")
    def test_process_lock_rejects_second_owner_and_releases_after_body_error(self):
        with tempfile.TemporaryDirectory(
            prefix="discord-pilot-lock-", dir=os.environ["CORPO_VIEWER_TEST_DIRECTORY"],
        ) as directory:
            runtime_directory = Path(directory)
            with acquire_pilot_lock(runtime_directory):
                with self.assertRaises(StagingConfigurationError):
                    with acquire_pilot_lock(runtime_directory):
                        self.fail("A second session acquired the active pilot lock.")
            with self.assertRaises(RuntimeError):
                with acquire_pilot_lock(runtime_directory):
                    raise RuntimeError("Fixture session ended unexpectedly")
            with acquire_pilot_lock(runtime_directory):
                self.assertTrue((runtime_directory / "discord-pilot.lock").is_file())
