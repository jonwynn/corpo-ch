"""Checks owned staging fixture writes using isolated real Django models."""

from unittest.mock import patch

from django.test import TestCase

from corpoch.dbot.models import Guilds, Roles
from corpoch.match_actions import MatchActionError, StaleMatchAction, get_match_state_token
from corpoch.match_viewer import build_match_presentation
from corpoch.match_viewer_reader import read_match_snapshot
from corpoch.models import Chart, CHIcon, DiscordToken, DiscordUser, Match, Tournament
from staging.configuration import StagingConfigurationError
from staging.viewer_fixture import advance_fixture, prepare_fixture, revoke_fixture_access, validate_fixture


class StagingViewerFixtureTests(TestCase):
    """Exercises fixture ownership, rollback and the real match action boundary."""

    def setUp(self):
        self.account = DiscordUser.objects.create(id=710, is_active=True)
        DiscordToken.objects.create(
            user=self.account, access_token="isolated-fixture-access",
            refresh_token="isolated-fixture-refresh",
        )
        self.guild_id = 510
        self.roles = [{"id": 610, "name": "DEV Referee"}, {"id": 611, "name": "DEV Collaborator"}]

    def prepare(self):
        return prepare_fixture(self.guild_id, self.account.pk, self.roles)

    def action(self, action):
        match = validate_fixture()
        return advance_fixture(action, get_match_state_token(match))

    def test_preparation_creates_first_to_four_with_bans_and_pending_round_only(self):
        self.assertEqual(self.prepare(), "local-viewer-pilot")
        match = validate_fixture()
        presentation = build_match_presentation(read_match_snapshot(self.account.pk, match.pk))
        self.assertEqual(presentation["target"], 4)
        self.assertEqual(presentation["wins"], [0, 0])
        self.assertEqual([seed.player.ch_name for seed in match.players.all()], ["Blue Player", "Coral Player"])
        self.assertEqual(match.group.bracket.setlist.count(), 11)
        self.assertEqual(match.match_bans.count(), 4)
        self.assertEqual(match.match_rounds.count(), 1)
        self.assertIsNone(match.current_round.chart_id)
        self.assertIsNone(match.current_round.winner_id)
        self.assertFalse(match.complete or match.finished or match.submitted)
        self.assertEqual(Guilds.objects.get(pk=self.guild_id).ref_role_id, 610)
        self.assertEqual(
            list(Guilds.objects.get(pk=self.guild_id).additional_ref_roles.values_list("pk", flat=True)), [611],
        )
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff or self.account.is_superuser)
        self.assertFalse(self.account.guilds_admin.exists())
        self.assertEqual(DiscordUser.objects.count(), 1)

    def test_preparation_requires_existing_active_oauth_account(self):
        for account_id in (999, self.account.pk):
            if account_id == self.account.pk:
                DiscordUser.objects.filter(pk=account_id).update(is_active=False)
            with self.subTest(account_id=account_id), self.assertRaises(StagingConfigurationError):
                prepare_fixture(self.guild_id, account_id, self.roles)
        DiscordUser.objects.filter(pk=self.account.pk).update(is_active=True)
        DiscordToken.objects.all().delete()
        with self.assertRaises(StagingConfigurationError):
            self.prepare()
        self.assertFalse(Guilds.objects.exists())
        self.assertFalse(Tournament.objects.exists())

    def test_preparation_refuses_existing_tournament_without_modifying_memberships(self):
        tournament = Tournament(name="Existing event")
        tournament.save()
        with self.assertRaises(StagingConfigurationError):
            self.prepare()
        self.assertEqual(Tournament.objects.get().name, "Existing event")
        self.assertFalse(Guilds.objects.exists())

    def test_preparation_rolls_back_entire_graph_and_grants_after_service_failure(self):
        with patch("staging.viewer_fixture.record_opening_action", side_effect=RuntimeError("isolated failure")):
            with self.assertRaises(RuntimeError):
                self.prepare()
        for model in (Tournament, Match, Chart, CHIcon, Guilds, Roles):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertEqual(DiscordUser.objects.count(), 1)
        self.assertEqual(DiscordToken.objects.count(), 1)

    def test_foreign_role_collision_does_not_change_either_guild(self):
        other = Guilds.objects.create(id=511, name="Other DEV guild")
        Roles.objects.create(id=self.roles[0]["id"], guild=other, name="Existing role")
        with self.assertRaises(StagingConfigurationError):
            self.prepare()
        self.assertEqual(Roles.objects.get(pk=610).guild_id, other.pk)
        self.assertEqual(Roles.objects.get(pk=610).name, "Existing role")
        self.assertFalse(Guilds.objects.filter(pk=self.guild_id).exists())

    def test_repeat_preserves_play_and_verified_repeat_restores_only_fixture_access(self):
        self.prepare()
        self.action("pick")
        self.action("win-p1")
        before = get_match_state_token(validate_fixture())
        self.assertEqual(self.prepare(), "local-viewer-pilot")
        self.assertEqual(get_match_state_token(validate_fixture()), before)
        guild = Guilds.objects.get(pk=self.guild_id)
        guild.referees.remove(self.account)
        with self.assertRaises(StagingConfigurationError):
            validate_fixture()
        self.assertEqual(validate_fixture(require_access=False).referee_id, self.account.pk)
        self.prepare()
        self.assertEqual(get_match_state_token(validate_fixture()), before)
        self.assertFalse(guild.admins.exists())

    def test_changed_owner_or_graph_refuses_rerun_and_actions(self):
        self.prepare()
        token = get_match_state_token(validate_fixture())
        with self.assertRaises(StagingConfigurationError):
            prepare_fixture(999, self.account.pk, self.roles)
        Chart.objects.filter(name="Synthetic Song 05").update(name="Changed chart")
        for operation in (self.prepare, validate_fixture, lambda: advance_fixture("pick", token)):
            with self.subTest(operation=operation), self.assertRaises(StagingConfigurationError):
                operation()
        self.assertEqual(Match.objects.get().match_rounds.count(), 1)
        self.assertIsNone(Match.objects.get().current_round.chart_id)

    def test_stale_action_and_revoked_account_cannot_mutate_fixture(self):
        self.prepare()
        token = get_match_state_token(validate_fixture())
        advance_fixture("pick", token)
        with self.assertRaises(StaleMatchAction):
            advance_fixture("win-p1", token)
        token = get_match_state_token(validate_fixture())
        Guilds.objects.get(pk=self.guild_id).referees.remove(self.account)
        with self.assertRaises(StagingConfigurationError):
            advance_fixture("win-p1", token)
        self.assertIsNone(Match.objects.get().current_round.winner_id)

    def test_live_progression_uses_loser_picks_and_preserves_pending_states(self):
        self.prepare()
        match = self.action("pick")
        high, low = list(match.players.all())
        self.assertEqual(match.current_round.picked_id, high.player_id)
        match = self.action("win-p1")
        self.assertIsNone(match.current_round.chart_id)
        self.assertEqual(match.current_round.picked_id, low.player_id)
        match = self.action("pick")
        self.assertEqual(match.current_round.picked_id, low.player_id)
        match = self.action("win-p2")
        presentation = build_match_presentation(read_match_snapshot(self.account.pk, match.pk))
        self.assertEqual(presentation["wins"], [1, 1])
        self.assertEqual(match.current_round.picked_id, high.player_id)

    def test_finalization_and_undo_never_publish_or_remove_initial_bans(self):
        self.prepare()
        with self.assertRaises(MatchActionError):
            self.action("undo")
        for unused_round in range(4):
            self.action("pick")
            match = self.action("win-p1")
        self.assertFalse(match.complete)
        match = self.action("finalize")
        self.assertTrue(match.complete)
        self.assertFalse(match.finished or match.submitted)
        match = self.action("undo")
        self.assertFalse(match.complete)
        self.assertEqual(match.match_bans.count(), 4)
        self.assertEqual(match.match_rounds.count(), 4)
        self.assertFalse(match.match_rounds.exclude(screenshot="").exclude(screenshot__isnull=True).exists())

    def test_invalid_role_inputs_leave_database_untouched(self):
        for roles in ([], [{"id": True, "name": "Invalid"}], [self.roles[0], self.roles[0]]):
            with self.subTest(roles=roles), self.assertRaises(StagingConfigurationError):
                prepare_fixture(self.guild_id, self.account.pk, roles)
        self.assertFalse(Tournament.objects.exists())
        self.assertFalse(Guilds.objects.exists())

    def test_undo_returns_to_initial_pending_round_without_changing_bans(self):
        self.prepare()
        self.action("pick")
        self.action("win-p1")
        match = self.action("undo")
        self.assertEqual(match.match_rounds.count(), 1)
        self.assertIsNone(match.current_round.winner_id)
        match = self.action("undo")
        self.assertIsNone(match.current_round.chart_id)
        self.assertEqual(match.match_bans.count(), 4)
        self.action("pick")
        self.action("win-p2")
        self.assertEqual(validate_fixture().score_int, [0, 1])

    def test_changed_ownership_marker_or_finalization_refuses_access(self):
        self.prepare()
        tournament = Tournament.objects.get()
        marker = tournament.config.rules
        for invalid_marker in ("not-json", "[]", "{}"):
            tournament.config.rules = invalid_marker
            tournament.config.save(update_fields=["rules"])
            with self.subTest(marker=invalid_marker), self.assertRaises(StagingConfigurationError):
                validate_fixture()
        tournament.config.rules = marker
        tournament.config.save(update_fields=["rules"])
        Match.objects.update(complete=True)
        with self.assertRaises(StagingConfigurationError):
            validate_fixture()

    def test_revocation_is_idempotent_for_inactive_owner_and_preserves_other_grants(self):
        self.prepare()
        self.action("pick")
        token = get_match_state_token(validate_fixture())
        other_account = DiscordUser.objects.create(id=711, is_active=True)
        guild = Guilds.objects.get(pk=self.guild_id)
        guild.referees.add(other_account)
        guild.admins.add(other_account)
        other_guild = Guilds.objects.create(id=511)
        other_guild.referees.add(self.account)
        DiscordUser.objects.filter(pk=self.account.pk).update(is_active=False)
        for unused_attempt in range(2):
            match = revoke_fixture_access()
            self.assertEqual(match.referee_id, self.account.pk)
            self.assertEqual(get_match_state_token(match), token)
            self.assertEqual(list(guild.referees.values_list("pk", flat=True)), [other_account.pk])
            self.assertTrue(guild.admins.filter(pk=other_account.pk).exists())
            self.assertTrue(other_guild.referees.filter(pk=self.account.pk).exists())
        self.assertEqual(validate_fixture(require_access=False).pk, "local-viewer-pilot")
