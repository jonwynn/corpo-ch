"""Checks database-only Discord controls for the owned local sample match."""

import json

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from corpoch.dbot.models import Channels, Guilds
from corpoch.match_actions import MatchActionError, StaleMatchAction, get_match_state_token
from corpoch.models import Chart, DiscordToken, DiscordUser, Match
from staging.configuration import StagingConfigurationError
from staging.discord_fixture import (
    apply_control_action, build_control_snapshot, grant_shared_referee, revoke_shared_referee,
)
from staging.viewer_fixture import prepare_fixture, validate_fixture


class StagingDiscordFixtureTests(TestCase):
    """Exercises explicit Discord controls without provider or bot connections."""

    def setUp(self):
        self.account = DiscordUser.objects.create(id=710, is_active=True)
        DiscordToken.objects.create(
            user=self.account, access_token="isolated-fixture-access",
            refresh_token="isolated-fixture-refresh",
        )
        self.guild_id = 510
        self.roles = [
            {"id": 610, "name": "DEV Referee"},
            {"id": 611, "name": "DEV Collaborator"},
        ]
        prepare_fixture(self.guild_id, self.account.pk, self.roles)

    def snapshot(self):
        return build_control_snapshot(self.account.pk, self.guild_id)

    def action(self, action, **choices):
        snapshot = self.snapshot()
        return apply_control_action(
            action, snapshot["token"], self.account.pk, self.guild_id, **choices,
        )

    def pick(self):
        snapshot = self.snapshot()
        return self.action(
            "pick", chart_id=snapshot["charts"][0]["id"],
            player_id=snapshot["next_picker_id"],
        )

    def assert_action_rejected(self, action, **overrides):
        snapshot = self.snapshot()
        arguments = {
            "action": action, "expected_state": snapshot["token"],
            "account_id": self.account.pk, "guild_id": self.guild_id,
        }
        arguments.update(overrides)
        with self.assertRaises((MatchActionError, StagingConfigurationError)):
            apply_control_action(**arguments)
        self.assertEqual(get_match_state_token(validate_fixture()), snapshot["token"])

    def test_snapshot_has_plain_explicit_data_without_database_writes(self):
        token = get_match_state_token(validate_fixture())
        with CaptureQueriesContext(connection) as queries:
            snapshot = self.snapshot()
        mutations = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        ]
        self.assertEqual(mutations, [])
        self.assertEqual(snapshot["match_id"], "local-viewer-pilot")
        self.assertEqual(snapshot["token"], token)
        self.assertEqual(snapshot["account_id"], self.account.pk)
        self.assertEqual(snapshot["guild_id"], self.guild_id)
        self.assertEqual(tuple(snapshot["role_ids"]), (610, 611))
        self.assertIsInstance(snapshot["players"], tuple)
        self.assertEqual(
            [(player["name"], player["seed"]) for player in snapshot["players"]],
            [("Blue Player", 1), ("Coral Player", 2)],
        )
        self.assertEqual(snapshot["next_picker_id"], snapshot["players"][0]["id"])
        self.assertEqual(snapshot["round_number"], 1)
        self.assertIsNone(snapshot["current_chart"])
        self.assertEqual(snapshot["wins"], (0, 0))
        self.assertEqual(snapshot["target"], 4)
        self.assertFalse(snapshot["complete"] or snapshot["can_finalize"] or snapshot["can_undo"])
        self.assertEqual(
            [chart["name"] for chart in snapshot["charts"]],
            [f"Synthetic Song {number:02d}" for number in range(5, 12)],
        )
        self.assertTrue(all(isinstance(chart["description"], str) for chart in snapshot["charts"]))
        self.assertEqual(json.loads(json.dumps(snapshot))["match_id"], snapshot["match_id"])
        self.assertEqual(build_control_snapshot(), snapshot)

    def test_explicit_pick_and_winner_use_loser_picks_without_awarding_pick_points(self):
        initial = self.snapshot()
        selected = initial["charts"][2]
        first = self.action(
            "pick", chart_id=selected["id"], player_id=initial["players"][0]["id"],
        )
        self.assertEqual(first["current_chart"], {"id": selected["id"], "name": selected["name"]})
        self.assertEqual(first["wins"], (0, 0))
        self.assertTrue(first["can_undo"])
        after_result = self.action("winner", player_id=initial["players"][1]["id"])
        self.assertEqual(after_result["wins"], (0, 1))
        self.assertEqual(after_result["round_number"], 2)
        self.assertIsNone(after_result["current_chart"])
        self.assertEqual(after_result["next_picker_id"], initial["players"][0]["id"])
        self.assertNotIn(selected["id"], [chart["id"] for chart in after_result["charts"]])
        self.pick()
        second_result = self.action("winner", player_id=initial["players"][0]["id"])
        self.assertEqual(second_result["wins"], (1, 1))
        self.assertEqual(second_result["next_picker_id"], initial["players"][1]["id"])

    def test_actions_reject_stale_tokens_even_after_undo_restores_visible_state(self):
        initial = self.snapshot()
        self.pick()
        self.action("undo")
        self.assertEqual(self.snapshot()["wins"], initial["wins"])
        self.assertNotEqual(self.snapshot()["token"], initial["token"])
        with self.assertRaises(StaleMatchAction):
            apply_control_action(
                "pick", initial["token"], self.account.pk, self.guild_id,
                chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"],
            )
        self.assertIsNone(validate_fixture().current_round.chart_id)

    def test_actions_and_reads_reject_different_accounts_or_guilds(self):
        other = DiscordUser.objects.create(id=711, is_active=True)
        DiscordToken.objects.create(user=other, access_token="other-access", refresh_token="other-refresh")
        Guilds.objects.get(pk=self.guild_id).referees.add(other)
        initial = self.snapshot()
        for account_id, guild_id in ((other.pk, self.guild_id), (self.account.pk, 511)):
            with self.subTest(account_id=account_id, guild_id=guild_id):
                with self.assertRaises(StagingConfigurationError):
                    build_control_snapshot(account_id, guild_id)
                self.assert_action_rejected(
                    "pick", account_id=account_id, guild_id=guild_id,
                    chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"],
                )

    def test_controls_recheck_revocation_and_disabled_owner_before_writes(self):
        initial = self.snapshot()
        guild = Guilds.objects.get(pk=self.guild_id)
        for restriction in ("revoked", "disabled", "missing_oauth"):
            with self.subTest(restriction=restriction):
                if restriction == "revoked":
                    guild.referees.remove(self.account)
                elif restriction == "disabled":
                    DiscordUser.objects.filter(pk=self.account.pk).update(is_active=False)
                else:
                    DiscordToken.objects.filter(user=self.account).delete()
                with self.assertRaises(StagingConfigurationError):
                    self.snapshot()
                with self.assertRaises(StagingConfigurationError):
                    apply_control_action(
                        "pick", initial["token"], self.account.pk, self.guild_id,
                        chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"],
                    )
                self.assertEqual(get_match_state_token(Match.objects.get()), initial["token"])
                guild.referees.add(self.account)
                DiscordUser.objects.filter(pk=self.account.pk).update(is_active=True)

    def test_picks_reject_banned_unknown_reused_or_missing_charts(self):
        initial = self.snapshot()
        banned_id = Chart.objects.get(name="Synthetic Song 01").pk
        for chart_id in (banned_id, 999999, None):
            with self.subTest(chart_id=chart_id):
                self.assert_action_rejected("pick", chart_id=chart_id, player_id=initial["next_picker_id"])
        selected_id = self.pick()["current_chart"]["id"]
        self.action("winner", player_id=initial["players"][0]["id"])
        self.assert_action_rejected(
            "pick", chart_id=selected_id, player_id=self.snapshot()["next_picker_id"],
        )

    def test_picks_require_the_current_picker_and_winners_require_a_match_player(self):
        initial = self.snapshot()
        for player_id in (initial["players"][1]["id"], 999999, None):
            with self.subTest(picker_id=player_id):
                self.assert_action_rejected("pick", chart_id=initial["charts"][0]["id"], player_id=player_id)
        self.pick()
        for player_id in (999999, None):
            with self.subTest(winner_id=player_id):
                self.assert_action_rejected("winner", player_id=player_id)

    def test_unknown_actions_and_missing_tokens_cannot_mutate_the_fixture(self):
        initial = self.snapshot()
        for action in ("upload", "ban", "bind-channel", "win-p1", "", None):
            with self.subTest(action=action):
                self.assert_action_rejected(action)
        for token in (None, "", "forged-token"):
            with self.subTest(token=token):
                self.assert_action_rejected(
                    "pick", expected_state=token,
                    chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"],
                )

    def test_control_values_require_numeric_ids_and_only_the_expected_choices(self):
        initial = self.snapshot()
        for field in ("account_id", "guild_id", "chart_id", "player_id"):
            values = {
                "account_id": self.account.pk, "guild_id": self.guild_id,
                "chart_id": initial["charts"][0]["id"], "player_id": initial["next_picker_id"],
            }
            for value in (True, str(values[field])):
                with self.subTest(field=field, value=value):
                    self.assert_action_rejected("pick", **{**values, field: value})
        for action in ("undo", "finalize"):
            self.assert_action_rejected(action, player_id=initial["players"][0]["id"])
            self.assert_action_rejected(action, chart_id=initial["charts"][0]["id"])
        self.pick()
        self.assert_action_rejected(
            "winner", player_id=initial["players"][0]["id"], chart_id=initial["charts"][0]["id"],
        )

    def test_invalid_lifecycle_actions_leave_pending_round_unchanged(self):
        initial = self.snapshot()
        self.assert_action_rejected("winner", player_id=initial["players"][0]["id"])
        self.assert_action_rejected("finalize")
        self.assert_action_rejected("undo")
        self.pick()
        self.assert_action_rejected(
            "pick", chart_id=initial["charts"][1]["id"], player_id=initial["next_picker_id"],
        )
        self.assert_action_rejected("finalize")

    def test_undo_removes_recorded_result_then_selection_but_preserves_opening_bans(self):
        initial = self.snapshot()
        self.pick()
        self.action("winner", player_id=initial["players"][0]["id"])
        undone_result = self.action("undo")
        self.assertEqual(undone_result["wins"], (0, 0))
        self.assertEqual(undone_result["round_number"], 1)
        self.assertIsNotNone(undone_result["current_chart"])
        undone_selection = self.action("undo")
        self.assertIsNone(undone_selection["current_chart"])
        self.assertFalse(undone_selection["can_undo"])
        self.assert_action_rejected("undo")
        self.assertEqual(validate_fixture().match_bans.count(), 4)

    def test_completion_is_explicit_and_does_not_submit_evidence_or_bind_discord(self):
        initial = self.snapshot()
        for unused_round in range(4):
            self.pick()
            latest = self.action("winner", player_id=initial["players"][0]["id"])
        self.assertEqual(latest["wins"], (4, 0))
        self.assertTrue(latest["can_finalize"])
        self.assertFalse(latest["complete"])
        completed = self.action("finalize")
        self.assertTrue(completed["complete"])
        self.assertFalse(completed["can_finalize"])
        self.assertTrue(completed["can_undo"])
        match = validate_fixture()
        self.assertFalse(match.finished or match.submitted)
        self.assertIsNone(match.channel_id)
        self.assertIsNone(match.message)
        self.assertFalse(Channels.objects.exists())
        self.assertTrue(all(not record.screenshot and not record.steg for record in match.match_rounds.all()))
        reopened = self.action("undo")
        self.assertFalse(reopened["complete"])
        self.assertEqual(reopened["wins"], (4, 0))
        self.assertTrue(reopened["can_finalize"])

    def test_changed_owned_graph_is_rejected_before_snapshot_or_write(self):
        initial = self.snapshot()
        Chart.objects.filter(name="Synthetic Song 05").update(name="Changed chart")
        with self.assertRaises(StagingConfigurationError):
            self.snapshot()
        with self.assertRaises(StagingConfigurationError):
            apply_control_action(
                "pick", initial["token"], self.account.pk, self.guild_id,
                chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"],
            )
        self.assertIsNone(Match.objects.get().current_round.chart_id)

    def test_shared_grant_creates_only_an_active_unprivileged_account_without_oauth(self):
        grant_shared_referee(711, self.guild_id)
        other = DiscordUser.objects.get(pk=711)
        self.assertTrue(other.is_active)
        self.assertFalse(other.is_staff or other.is_superuser or other.has_usable_password())
        self.assertFalse(DiscordToken.objects.filter(user=other).exists())
        self.assertTrue(Guilds.objects.get(pk=self.guild_id).referees.filter(pk=other.pk).exists())
        grant_shared_referee(other.pk, self.guild_id)
        self.assertEqual(DiscordUser.objects.filter(pk=other.pk).count(), 1)
        self.assertEqual(validate_fixture().referee_id, self.account.pk)

    def test_shared_referee_requires_explicit_mode_and_uses_same_state_token(self):
        initial = self.snapshot()
        grant_shared_referee(711, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            build_control_snapshot(711, self.guild_id)
        shared = build_control_snapshot(711, self.guild_id, shared_referees=True)
        self.assertEqual(shared, initial)
        selected = apply_control_action(
            "pick", initial["token"], 711, self.guild_id,
            chart_id=initial["charts"][0]["id"], player_id=initial["next_picker_id"], shared_referees=True,
        )
        self.assertEqual(selected["current_chart"]["id"], initial["charts"][0]["id"])
        with self.assertRaises(StaleMatchAction):
            apply_control_action("undo", initial["token"], self.account.pk, self.guild_id)
        self.assertEqual(validate_fixture().referee_id, self.account.pk)

    def test_shared_referee_revocation_is_scoped_and_disabled_accounts_are_not_reactivated(self):
        for account_id in (711, 712):
            grant_shared_referee(account_id, self.guild_id)
        revoke_shared_referee(711, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            build_control_snapshot(711, self.guild_id, shared_referees=True)
        build_control_snapshot(712, self.guild_id, shared_referees=True)
        self.snapshot()
        DiscordUser.objects.filter(pk=712).update(is_active=False)
        with self.assertRaises(StagingConfigurationError):
            grant_shared_referee(712, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            build_control_snapshot(712, self.guild_id, shared_referees=True)
        self.assertFalse(DiscordUser.objects.get(pk=712).is_active)

    def test_shared_grants_reject_wrong_guild_invalid_identity_and_missing_owner_access(self):
        for account_id, guild_id in ((711, 511), (True, self.guild_id), ("711", self.guild_id)):
            with self.subTest(account_id=account_id, guild_id=guild_id):
                with self.assertRaises(StagingConfigurationError):
                    grant_shared_referee(account_id, guild_id)
                with self.assertRaises(StagingConfigurationError):
                    revoke_shared_referee(account_id, guild_id)
        self.assertFalse(DiscordUser.objects.filter(pk=711).exists())
        grant_shared_referee(711, self.guild_id)
        revoke_shared_referee(self.account.pk, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            grant_shared_referee(712, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            build_control_snapshot(711, self.guild_id, shared_referees=True)
        revoke_shared_referee(711, self.guild_id)
        self.assertFalse(Guilds.objects.get(pk=self.guild_id).referees.exists())

    def test_shared_referees_have_no_administrator_bypass_or_cross_guild_grant(self):
        other = DiscordUser.objects.create(pk=711, is_active=True, is_staff=True, is_superuser=True)
        initial = self.snapshot()
        for options in ({"shared_referees": True}, {"shared_referees": "true"}):
            with self.subTest(options=options), self.assertRaises(StagingConfigurationError):
                build_control_snapshot(other.pk, self.guild_id, **options)
        with self.assertRaises(StagingConfigurationError):
            apply_control_action("undo", initial["token"], other.pk, self.guild_id, shared_referees=True)
        grant_shared_referee(other.pk, self.guild_id)
        with self.assertRaises(StagingConfigurationError):
            build_control_snapshot(other.pk, 511, shared_referees=True)
        other.refresh_from_db()
        self.assertTrue(other.is_staff and other.is_superuser)
