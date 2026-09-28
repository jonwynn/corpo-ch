"""Database-backed CORP transitions, compatibility and stale-action checks."""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils.connection import ConnectionDoesNotExist

from corpoch.match_actions import (
    MatchActionError, StaleMatchAction, assign_match_players, cancel_match,
    finalize_match, get_match_state_token,
    load_corp_context, record_opening_action, record_round_winner,
    select_chart, undo_match_action,
)
from corpoch.models import (
    BracketRules, ExhibitionMatchBan, ExhibitionMatchRound, Match, MatchBan, MatchRound,
)
from corpoch.types import StegScreenshot
from tests.match_fixtures import create_corp_match


class MatchActionTests(TestCase):
    def setUp(self):
        self.match, self.seeds, self.charts = create_corp_match()
        self.players = tuple(seed.player_id for seed in self.seeds)

    def token(self):
        self.match.refresh_from_db()
        return get_match_state_token(self.match)

    def opening(self, save_first=False, save_second=False):
        for actor, chart, saved in (
            (self.players[0], self.charts[0], False),
            (self.players[1], self.charts[0 if save_first else 1], save_first),
            (self.players[1], self.charts[2], False),
            (self.players[0], self.charts[2 if save_second else 3], save_second),
        ):
            self.match = record_opening_action(
                self.match.pk, actor, chart.pk, saved=saved, expected_state=self.token(),
            )

    def play(self, winner):
        context = load_corp_context(self.match)
        current = self.match.current_round
        if current.chart_id is None:
            self.match = select_chart(
                self.match.pk, context.selection.eligible_chart_ids[0],
                player_id=context.selection.next_picker_id, expected_state=self.token(),
            )
        self.match = record_round_winner(self.match.pk, winner, expected_state=self.token())

    def test_four_opening_actions_and_saved_flag_publish_with_first_round(self):
        self.opening(True, True)
        self.assertEqual(self.match.match_bans.count(), 4)
        self.assertEqual(self.match.effective_bans.count(), 0)
        self.assertEqual(self.match.match_rounds.count(), 1)
        self.assertIsNone(self.match.current_round.chart_id)
        self.assertEqual(self.match.picking_player.pk, self.players[0])
        self.assertEqual(set(self.match.match_bans.values_list("action_phase", flat=True)), {"opening"})
        self.assertEqual(self.match.current_round.selection_kind, "unknown")

    def test_model_action_delegates_refresh_the_revision_for_subsequent_calls(self):
        self.match.add_ban(self.seeds[0].player, self.charts[0])
        self.match.add_save(self.seeds[1].player, self.charts[0])
        self.match.add_ban(self.seeds[1].player, self.charts[2])
        self.match.add_save(self.seeds[0].player, self.charts[2])
        self.assertEqual(self.match.action_revision, 4)
        self.assertEqual(self.match.rounds.count(), 1)
        self.match.add_round()
        self.assertEqual(self.match.rounds.count(), 1)
        self.assertEqual(self.match.action_revision, 4)

    def test_invalid_opening_action_and_stale_duplicate_leave_no_partial_write(self):
        token = self.token()
        with self.assertRaises(MatchActionError):
            record_opening_action(self.match.pk, self.players[1], self.charts[0].pk, expected_state=token)
        self.match = record_opening_action(self.match.pk, self.players[0], self.charts[0].pk, expected_state=token)
        with self.assertRaises(StaleMatchAction):
            record_opening_action(self.match.pk, self.players[1], self.charts[1].pk, expected_state=token)
        with self.assertRaises(MatchActionError):
            record_opening_action(self.match.pk, self.players[1], self.charts[1].pk, saved=True, expected_state=self.token())
        self.assertEqual(self.match.match_bans.count(), 1)

    def test_back_before_bans_clears_assignment_and_reassignment_is_atomic(self):
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(self.match.players.exists())
        token = self.token()
        with self.assertRaises(MatchActionError):
            assign_match_players(self.match.pk, [self.seeds[0].pk] * 2, expected_state=token)
        self.assertFalse(self.match.players.exists())
        self.match = assign_match_players(
            self.match.pk, [seed.pk for seed in self.seeds], expected_state=token,
        )
        self.assertEqual(self.match.players.count(), 2)
        self.opening()
        with self.assertRaises(MatchActionError):
            assign_match_players(
                self.match.pk, [seed.pk for seed in self.seeds], expected_state=self.token(),
            )

    def test_cancel_checks_token_and_deleted_matches_report_stale(self):
        old_token = self.token()
        self.match = record_opening_action(
            self.match.pk, self.players[0], self.charts[0].pk, expected_state=old_token,
        )
        with self.assertRaises(StaleMatchAction):
            cancel_match(self.match.pk, expected_state=old_token)
        token = self.token()
        cancel_match(self.match.pk, expected_state=token)
        self.assertFalse(Match.objects.filter(pk=self.match.pk).exists())
        with self.assertRaises(StaleMatchAction):
            cancel_match(self.match.pk, expected_state=token)

    def test_saved_chart_cannot_be_rebanned_and_no_tiebreaker_ban_is_allowed(self):
        self.opening(True, True)
        with self.assertRaises(MatchActionError):
            record_opening_action(self.match.pk, self.players[0], self.charts[4].pk, expected_state=self.token())
        self.assertEqual(self.match.match_bans.count(), 4)

    def test_last_opening_failure_rolls_back_ban_and_first_round(self):
        for actor, chart in ((self.players[0], self.charts[0]), (self.players[1], self.charts[1]), (self.players[1], self.charts[2])):
            self.match = record_opening_action(self.match.pk, actor, chart.pk, expected_state=self.token())
        with patch("corpoch.match_actions.create_pending_round", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                record_opening_action(self.match.pk, self.players[0], self.charts[3].pk, expected_state=self.token())
        self.assertEqual(self.match.match_bans.count(), 3)
        self.assertFalse(self.match.match_rounds.exists())

    def test_chart_picker_result_and_next_round_agree_with_loser_picks(self):
        self.opening()
        self.play(self.players[1])
        first, second = self.match.rounds.all()
        self.assertEqual((first.picked_id, first.selection_kind), (self.players[0], "player"))
        self.assertEqual((first.winner_id, first.loser_id), (self.players[1], self.players[0]))
        self.assertEqual(second.picked_id, self.players[0])
        self.assertEqual(self.match.picking_player.pk, self.players[0])
        self.assertEqual(self.match.score_int, [0, 1])

    def test_wrong_picker_and_duplicate_result_do_not_advance_rounds(self):
        self.opening()
        with self.assertRaises(MatchActionError):
            select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[1], expected_state=self.token())
        self.match = select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[0], expected_state=self.token())
        token = self.token()
        self.match = record_round_winner(self.match.pk, self.players[0], expected_state=token)
        with self.assertRaises(StaleMatchAction):
            record_round_winner(self.match.pk, self.players[0], expected_state=token)
        self.assertEqual(self.match.match_rounds.count(), 2)

    def test_round_selection_undo_and_reselection_rejects_older_identical_state(self):
        self.opening()
        self.match = select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[0], expected_state=self.token())
        earlier_token = self.token()
        earlier_revision = self.match.action_revision
        self.match = undo_match_action(self.match.pk, expected_state=earlier_token)
        self.match = select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[0], expected_state=self.token())
        self.assertGreater(self.match.action_revision, earlier_revision)
        with self.assertRaises(StaleMatchAction):
            record_round_winner(self.match.pk, self.players[0], expected_state=earlier_token)
        self.assertIsNone(self.match.current_round.winner_id)

    def test_four_bans_force_neutral_tiebreaker_and_undo_restores_prior_result(self):
        self.opening()
        for winner in self.players * 3:
            self.play(winner)
        current = self.match.current_round
        self.assertEqual(current.num, 7)
        self.assertEqual(current.selection_kind, "automatic")
        self.assertIsNone(current.picked_id)
        self.assertIsNotNone(current.chart_id)
        self.assertIsNone(self.match.picking_player)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertEqual(self.match.rounds.count(), 6)
        self.assertIsNone(self.match.current_round.winner_id)
        self.assertEqual(self.match.score_int, [3, 2])

    def test_two_unplayed_saves_are_withheld_from_tiebreaker_selection(self):
        self.opening(True, True)
        for index, winner in enumerate(self.players * 3):
            self.match = select_chart(
                self.match.pk, self.charts[index + 4].pk,
                player_id=self.match.picking_player.pk, expected_state=self.token(),
            )
            self.match = record_round_winner(self.match.pk, winner, expected_state=self.token())
        candidates = set(self.match.setlist_remaining.values_list("pk", flat=True))
        self.assertEqual(len(candidates), 3)
        self.assertNotIn(self.charts[0].pk, candidates)
        self.assertNotIn(self.charts[2].pk, candidates)
        self.assertEqual(self.match.picking_player.pk, self.players[0])

    def test_undo_rebuilds_an_invalid_blank_forced_tiebreaker(self):
        self.opening()
        for winner in self.players * 3:
            self.play(winner)
        broken = self.match.current_round
        broken_id, forced_chart_id = broken.pk, broken.chart_id
        self.match.rounds.filter(pk=broken_id).update(chart=None, selection_kind="unknown")
        with self.assertRaises(MatchActionError):
            load_corp_context(self.match)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        restored = self.match.current_round
        self.assertNotEqual(restored.pk, broken_id)
        self.assertEqual((restored.num, restored.chart_id, restored.selection_kind), (7, forced_chart_id, "automatic"))
        self.assertIsNone(restored.picked_id)
        self.assertEqual(self.match.score_int, [3, 3])
        self.match = record_round_winner(self.match.pk, self.players[0], expected_state=self.token())
        self.assertEqual(self.match.score_int, [4, 3])

    def test_finalize_waits_for_target_and_does_not_claim_screenshots_or_export(self):
        self.opening()
        with self.assertRaises(MatchActionError):
            finalize_match(self.match.pk, expected_state=self.token())
        for index in range(4):
            self.play(self.players[0])
        self.assertFalse(self.match.complete)
        self.assertEqual(self.match.rounds.count(), 4)
        self.match = finalize_match(self.match.pk, expected_state=self.token())
        self.assertTrue(self.match.complete)
        self.assertEqual(self.match.winner_id, self.players[0])
        self.assertFalse(self.match.finished)
        self.assertFalse(self.match.submitted)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(self.match.complete)
        self.assertEqual(self.match.score_int, [4, 0])

    def test_result_undo_preserves_surviving_evidence_and_historical_export_flag(self):
        self.opening()
        for index in range(4):
            self.play(self.players[0])
        self.match = finalize_match(self.match.pk, expected_state=self.token())
        current_id = self.match.current_round.pk
        evidence = StegScreenshot(players=[])
        MatchRound.objects.filter(pk=current_id).update(screenshot="retained.png", steg=evidence)
        Match.objects.filter(pk=self.match.pk).update(finished=True, submitted=True)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertTrue(self.match.submitted)
        self.assertFalse(self.match.finished)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        current = self.match.current_round
        self.assertIsNone(current.winner_id)
        self.assertEqual(current.screenshot.name, "retained.png")
        self.assertEqual(current.steg, evidence)
        self.assertTrue(self.match.submitted)
        self.match = record_round_winner(self.match.pk, self.players[0], expected_state=self.token())
        self.match = finalize_match(self.match.pk, expected_state=self.token())
        self.assertTrue(self.match.submitted)
        self.assertEqual(self.match.current_round.screenshot.name, "retained.png")

    def test_undo_selection_clears_personal_provenance_and_undo_first_round_restores_opening(self):
        self.opening()
        self.match = select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[0], expected_state=self.token())
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertIsNone(self.match.current_round.chart_id)
        self.assertIsNone(self.match.current_round.picked_id)
        self.assertEqual(self.match.current_round.selection_kind, "unknown")
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(self.match.rounds.exists())
        self.assertEqual(self.match.bans.count(), 3)

    def test_setup_chart_and_player_changes_invalidate_a_rendered_token(self):
        changes = (
            lambda: self.seeds[0].__class__.objects.filter(pk=self.seeds[0].pk).update(seed=8),
            lambda: self.charts[0].__class__.objects.filter(pk=self.charts[0].pk).update(speed=110),
            lambda: BracketRules.objects.filter(pk=self.match.group.bracket_id).update(num_bans=3),
        )
        for change in changes:
            token = self.token()
            change()
            with self.assertRaises(StaleMatchAction):
                record_opening_action(self.match.pk, self.players[0], self.charts[0].pk, expected_state=token)

    def test_profile_rejects_unsupported_setup_and_unknown_history(self):
        self.match.rev_seeds = True
        with self.assertRaises(MatchActionError):
            load_corp_context(self.match)

    def test_undo_discards_unverified_latest_selection_without_inventing_provenance(self):
        self.opening()
        self.match = select_chart(self.match.pk, self.charts[4].pk, player_id=self.players[0], expected_state=self.token())
        old_id = self.match.current_round.pk
        stale_token = self.token()
        MatchRound.objects.filter(pk=old_id).update(selection_kind="unknown")
        Match.objects.filter(pk=self.match.pk).update(submitted=True)
        with self.assertRaises(StaleMatchAction):
            undo_match_action(self.match.pk, expected_state=stale_token)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(MatchRound.objects.filter(pk=old_id).exists())
        self.assertIsNone(self.match.current_round.chart_id)
        self.assertEqual(self.match.current_round.selection_kind, "unknown")
        self.assertTrue(self.match.submitted)
        self.assertEqual(load_corp_context(self.match).selection.next_picker_id, self.players[0])

    def test_undo_discards_unverified_last_opening_action_and_preserves_prior_history(self):
        for actor, chart in ((self.players[0], self.charts[0]), (self.players[1], self.charts[1])):
            self.match = record_opening_action(self.match.pk, actor, chart.pk, expected_state=self.token())
        last = self.match.match_bans.last()
        MatchBan.objects.filter(pk=last.pk).update(action_phase="unknown")
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertEqual(self.match.match_bans.count(), 1)
        self.assertEqual(self.match.match_bans.first().action_phase, "opening")
        self.assertEqual(len(load_corp_context(self.match).actions), 1)

    def test_completed_unverified_match_reopens_before_any_record_is_removed(self):
        self.opening()
        for index in range(4):
            self.play(self.players[0])
        self.match = finalize_match(self.match.pk, expected_state=self.token())
        last_id = self.match.current_round.pk
        MatchRound.objects.filter(pk=last_id).update(selection_kind="unknown", screenshot="surviving.png")
        Match.objects.filter(pk=self.match.pk).update(submitted=True)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(self.match.complete)
        self.assertEqual(self.match.score_int, [4, 0])
        self.assertEqual(self.match.current_round.pk, last_id)
        self.assertEqual(self.match.current_round.screenshot.name, "surviving.png")
        self.assertTrue(self.match.submitted)
        self.match = undo_match_action(self.match.pk, expected_state=self.token())
        self.assertFalse(MatchRound.objects.filter(pk=last_id).exists())
        self.assertEqual(self.match.score_int, [3, 0])
        self.assertIsNone(self.match.current_round.chart_id)

    def test_undo_recovery_cannot_discard_history_under_invalid_profile(self):
        self.opening()
        MatchRound.objects.filter(match=self.match).update(selection_kind="player")
        Match.objects.filter(pk=self.match.pk).update(rev_seeds=True)
        with self.assertRaises(MatchActionError):
            undo_match_action(self.match.pk, expected_state=self.token())
        self.assertEqual(self.match.match_rounds.count(), 1)
        self.assertEqual(self.match.match_bans.count(), 4)
        self.match.rev_seeds = False
        MatchBan.objects.create(match=self.match, num=0, player_id=self.players[0], chart=self.charts[0])
        with self.assertRaises(MatchActionError):
            load_corp_context(self.match)

    def test_scoped_saves_preserve_unrelated_fields_and_do_not_decode_screenshots(self):
        self.opening()
        current = self.match.current_round
        MatchRound.objects.filter(pk=current.pk).update(screenshot="fixture.png")
        current.refresh_from_db()
        current.picked_id = self.players[1]
        current.save(update_fields=["picked"], using="default")
        self.assertEqual(MatchRound.objects.get(pk=current.pk).screenshot.name, "fixture.png")
        stale = Match.objects.get(pk=self.match.pk)
        Match.objects.filter(pk=self.match.pk).update(complete=True)
        stale.message = 123
        stale.save(update_fields=["message"], using="default")
        self.assertTrue(Match.objects.get(pk=self.match.pk).complete)
        for record in (self.match, current):
            with self.subTest(record=type(record).__name__), self.assertRaises(ConnectionDoesNotExist):
                record.save(using="missing-test-alias", update_fields=["message"] if isinstance(record, Match) else ["picked"])

    def test_get_score_never_credits_an_outsider_to_second_player(self):
        MatchRound.objects.create(match=self.match, num=1, winner_id=self.players[0])
        other_match, seeds, charts = create_corp_match(match_id="fixture-other-match")
        MatchRound.objects.create(match=self.match, num=2, winner_id=seeds[0].player_id)
        self.assertEqual(self.match.score_int, [1, 0])
        self.assertEqual(self.match.score, "1 - 0")
        self.assertEqual(self.match.high_seed_score, 1)
        self.assertEqual(self.match.low_seed_score, 0)

    def test_provenance_is_additive_and_existing_round_serializer_shape_is_frozen(self):
        from corpoch.api.serializers import MatchRoundSerializer, MatchSerializer, MatchSerializerLight

        self.assertEqual(MatchBan().action_phase, "unknown")
        self.assertEqual(MatchRound().selection_kind, "unknown")
        self.assertFalse(hasattr(ExhibitionMatchBan(), "action_phase"))
        self.assertFalse(hasattr(ExhibitionMatchRound(), "selection_kind"))
        self.assertEqual(set(MatchRoundSerializer().fields), {
            "id", "num", "steg", "screenshot", "created", "chart", "match", "picked", "winner", "loser",
        })
        self.assertEqual(set(MatchSerializer(self.match).data), {
            "id", "players", "match_rounds", "match_bans", "group", "winner", "loser", "referee",
            "defer", "started_on", "ended_on", "complete", "finished", "submitted", "rev_seeds",
        })
        self.assertEqual(set(MatchSerializerLight(self.match).data), {
            "id", "players", "winner", "loser", "defer",
        })

    def test_corp_rules_validation_and_legacy_tb_configuration_remain_distinct(self):
        rules = self.match.ruleset
        rules.full_clean()
        self.assertTrue(rules.pickable_tb)
        self.assertFalse(rules.bannable_tb)
        rules.pick_ruleset = "alternate"
        with self.assertRaises(ValidationError):
            rules.full_clean()
        rules.tb_ruleset = "bansave"
        self.assertTrue(rules.bannable_tb)

    def test_playoff_target_requires_five_recorded_wins(self):
        self.match, self.seeds, self.charts = create_corp_match(9, "fixture-playoff-match")
        self.players = tuple(seed.player_id for seed in self.seeds)
        self.opening()
        for index in range(4):
            self.play(self.players[0])
        self.assertFalse(load_corp_context(self.match).selection.complete)
        self.play(self.players[0])
        self.assertTrue(load_corp_context(self.match).selection.complete)
        self.assertEqual(self.match.score_int, [5, 0])


