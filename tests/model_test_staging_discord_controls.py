"""Checks detached DEV controls with real referee callbacks and fake Discord I/O."""

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from django.test import SimpleTestCase

from corpoch.dbot.view.reftool import PlayerRoundSelect, SongRoundSelect
from corpoch.match_actions import (
    cancel_match,
    finalize_match,
    record_round_winner,
    select_chart,
    undo_match_action,
)
from staging.discord_controls import create_controls


class StagingDiscordControlTests(SimpleTestCase):
    """Forbids database queries while exercising the rendered component callbacks."""

    def setUp(self):
        self.snapshot = {
            "match_id": "local-viewer-pilot", "token": "a" * 64,
            "account_id": 123, "guild_id": 456, "role_ids": (789,),
            "players": (
                {"id": 11, "name": "Blue Player", "seed": 1},
                {"id": 12, "name": "Coral Player", "seed": 2},
            ),
            "charts": ({"id": 21, "name": "Synthetic Song 05", "description": "Test chart"},),
            "next_picker_id": 11, "round_number": 1, "current_chart": None,
            "wins": (0, 0), "target": 4, "complete": False,
            "can_finalize": False, "can_undo": False,
        }
        self.authorize = AsyncMock(return_value=True)
        self.perform = AsyncMock(return_value=True)
        self.interaction = SimpleNamespace()
        loop_patch = patch(
            "discord.ui.core.asyncio",
            SimpleNamespace(get_running_loop=lambda: SimpleNamespace(create_future=Future)),
        )
        loop_patch.start()
        self.addCleanup(loop_patch.stop)

    def run_callback(self, callback, *arguments, **options):
        coroutine = callback(*arguments, **options)
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        self.fail("The detached control attempted an unmocked async operation.")

    def build_controls(self):
        return self.run_callback(create_controls, self.snapshot, self.authorize, self.perform)

    def invoke_selection(self, control, value):
        with patch.object(type(control), "values", new_callable=PropertyMock, return_value=[value]):
            self.run_callback(control.callback, self.interaction)

    def test_opening_build_is_query_free_and_has_only_pick_and_disabled_winner(self):
        before = deepcopy(self.snapshot)
        view, embed = self.build_controls()
        self.assertEqual(view.timeout, 900)
        self.assertEqual([item.custom_id for item in view.children], ["roundsong_sel", "roundwin_sel"])
        self.assertFalse(view.children[0].disabled)
        self.assertTrue(view.children[1].disabled)
        self.assertIsNone(view.children[0].options[0].emoji)
        self.assertEqual(self.snapshot, before)
        self.assertIn("First to 4", [field.name for field in embed.fields])
        self.perform.assert_not_awaited()

    def test_song_uses_original_callback_and_exact_snapshot_binding(self):
        view, unused_embed = self.build_controls()
        song = view.children[0]
        self.assertIs(type(song).callback, SongRoundSelect.callback)
        self.invoke_selection(song, "21")
        self.perform.assert_awaited_once_with(
            self.interaction, "pick", "a" * 64, chart_id=21, player_id=11,
        )

    def test_pending_result_uses_original_winner_callback_with_real_player_identity(self):
        self.snapshot.update(current_chart={"id": 21, "name": "Synthetic Song 05"}, can_undo=True)
        view, unused_embed = self.build_controls()
        self.assertTrue(view.children[0].disabled)
        winner = view.children[1]
        self.assertIs(type(winner).callback, PlayerRoundSelect.callback)
        self.assertFalse(winner.disabled)
        self.invoke_selection(winner, winner.options[1].value)
        self.perform.assert_awaited_once_with(self.interaction, "winner", "a" * 64, player_id=12)

    def test_undo_and_finalize_use_original_guarded_callbacks(self):
        self.snapshot.update(can_undo=True, can_finalize=True, wins=(4, 1))
        view, unused_embed = self.build_controls()
        self.assertEqual([item.custom_id for item in view.children], ["backBtn", "submitBtn"])
        self.run_callback(view.children[0].callback, self.interaction)
        self.perform.assert_awaited_with(self.interaction, "undo", "a" * 64)
        self.run_callback(view.children[1].callback, self.interaction)
        self.perform.assert_awaited_with(self.interaction, "finalize", "a" * 64)

    def test_complete_match_offers_reopen_without_screenshot_or_export_controls(self):
        self.snapshot.update(complete=True, can_finalize=True, can_undo=True, wins=(4, 1))
        view, embed = self.build_controls()
        self.assertEqual([item.custom_id for item in view.children], ["backBtn"])
        self.assertEqual(view.children[0].label, "Reopen match")
        self.assertIn("exports are disabled", embed.fields[-1].value)

    def test_view_authorization_denial_is_returned_before_any_mutation(self):
        view, unused_embed = self.build_controls()
        self.authorize.return_value = False
        self.assertFalse(self.run_callback(view.interaction_check, self.interaction))
        self.authorize.assert_awaited_once_with(self.interaction)
        self.perform.assert_not_awaited()

    def test_dispatch_rejects_unavailable_actions_and_wrong_identifiers(self):
        view, unused_embed = self.build_controls()
        rejected = (
            (cancel_match, ("local-viewer-pilot",), {"expected_state": "a" * 64}),
            (select_chart, ("different-match", 21), {"expected_state": "a" * 64, "player_id": 11}),
            (select_chart, ("local-viewer-pilot", 21), {"expected_state": "b" * 64, "player_id": 11}),
            (select_chart, ("local-viewer-pilot", 21), {"expected_state": "a" * 64, "player_id": 12}),
            (select_chart, ("local-viewer-pilot", 99), {"expected_state": "a" * 64, "player_id": 11}),
            (record_round_winner, ("local-viewer-pilot", 11), {"expected_state": "a" * 64}),
            (undo_match_action, ("local-viewer-pilot",), {"expected_state": "a" * 64}),
            (finalize_match, ("local-viewer-pilot",), {"expected_state": "a" * 64}),
        )
        for action, arguments, options in rejected:
            with self.subTest(action=action.__name__, arguments=arguments, options=options):
                with self.assertRaises(ValueError):
                    self.run_callback(view.match.apply_match_action, self.interaction, action, *arguments, **options)
        self.perform.assert_not_awaited()

    def test_selected_chart_is_shown_disabled_even_when_absent_from_remaining_options(self):
        self.snapshot["current_chart"] = {"id": 25, "name": "Previous choice"}
        view, unused_embed = self.build_controls()
        self.assertTrue(view.children[0].disabled)
        self.assertEqual(view.children[0].options[0].value, "25")
        with self.assertRaises(ValueError):
            self.invoke_selection(view.children[0], "25")
        self.perform.assert_not_awaited()

    def test_embed_and_options_contain_no_media_or_mentions_and_fit_discord_limits(self):
        self.snapshot["charts"][0].update(name="Long chart " * 60, description="Details " * 100)
        self.snapshot["players"][0]["name"] = "Long player " * 40
        view, embed = self.build_controls()
        self.assertLessEqual(len(view.children[0].options[0].label), 100)
        self.assertLessEqual(len(view.children[0].options[0].description), 100)
        self.assertLessEqual(len(view.children[0].placeholder), 150)
        self.assertFalse(embed.url)
        self.assertNotIn("image", embed.to_dict())
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertNotIn("<@", str(embed.to_dict()))

    def test_callback_adapter_returns_guarded_mutation_rejection(self):
        self.perform.return_value = False
        view, unused_embed = self.build_controls()
        result = self.run_callback(
            view.match.apply_match_action, self.interaction, select_chart,
            "local-viewer-pilot", 21, expected_state="a" * 64, player_id=11,
        )
        self.assertFalse(result)

    def test_control_error_uses_generic_notice_without_private_error_content(self):
        notify = AsyncMock()
        view, unused_embed = self.run_callback(
            create_controls, self.snapshot, self.authorize, self.perform, notify,
        )
        error = RuntimeError("private-interaction-value")
        with self.assertLogs(view.log, level="WARNING") as captured:
            self.run_callback(view.on_error, error, view.children[0], self.interaction)
        notify.assert_awaited_once_with(
            self.interaction,
            "A control could not finish. Inspect the saved viewer and reopen /viewer-pilot before retrying.",
        )
        self.assertNotIn("private-interaction-value", str(notify.await_args))
        self.assertNotIn("private-interaction-value", str(captured.output))
        self.assertIsNone(captured.records[0].exc_info)
        self.perform.assert_not_awaited()
