"""Checks delayed evidence and export publication against real isolated records."""

from django.test import TestCase

from corpoch.match_actions import (
    MatchActionError, StaleMatchAction, corp_picker_id, finalize_match,
    get_match_state_token, record_opening_action, record_round_winner,
    select_chart, undo_match_action,
)
from corpoch.match_publication import (
    finish_match_evidence, publish_export_status, publish_round_evidence,
)
from corpoch.models import Match, MatchRound
from corpoch.types import StegScreenshot, StegScreenshotPlayerDummy
from tests.match_fixtures import create_corp_match


class MatchPublicationTests(TestCase):
    def setUp(self):
        self.match, self.seeds, self.charts = create_corp_match()
        for index, actor in enumerate((0, 1, 1, 0)):
            self.match = record_opening_action(
                self.match.pk, self.seeds[actor].player_id, self.charts[index].pk,
                expected_state=get_match_state_token(self.match),
            )
        for chart in self.charts[4:8]:
            self.match = select_chart(
                self.match.pk, chart.pk, player_id=corp_picker_id(self.match),
                expected_state=get_match_state_token(self.match),
            )
            self.match = record_round_winner(
                self.match.pk, self.seeds[0].player_id,
                expected_state=get_match_state_token(self.match),
            )
        self.match = finalize_match(self.match.pk, expected_state=get_match_state_token(self.match))
        self.metadata = StegScreenshot(players=[
            StegScreenshotPlayerDummy(profile_name="Higher Seed", error_reason="Fixture"),
            StegScreenshotPlayerDummy(profile_name="Lower Seed", error_reason="Fixture"),
        ])

    def publish(self, round_record, **kwargs):
        return publish_round_evidence(
            self.match.pk, round_record.pk, round_record.chart_id,
            f"fixture/{round_record.pk}.png", self.metadata, **kwargs,
        )

    def test_publication_changes_only_evidence_and_preserves_sporting_state(self):
        round_record = self.match.match_rounds.first()
        before = get_match_state_token(self.match)
        self.match = self.publish(round_record, expected_state=before)
        self.assertEqual(get_match_state_token(self.match), before)
        round_record.refresh_from_db()
        self.assertEqual(round_record.winner_id, self.seeds[0].player_id)
        self.assertEqual(round_record.selection_kind, "player")
        self.assertEqual(len(round_record.steg.players), 2)
        self.assertTrue(self.match.complete)
        self.assertFalse(self.match.finished)

    def test_sibling_uploads_accept_same_sporting_token(self):
        token = get_match_state_token(self.match)
        for round_record in self.match.match_rounds.all():
            self.publish(round_record, expected_state=token)
        result = finish_match_evidence(self.match.pk)
        self.assertTrue(result.finished)
        self.assertFalse(result.submitted)

    def test_missing_metadata_or_incomplete_rounds_cannot_finish_evidence(self):
        with self.assertRaises(MatchActionError):
            finish_match_evidence(self.match.pk)
        MatchRound.objects.filter(match=self.match).update(screenshot="fixture/reference.png", steg=None)
        with self.assertRaises(MatchActionError):
            finish_match_evidence(self.match.pk)
        incomplete = StegScreenshot(players=[self.metadata.players[0]])
        MatchRound.objects.filter(match=self.match).update(steg=incomplete)
        with self.assertRaises(MatchActionError):
            finish_match_evidence(self.match.pk)

    def test_changed_chart_deleted_round_and_reopened_match_reject_upload(self):
        round_record = self.match.match_rounds.first()
        token = get_match_state_token(self.match)
        MatchRound.objects.filter(pk=round_record.pk).update(chart=self.charts[-1])
        with self.assertRaises(StaleMatchAction):
            self.publish(round_record, expected_state=token)
        with self.assertRaises(MatchActionError):
            self.publish(round_record)
        MatchRound.objects.filter(pk=round_record.pk).delete()
        with self.assertRaises(MatchActionError):
            self.publish(round_record)
        Match.objects.filter(pk=self.match.pk).update(complete=False)
        with self.assertRaises(MatchActionError):
            self.publish(self.match.match_rounds.first())

    def test_delayed_second_upload_cannot_replace_newer_file(self):
        round_record = self.match.match_rounds.first()
        self.publish(round_record)
        with self.assertRaises(MatchActionError):
            self.publish(round_record, expected_screenshot="")
        round_record.refresh_from_db()
        self.publish(round_record, expected_screenshot=str(round_record.screenshot))

    def test_export_flag_does_not_restore_stale_match_fields(self):
        for round_record in self.match.match_rounds.all():
            self.publish(round_record)
        self.match = finish_match_evidence(self.match.pk)
        token = get_match_state_token(self.match)
        self.match = undo_match_action(self.match.pk, expected_state=token)
        with self.assertRaises(StaleMatchAction):
            publish_export_status(self.match.pk, expected_state=token)
        self.match.refresh_from_db()
        self.assertFalse(self.match.complete)
        self.assertFalse(self.match.submitted)

    def test_explicit_export_reset_does_not_change_result_or_evidence(self):
        Match.objects.filter(pk=self.match.pk).update(finished=True, submitted=True)
        self.match.refresh_from_db()
        before = get_match_state_token(self.match)
        publish_export_status(self.match.pk, submitted=False)
        self.match.refresh_from_db()
        self.assertTrue(self.match.complete)
        self.assertTrue(self.match.finished)
        self.assertFalse(self.match.submitted)
        self.assertEqual(get_match_state_token(self.match), before)
