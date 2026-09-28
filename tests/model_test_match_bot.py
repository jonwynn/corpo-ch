"""Exercises referee controls using real isolated records and fake Discord I/O."""

import io
import os
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from django.core.files.base import ContentFile
from django.test import TransactionTestCase

from corpoch.dbot.cogs.tourneycmds import DiscordMatch
from corpoch.dbot.view.reftool import (
    BanSelect, DiscordMatchView, MatchScreenModal, PlayerRoundSelect, PlayerSelect,
    RoundReview, SongRoundSelect,
    publish_evidence_file,
)
from corpoch.match_actions import (
    finalize_match, get_match_state_token, record_opening_action,
    record_round_winner, select_chart, undo_match_action,
)
from corpoch.models import DiscordUser, Match, MatchRound
from corpoch.types import StegScreenshot, StegScreenshotPlayer
from tests.match_fixtures import create_corp_match


class MatchBotTests(TransactionTestCase):
    """Runs callback logic with fake Discord awaits and real synchronous writes."""

    def setUp(self):
        self.match, self.seeds, self.charts = create_corp_match()
        self.player_ids = tuple(seed.player_id for seed in self.seeds)
        self.referee = DiscordUser.objects.create(id=9100, global_name="Fixture Referee")
        for index, seed in enumerate(self.seeds):
            user = DiscordUser.objects.create(id=9200 + index, global_name=seed.player.name)
            seed.player.user = user
            seed.player.save(update_fields=["user"])
        for index, chart in enumerate(self.charts):
            chart.md5 = f"{index:032x}"
            chart.save(update_fields=["md5"])
        self.bot = SimpleNamespace(matches={}, owners=[])
        self.wrapper = DiscordMatch(self.bot)
        self.wrapper.referee = SimpleNamespace(id=self.referee.pk)
        self.wrapper.msg = SimpleNamespace(id=9300)
        self.wrapper.showTool = AsyncMock()
        self.interaction = SimpleNamespace(
            response=SimpleNamespace(
                is_done=lambda: False, send_message=AsyncMock(),
                defer=AsyncMock(), edit_message=AsyncMock(),
                send_modal=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            user=SimpleNamespace(id=self.referee.pk),
            message=self.wrapper.msg,
        )
        # Windows event loops create a loopback socketpair. Keep network access
        # blocked and resolve fake Discord awaits and sync_to_async boundaries
        # inline. These tests verify callback/data behavior, not event scheduling.
        for path in (
            "corpoch.dbot.view.reftool.sync_to_async",
            "corpoch.dbot.cogs.tourneycmds.sync_to_async",
            "django.db.models.query.sync_to_async",
            "django.db.models.base.sync_to_async",
        ):
            boundary_patch = patch(path, side_effect=self.create_immediate_adapter)
            boundary_patch.start()
            self.addCleanup(boundary_patch.stop)
        loop_patch = patch(
            "discord.ui.core.asyncio",
            SimpleNamespace(get_running_loop=lambda: SimpleNamespace(create_future=Future)),
        )
        loop_patch.start()
        self.addCleanup(loop_patch.stop)
        self.refresh_wrapper()

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

    def refresh_wrapper(self):
        self.match.refresh_from_db()
        self.wrapper.matchDb = Match.objects.get(pk=self.match.pk)
        self.wrapper.bracket = self.wrapper.matchDb.group.bracket
        self.wrapper.render_state = get_match_state_token(self.wrapper.matchDb)

    def complete_opening(self, saves=False):
        for player_id, chart, saved in (
            (self.player_ids[0], self.charts[0], False),
            (self.player_ids[1], self.charts[0 if saves else 1], saves),
            (self.player_ids[1], self.charts[2], False),
            (self.player_ids[0], self.charts[2 if saves else 3], saves),
        ):
            self.match = record_opening_action(
                self.match.pk, player_id, chart.pk, saved=saved,
                expected_state=get_match_state_token(self.match),
            )
        self.refresh_wrapper()

    def select_current(self, chart_index=4):
        self.match = select_chart(
            self.match.pk, self.charts[chart_index].pk,
            player_id=self.match.picking_player.pk,
            expected_state=get_match_state_token(self.match),
        )
        self.refresh_wrapper()

    def finalize_fixture(self):
        self.complete_opening()
        for index in range(4):
            self.select_current(index + 4)
            self.match = record_round_winner(
                self.match.pk, self.player_ids[0],
                expected_state=get_match_state_token(self.match),
            )
        self.match = finalize_match(self.match.pk, expected_state=get_match_state_token(self.match))
        self.refresh_wrapper()

    def invoke_selection(self, control, values):
        with patch.object(type(control), "values", new_callable=PropertyMock, return_value=values):
            self.run_callback(control.callback, self.interaction)

    def build_view(self):
        return DiscordMatchView(self.wrapper)

    def test_referee_import_does_not_initialize_provider_or_bot_settings(self):
        self.assertNotIn("corpoch.providers", sys.modules)
        self.assertNotIn("corpoch.dbot.settings", sys.modules)

    def test_ban_callback_uses_captured_actor_and_rejects_duplicate(self):
        control = BanSelect(self.wrapper)
        control.retOpts = {"chart": self.charts[0]}
        self.invoke_selection(control, ["chart"])
        self.assertEqual(self.match.match_bans.count(), 1)
        self.assertEqual(self.match.match_bans.get().player_id, self.player_ids[0])
        self.invoke_selection(control, ["chart"])
        self.assertEqual(self.match.match_bans.count(), 1)
        self.interaction.response.send_message.assert_awaited_once()
        self.assertEqual(self.wrapper.showTool.await_count, 2)

    def test_save_button_uses_rendered_opponents_ban(self):
        self.match = record_opening_action(
            self.match.pk, self.player_ids[0], self.charts[0].pk,
            expected_state=get_match_state_token(self.match),
        )
        self.refresh_wrapper()
        view = self.build_view()
        self.run_callback(view.saveBtn, self.interaction)
        saved = self.match.match_bans.get(num=1)
        self.assertTrue(saved.saved)
        self.assertEqual((saved.player_id, saved.chart_id), (self.player_ids[1], self.charts[0].pk))

    def test_song_and_winner_callbacks_advance_once_and_preserve_loser_pick(self):
        self.complete_opening()
        song_control = SongRoundSelect(self.wrapper, False)
        song_control.retOpts = {"chart": self.charts[4]}
        self.invoke_selection(song_control, ["chart"])
        self.refresh_wrapper()
        winner_control = PlayerRoundSelect(self.wrapper, False)
        winner_control.retOpts = {"winner": self.seeds[1]}
        self.invoke_selection(winner_control, ["winner"])
        self.assertEqual(self.match.rounds.count(), 2)
        self.assertEqual(self.wrapper.matchDb.picking_player.pk, self.player_ids[0])
        self.invoke_selection(winner_control, ["winner"])
        self.assertEqual(self.match.rounds.count(), 2)
        self.assertEqual(self.match.score_int, [0, 1])

    def test_back_button_clears_selection_through_the_locked_service(self):
        self.complete_opening()
        self.select_current()
        view = self.build_view()
        self.run_callback(view.backBtn, self.interaction)
        current = self.match.current_round
        self.assertIsNone(current.chart_id)
        self.assertIsNone(current.picked_id)
        self.assertEqual(current.selection_kind, "unknown")

    def test_corp_controls_do_not_offer_seed_swapping_or_deferral(self):
        view = self.build_view()
        with patch("corpoch.dbot.view.reftool.get_chart_emoji", new=AsyncMock(return_value=None)):
            self.run_callback(view.init)
        control_ids = {control.custom_id for control in view.children}
        self.assertNotIn("deferBtn", control_ids)
        self.assertNotIn("seedSwapBtn", control_ids)
        self.assertIn("ban_sel", control_ids)

    def test_player_authorization_follows_current_picker_and_rejects_stale_view(self):
        self.wrapper.player_input = True
        view = self.build_view()
        self.interaction.custom_id = "ban_sel"
        self.interaction.user = SimpleNamespace(id=self.seeds[0].player.user_id)
        self.assertTrue(self.run_callback(view.interaction_check, self.interaction))
        self.interaction.user = SimpleNamespace(id=self.seeds[1].player.user_id)
        self.assertFalse(self.run_callback(view.interaction_check, self.interaction))
        self.match = record_opening_action(
            self.match.pk, self.player_ids[0], self.charts[0].pk,
            expected_state=get_match_state_token(self.match),
        )
        self.assertFalse(self.run_callback(view.interaction_check, self.interaction))
        self.wrapper.showTool.assert_awaited_once()

    def test_forced_tiebreaker_offers_winner_without_another_ban(self):
        self.complete_opening()
        for index in range(6):
            self.select_current(index + 4)
            self.match = record_round_winner(
                self.match.pk, self.player_ids[index % 2],
                expected_state=get_match_state_token(self.match),
            )
        self.refresh_wrapper()
        self.assertEqual(self.match.current_round.selection_kind, "automatic")
        view = self.build_view()
        with patch("corpoch.dbot.view.reftool.get_chart_emoji", new=AsyncMock(return_value=None)):
            self.run_callback(view.init)
        controls = {control.custom_id: control for control in view.children}
        self.assertNotIn("ban_sel", controls)
        self.assertTrue(controls["roundsong_sel"].disabled)
        self.assertTrue(controls["roundsong_sel"].placeholder.startswith("Automatic tiebreaker"))
        self.assertFalse(controls["roundwin_sel"].disabled)
        self.assertEqual(self.match.match_bans.count(), 4)

    def test_submit_button_finalizes_result_before_evidence_completion(self):
        self.complete_opening()
        for index in range(4):
            self.select_current(index + 4)
            self.match = record_round_winner(
                self.match.pk, self.player_ids[0],
                expected_state=get_match_state_token(self.match),
            )
        self.refresh_wrapper()
        view = self.build_view()
        self.run_callback(view.init)
        self.assertFalse(view.submit.disabled)
        self.run_callback(view.submitBtn, self.interaction)
        self.match.refresh_from_db()
        self.assertTrue(self.match.complete)
        self.assertFalse(self.match.finished)
        self.assertEqual(self.match.winner_id, self.player_ids[0])

    def test_player_assignment_uses_selected_seeds_and_rejects_repeat(self):
        self.match.players.clear()
        self.refresh_wrapper()
        control = PlayerSelect(self.wrapper)
        control.retOpts = {"higher": self.seeds[0], "lower": self.seeds[1]}
        self.invoke_selection(control, ["lower", "higher"])
        self.assertEqual(list(self.match.players.values_list("pk", flat=True)), [seed.pk for seed in self.seeds])
        self.invoke_selection(control, ["lower", "higher"])
        self.interaction.response.send_message.assert_awaited_once()

    def test_stale_cancel_preserves_match_and_current_confirmation_deletes(self):
        view = self.build_view()
        self.wrapper.confirm_cancel = True
        self.match = record_opening_action(
            self.match.pk, self.player_ids[0], self.charts[0].pk,
            expected_state=get_match_state_token(self.match),
        )
        self.run_callback(view.cancelBtn, self.interaction)
        self.assertTrue(Match.objects.filter(pk=self.match.pk).exists())
        self.refresh_wrapper()
        self.run_callback(self.build_view().cancelBtn, self.interaction)
        self.assertFalse(Match.objects.filter(pk=self.match.pk).exists())
        self.interaction.response.edit_message.assert_awaited_once()

    def test_legacy_selection_and_back_remain_persisted_without_full_render_save(self):
        rules = self.match.ruleset
        rules.tb_ruleset = "refdecide"
        rules.ban_ruleset = "deferboth"
        rules.save()
        self.refresh_wrapper()
        self.run_callback(self.build_view().deferBtn, self.interaction)
        self.match.refresh_from_db()
        self.assertTrue(self.match.defer)
        self.refresh_wrapper()
        self.run_callback(self.build_view().seedSwapBtn, self.interaction)
        self.match.refresh_from_db()
        self.assertTrue(self.match.rev_seeds)
        for index in range(4):
            self.match.add_ban(self.match.picking_player, self.charts[index])
        self.refresh_wrapper()
        song_control = SongRoundSelect(self.wrapper, False)
        song_control.retOpts = {"chart": self.charts[4]}
        self.invoke_selection(song_control, ["chart"])
        self.assertEqual(self.match.current_round.chart_id, self.charts[4].pk)
        self.refresh_wrapper()
        self.run_callback(self.build_view().backBtn, self.interaction)
        self.assertIsNone(self.match.current_round.chart_id)

    def test_render_metadata_save_does_not_restore_old_results_or_decode_files(self):
        self.complete_opening()
        self.select_current()
        self.wrapper.matchDb.complete = False
        self.wrapper.matchDb.finished = False
        Match.objects.filter(pk=self.match.pk).update(complete=True, finished=True)
        MatchRound.objects.filter(match=self.match).update(screenshot="existing.png", steg=None)
        self.wrapper.save_match()
        self.match.refresh_from_db()
        self.assertTrue(self.match.complete)
        self.assertTrue(self.match.finished)
        self.assertEqual(self.match.current_round.screenshot.name, "existing.png")
        self.assertEqual(self.match.referee_id, self.referee.pk)

    def test_redraw_clears_controls_for_deleted_match(self):
        self.bot.matches[self.match.pk] = self.wrapper
        self.match.delete()
        self.run_callback(DiscordMatch.showTool, self.wrapper, self.interaction)
        self.assertIsNone(self.wrapper.matchDb)
        self.assertFalse(self.bot.matches)
        self.interaction.edit_original_response.assert_awaited_once_with(
            content="Match no longer available.", embeds=[], view=None,
        )

    def test_invalid_history_keeps_staff_removal_controls_until_recovered(self):
        self.complete_opening()
        self.match.rounds.update(chart=self.charts[4], selection_kind="unknown")
        MatchRound.objects.create(match=self.match, num=2, chart=self.charts[5])
        self.wrapper.genMatchEmbed = AsyncMock()
        self.run_callback(DiscordMatch.showTool, self.wrapper, self.interaction)
        view = self.interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual({item.custom_id for item in view.children}, {"backBtn", "cancelBtn"})
        self.assertEqual(view.back.label, "Remove last recorded action")
        self.assertFalse(view.back.disabled)
        self.wrapper.genMatchEmbed.assert_not_awaited()
        self.interaction.custom_id = "backBtn"
        self.interaction.user = SimpleNamespace(id=self.seeds[0].player.user_id)
        self.assertFalse(self.run_callback(view.interaction_check, self.interaction))
        self.interaction.user = SimpleNamespace(id=self.referee.pk)
        self.assertTrue(self.run_callback(view.interaction_check, self.interaction))
        self.run_callback(view.backBtn, self.interaction)
        self.assertEqual(self.match.rounds.count(), 1)
        self.run_callback(DiscordMatch.showTool, self.wrapper, self.interaction)
        next_view = self.interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(next_view.back.label, "Remove last recorded action")
        self.run_callback(next_view.backBtn, self.interaction)
        self.assertEqual(self.match.rounds.count(), 1)
        self.assertIsNone(self.match.current_round.chart_id)
        self.assertEqual(self.wrapper.load_recovery_status(), (None, False))

    def test_invalid_profile_disables_history_discard_and_explains_admin_repair(self):
        Match.objects.filter(pk=self.match.pk).update(rev_seeds=True)
        self.run_callback(DiscordMatch.showTool, self.wrapper, self.interaction)
        response = self.interaction.edit_original_response.await_args.kwargs
        self.assertTrue(response["view"].back.disabled)
        self.assertIn("administration page", response["embeds"][0].fields[0].value)

    def test_completed_match_reopen_is_available_only_to_staff(self):
        self.finalize_fixture()
        view = self.build_view()
        self.run_callback(view.init)
        self.assertEqual(view.back.label, "Reopen match")
        self.assertIn(view.back, view.children)
        self.interaction.user = SimpleNamespace(id=self.seeds[0].player.user_id)
        self.interaction.custom_id = "backBtn"
        self.assertFalse(self.run_callback(view.interaction_check, self.interaction))
        self.interaction.custom_id = "uploadBtn"
        self.assertTrue(self.run_callback(view.interaction_check, self.interaction))
        self.interaction.user = SimpleNamespace(id=self.referee.pk)
        self.interaction.custom_id = "backBtn"
        self.assertTrue(self.run_callback(view.interaction_check, self.interaction))
        self.run_callback(view.backBtn, self.interaction)
        self.match.refresh_from_db()
        self.assertFalse(self.match.complete)
        self.assertEqual(self.match.score_int, [4, 0])

    def test_legacy_completed_view_does_not_offer_corp_reopen(self):
        self.finalize_fixture()
        rules = self.match.ruleset
        rules.tb_ruleset = "bansave"
        rules.save()
        self.refresh_wrapper()
        view = self.build_view()
        self.run_callback(view.init)
        self.assertNotIn("backBtn", {item.custom_id for item in view.children})
        self.assertIn("uploadBtn", {item.custom_id for item in view.children})

    def test_completed_invalid_history_reopens_before_discarding_rounds(self):
        self.finalize_fixture()
        self.match.rounds.update(selection_kind="unknown")
        self.run_callback(DiscordMatch.showTool, self.wrapper, self.interaction)
        view = self.interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(view.back.label, "Reopen recorded match")
        self.run_callback(view.backBtn, self.interaction)
        self.match.refresh_from_db()
        self.assertFalse(self.match.complete)
        self.assertEqual(self.match.rounds.count(), 4)

    def test_delayed_screenshot_publication_rejects_undo_and_removes_new_file(self):
        self.finalize_fixture()
        round_snapshot = self.match.rounds.first()
        expected_state = get_match_state_token(self.match)
        self.match = undo_match_action(self.match.pk, expected_state=expected_state)
        metadata = StegScreenshot(players=[StegScreenshotPlayer(profile_name="Higher Seed", controller_type="Guitar")])
        with self.assertRaises(ValueError):
            publish_evidence_file(round_snapshot, "delayed.png", ContentFile(b"fixture-image"), metadata, expected_state)
        self.assertFalse(round_snapshot.screenshot.storage.exists(round_snapshot.screenshot.name))
        self.assertFalse(self.match.rounds.get(pk=round_snapshot.pk).screenshot)
        self.assertFalse(self.match.complete)

    def test_review_without_missing_players_preserves_metadata_and_scopes_write(self):
        self.finalize_fixture()
        round_snapshot = self.match.rounds.first()
        metadata = StegScreenshot(players=[
            StegScreenshotPlayer(profile_name=seed.player.ch_name, controller_type="Guitar")
            for seed in self.seeds
        ])
        attachment = SimpleNamespace(filename="review.png", fp=io.BytesIO(b"fixture-image"), close=Mock())
        screen = SimpleNamespace(filename="review.png", to_file=AsyncMock(return_value=attachment))
        message = SimpleNamespace(delete=AsyncMock())
        review = RoundReview(
            self.match, message, screen, metadata, round_snapshot=round_snapshot,
            expected_state=get_match_state_token(self.match),
        )
        refreshed = self.run_callback(review.fix)
        saved_round = refreshed.rounds.get(pk=round_snapshot.pk)
        self.assertEqual(len(saved_round.steg.players), 2)
        self.assertEqual(saved_round.winner_id, self.player_ids[0])
        self.assertTrue(saved_round.screenshot)
        self.assertTrue(refreshed.complete)
        self.assertFalse(refreshed.finished)
        message.delete.assert_awaited_once()
        attachment.close.assert_called_once()

    def test_screenshot_upload_accepts_matching_names_and_captures_before_decode(self):
        self.finalize_fixture()
        view = self.build_view()
        self.wrapper.finishMatch = AsyncMock()
        round_snapshot = self.match.rounds.first()
        metadata = StegScreenshot(
            checksum=round_snapshot.chart.md5,
            game_version=self.match.tournament.config.version,
            players=[StegScreenshotPlayer(profile_name=seed.player.ch_name, controller_type="Guitar") for seed in self.seeds],
        )
        path = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"]) / "decoded-fixture.png"
        path.write_bytes(b"fixture-image")
        screen = SimpleNamespace(filename="upload.png")
        modal = SimpleNamespace(screens=[screen], wait=AsyncMock())
        tool = SimpleNamespace(img_path=path, getStegInfo=AsyncMock(return_value=metadata))
        with (
            patch("corpoch.dbot.view.reftool.MatchScreenModal", return_value=modal),
            patch("corpoch.dbot.view.reftool.create_screenshot_tool", return_value=tool),
        ):
            self.run_callback(view.uploadBtn, self.interaction)
        saved = self.match.rounds.get(pk=round_snapshot.pk)
        self.assertTrue(saved.screenshot)
        self.assertEqual([player.profile_name for player in saved.steg.players], [seed.player.ch_name for seed in self.seeds])
        self.assertEqual(saved.winner_id, self.player_ids[0])
        self.assertFalse(view.is_uploading)
        self.wrapper.finishMatch.assert_awaited_once()

    def test_screenshot_modal_accepts_a_model_related_manager(self):
        self.finalize_fixture()
        with patch("discord.ui.modal._get_event_loop", return_value=SimpleNamespace()):
            modal = MatchScreenModal(self.match)
        self.assertEqual(modal.children[1].item.max_values, 4)

    def test_duplicate_screenshot_players_do_not_publish_evidence(self):
        self.finalize_fixture()
        view = self.build_view()
        self.wrapper.finishMatch = AsyncMock()
        round_snapshot = self.match.rounds.first()
        metadata = StegScreenshot(
            checksum=round_snapshot.chart.md5,
            game_version=self.match.tournament.config.version,
            players=[StegScreenshotPlayer(profile_name=self.seeds[0].player.ch_name, controller_type="Guitar") for index in range(2)],
        )
        screen = SimpleNamespace(filename="duplicate-players.png")
        modal = SimpleNamespace(screens=[screen], wait=AsyncMock())
        tool = SimpleNamespace(getStegInfo=AsyncMock(return_value=metadata))
        with (
            patch("corpoch.dbot.view.reftool.MatchScreenModal", return_value=modal),
            patch("corpoch.dbot.view.reftool.create_screenshot_tool", return_value=tool),
        ):
            self.run_callback(view.uploadBtn, self.interaction)
        self.assertFalse(self.match.rounds.get(pk=round_snapshot.pk).screenshot)
        self.interaction.followup.send.assert_awaited_once()
        self.assertIn("distinct match participants", self.interaction.followup.send.await_args.args[0])

    def test_existing_screenshots_allow_finish_retry_without_another_upload(self):
        self.finalize_fixture()
        self.match.rounds.update(screenshot="already-uploaded.png")
        self.wrapper.finishMatch = AsyncMock()
        self.run_callback(self.build_view().uploadBtn, self.interaction)
        self.wrapper.finishMatch.assert_awaited_once()
        self.interaction.response.send_modal.assert_not_awaited()

    def test_review_buttons_keep_their_original_binding_when_list_changes(self):
        self.finalize_fixture()
        first = SimpleNamespace(fix=AsyncMock(return_value=self.match))
        second = SimpleNamespace(fix=AsyncMock(return_value=self.match))
        self.wrapper.screen_review = [first, second]
        self.wrapper.finishMatch = AsyncMock()
        view = self.build_view()
        self.run_callback(view.init)
        controls = [control for control in view.children if control.custom_id.startswith("reviewBtn_")]
        self.assertEqual(len(controls), 2)
        self.assertIsNot(controls[0], controls[1])
        self.interaction.custom_id = "reviewBtn_0"
        self.run_callback(view.reviewBtn, self.interaction)
        self.run_callback(view.reviewBtn, self.interaction)
        first.fix.assert_awaited_once()
        second.fix.assert_not_awaited()
        self.interaction.custom_id = "reviewBtn_1"
        self.run_callback(view.reviewBtn, self.interaction)
        second.fix.assert_awaited_once()
        self.assertFalse(self.wrapper.screen_review)

    def test_screenshot_decode_cannot_publish_after_match_undo(self):
        self.finalize_fixture()
        view = self.build_view()
        self.wrapper.finishMatch = AsyncMock()
        round_snapshot = self.match.rounds.first()
        metadata = StegScreenshot(
            checksum=round_snapshot.chart.md5,
            game_version=self.match.tournament.config.version,
            players=[StegScreenshotPlayer(profile_name=seed.player.ch_name, controller_type="Guitar") for seed in self.seeds],
        )

        def decode_after_undo(screen):
            self.match = undo_match_action(self.match.pk, expected_state=get_match_state_token(self.match))
            return metadata

        path = Path(os.environ["CORPO_VIEWER_TEST_DIRECTORY"]) / "delayed-decode.png"
        path.write_bytes(b"fixture-image")
        screen = SimpleNamespace(filename="delayed-upload.png")
        modal = SimpleNamespace(screens=[screen], wait=AsyncMock())
        tool = SimpleNamespace(img_path=path, getStegInfo=AsyncMock(side_effect=decode_after_undo))
        with (
            patch("corpoch.dbot.view.reftool.MatchScreenModal", return_value=modal),
            patch("corpoch.dbot.view.reftool.create_screenshot_tool", return_value=tool),
        ):
            self.run_callback(view.uploadBtn, self.interaction)
        self.assertFalse(self.match.rounds.get(pk=round_snapshot.pk).screenshot)
        self.assertFalse(self.match.complete)
        self.assertFalse(view.is_uploading)
        self.interaction.followup.send.assert_awaited_once()

    def test_finishing_without_all_evidence_leaves_recorded_match_complete(self):
        self.finalize_fixture()
        self.run_callback(self.wrapper.finishMatch, self.interaction)
        self.match.refresh_from_db()
        self.assertTrue(self.match.complete)
        self.assertFalse(self.match.finished)
        self.assertFalse(self.match.submitted)
        self.wrapper.showTool.assert_awaited_once()