class MatchProvenanceMigrationTests(TransactionTestCase):
    def test_existing_rows_keep_unknown_provenance_and_zero_initial_revision(self):
        previous = ("corpoch", "0029_alter_discordtoken_options_alter_discorduser_options_and_more")
        current = ("corpoch", "0030_match_provenance_corp_cup")
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            historical = executor.loader.project_state([previous]).apps
            tournament = historical.get_model("corpoch", "Tournament").objects.create(name="Historical fixture")
            bracket = historical.get_model("corpoch", "Bracket").objects.create(tournament=tournament)
            group = historical.get_model("corpoch", "Group").objects.create(bracket=bracket)
            match = historical.get_model("corpoch", "Match").objects.create(id="historical-fixture", group=group)
            ban = historical.get_model("corpoch", "MatchBan").objects.create(match=match, num=0)
            round_record = historical.get_model("corpoch", "MatchRound").objects.create(match=match, num=1)
        finally:
            MigrationExecutor(connection).migrate([current])
        self.assertEqual(Match.objects.get(pk=match.pk).action_revision, 0)
        self.assertEqual(MatchBan.objects.get(pk=ban.pk).action_phase, "unknown")
        self.assertEqual(MatchRound.objects.get(pk=round_record.pk).selection_kind, "unknown")
