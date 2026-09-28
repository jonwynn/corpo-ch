"""Isolated database reads, access checks and staff viewer responses."""

from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest import TestCase as UnitTestCase
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from corpoch.dbot.models import Guilds
from corpoch.match_viewer import build_match_presentation
from corpoch.match_viewer_reader import (
    ViewerReadError, discover_matches, match_read_transaction, read_alias,
    read_match_snapshot,
)
from corpoch.match_viewer_views import (
    match_viewer, match_viewer_index, match_viewer_state,
)
from corpoch.models import (
    Bracket, DiscordUser, GroupSeed, Match, MatchBan, MatchRound, Tournament,
)
from tests.match_fixtures import create_corp_match


@override_settings(MATCH_VIEWER_ENABLED=True, MATCH_VIEWER_POLLING_ENABLED=False)
class MatchViewerReadTests(TestCase):
    def setUp(self):
        self.match, self.seeds, self.charts = create_corp_match()
        self.guild = Guilds.objects.create(id=510, name="Fixture Staff")
        Tournament.objects.filter(pk=self.match.tournament.pk).update(guild=self.guild)
        self.staff = DiscordUser.objects.create(id=710, is_active=True)
        self.guild.referees.add(self.staff)
        self.factory = RequestFactory()

    def request(self, data=None, method="get", user=None):
        request = getattr(self.factory, method)("/match-viewer/", data or {})
        request.user = user if user is not None else self.staff
        return request

    def source(self):
        return read_match_snapshot(self.staff.pk, self.match.pk)

    def presentation(self):
        return build_match_presentation(self.source())

    def add_round(self, number=1, winner=None, chart=None, picked=None):
        if not self.match.match_bans.exists():
            for index, owner in enumerate((0, 1, 1, 0)):
                MatchBan.objects.create(
                    match=self.match, num=index, player=self.seeds[owner].player,
                    chart=self.charts[index], action_phase="opening",
                )
        previous = self.match.match_rounds.filter(num=number - 1).first()
        default_picker = self.seeds[0].player
        if previous and previous.winner_id:
            default_picker = next(seed.player for seed in self.seeds if seed.player_id != previous.winner_id)
        return MatchRound.objects.create(
            match=self.match, num=number, chart=chart or self.charts[4],
            picked=picked or default_picker, winner=winner,
            loser=next(seed.player for seed in self.seeds if seed.player_id != winner.pk) if winner else None,
            selection_kind="player",
        )

    def test_supported_source_and_route_names(self):
        source = self.source()
        self.assertTrue(source["rules"]["profile_supported"])
        self.assertEqual(self.presentation()["target"], 4)
        self.assertEqual(self.presentation()["wins"], [0, 0])
        self.assertEqual([player["name"] for player in source["players"]], ["Higher Seed", "Lower Seed"])
        self.assertEqual(resolve(reverse("match_viewer_state", args=[self.match.pk])).func, match_viewer_state)
        self.assertEqual(reverse("match_viewer_index"), "/match-viewer/")

    def test_access_requires_current_active_account_and_stored_same_guild_role(self):
        stranger = DiscordUser.objects.create(id=711, is_active=True, is_staff=True)
        for account_id, status in ((None, 401), (9999, 401), (stranger.pk, 403)):
            with self.subTest(account_id=account_id):
                with self.assertRaises(ViewerReadError) as error:
                    read_match_snapshot(account_id, self.match.pk)
                self.assertEqual(error.exception.status, status)
        self.guild.referees.remove(self.staff)
        Match.objects.filter(pk=self.match.pk).update(referee=self.staff)
        self.assertEqual(match_viewer_state(self.request(), self.match.pk).status_code, 403)
        self.guild.admins.add(self.staff)
        self.assertEqual(self.source()["access"]["same_guild_role"], "admin")
        stale_user = SimpleNamespace(pk=self.staff.pk, is_authenticated=True, is_active=True, is_superuser=True)
        DiscordUser.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(match_viewer_state(self.request(user=stale_user), self.match.pk).status_code, 403)

    def test_cross_guild_and_null_guild_require_superuser(self):
        other_guild = Guilds.objects.create(id=511, name="Other Staff")
        other_guild.referees.add(self.staff)
        self.guild.referees.clear()
        self.assertEqual(match_viewer(self.request(), self.match.pk).status_code, 403)
        Tournament.objects.filter(pk=self.match.tournament.pk).update(guild=None)
        self.assertEqual(match_viewer(self.request(), self.match.pk).status_code, 403)
        DiscordUser.objects.filter(pk=self.staff.pk).update(is_superuser=True)
        self.assertEqual(match_viewer(self.request(), self.match.pk).status_code, 200)
        DiscordUser.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(match_viewer(self.request(), self.match.pk).status_code, 403)

    def test_unauthenticated_not_found_and_failure_fragments_are_controlled(self):
        anonymous = SimpleNamespace(is_authenticated=False)
        response = match_viewer_state(self.request(user=anonymous), self.match.pk)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Location", response)
        self.assertContains(response, 'data-http-status="401"', status_code=401)
        self.assertEqual(match_viewer_state(self.request(), "missing").status_code, 404)
        with patch("corpoch.match_viewer_views.read_match_snapshot", side_effect=DatabaseError("private database path")):
            response = match_viewer_state(self.request(), self.match.pk)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "private database path", status_code=503)
        self.assertNotContains(response, 'data-score-slot=', status_code=503)

    def test_all_responses_are_private_and_get_only(self):
        for view, arguments in ((match_viewer, [self.match.pk]), (match_viewer_state, [self.match.pk]), (match_viewer_index, [])):
            with self.subTest(view=view.__name__):
                response = view(self.request(), *arguments)
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])
                self.assertIn("Cookie", response["Vary"])
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                rejected = view(self.request(method="post"), *arguments)
                self.assertEqual(rejected.status_code, 405)
                self.assertEqual(rejected["Allow"], "GET")

    @override_settings(MATCH_VIEWER_ENABLED=False)
    def test_disabled_gate_does_not_read_database(self):
        with self.assertNumQueries(0):
            response = match_viewer_state(self.request(), self.match.pk)
        self.assertEqual(response.status_code, 404)

    def test_polling_gate_only_publishes_endpoint_when_enabled(self):
        response = match_viewer(self.request(), self.match.pk)
        self.assertNotContains(response, "data-state-url=")
        with override_settings(MATCH_VIEWER_POLLING_ENABLED=True):
            response = match_viewer(self.request(), self.match.pk)
        self.assertContains(response, f'data-state-url="/match-viewer/{self.match.pk}/state/"')

    def test_alias_is_full_unsplit_and_invalid_config_does_not_crash(self):
        config = {"names_list": [{"ch_name": "Other", "is_primary": False}, {"ch_name": "[LOS] A long player name", "is_primary": True}], "private": "secret-value"}
        with connection.cursor() as cursor:
            cursor.execute("UPDATE corpoch_tournamentplayer SET config = %s WHERE id = %s", [json.dumps(config), self.seeds[0].player_id])
        self.assertEqual(self.source()["players"][0]["name"], "[LOS] A long player name")
        self.assertNotIn("secret-value", json.dumps(self.source()))
        with connection.cursor() as cursor:
            cursor.execute("UPDATE corpoch_tournamentplayer SET config = %s WHERE id = %s", [json.dumps({"names_list": ["malformed"]}), self.seeds[0].player_id])
        self.assertIsNone(self.source()["players"][0]["name"])

    def test_selected_bracket_controls_visibility_even_if_shared_chart_is_revealed_elsewhere(self):
        self.add_round()
        self.match.match_bans.all().delete()
        MatchBan.objects.create(match=self.match, num=0, player=self.seeds[0].player, chart=self.charts[4], action_phase="opening")
        other = Bracket(tournament=self.match.tournament, name="Other revealed bracket", revealed=True)
        other.save()
        self.charts[4].brackets.add(other)
        Bracket.objects.filter(pk=self.match.group.bracket_id).update(revealed=False)
        source = self.source()
        viewer = build_match_presentation(source)
        self.assertIsNone(source["rounds"][0]["chart_title"])
        self.assertNotIn(self.charts[4].name, json.dumps(viewer))
        self.assertNotIn("id", viewer["rounds"][0]["chart"])
        self.assertEqual(viewer["hidden_chart_count"], 1)
        self.assertEqual(viewer["effective_ban_chart_ids"], [])

    def test_chart_outside_selected_setlist_is_withheld(self):
        self.add_round()
        self.charts[4].brackets.remove(self.match.group.bracket)
        viewer = self.presentation()
        self.assertNotIn(self.charts[4].name, json.dumps(viewer))
        self.assertNotIn("id", viewer["rounds"][0]["chart"])
        self.assertEqual(viewer["state"], "unsupported_rules")

    def test_screenshot_and_metadata_are_presence_only_and_never_determine_score(self):
        recorded = self.add_round(winner=self.seeds[1].player)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE corpoch_matchround SET screenshot = %s, steg = %s WHERE id = %s", ["private/path.png", json.dumps({"private": "payload-secret", "score": 99999}), recorded.pk])
        source = self.source()
        self.assertTrue(source["rounds"][0]["screenshot_present"])
        self.assertEqual(source["rounds"][0]["metadata_kind"], "present")
        self.assertNotIn("private", json.dumps(source))
        self.assertEqual(build_match_presentation(source)["wins"], [0, 1])

    def test_contextual_pins_preserve_slots_after_seed_value_correction(self):
        viewer = self.presentation()
        GroupSeed.objects.filter(pk=self.seeds[0].pk).update(seed=3)
        refreshed = build_match_presentation(self.source(), viewer["assignment"])
        self.assertEqual(refreshed["slots"], viewer["slots"])
        response = match_viewer_state(self.request({"pins": json.dumps(viewer["assignment"])}), self.match.pk)
        self.assertContains(response, "Higher Seed")
        Match.objects.filter(pk=self.match.pk).update(rev_seeds=True)
        response = match_viewer_state(self.request({"pins": json.dumps(viewer["assignment"])}), self.match.pk)
        self.assertContains(response, 'data-state="setup_changed"')

    def test_invalid_pin_json_rejected_and_semantic_mismatch_is_setup_changed(self):
        for pins in ("{invalid", "x" * 2049):
            response = match_viewer_state(self.request({"pins": pins}), self.match.pk)
            self.assertEqual(response.status_code, 400)
        response = match_viewer_state(self.request({"pins": json.dumps({"players": [], "group_id": "other"})}), self.match.pk)
        self.assertContains(response, 'data-state="setup_changed"')

    def test_undo_and_correction_read_surviving_rows(self):
        recorded = self.add_round(winner=self.seeds[0].player)
        self.add_round(2, picked=self.seeds[1].player, chart=self.charts[5])
        before = self.presentation()
        self.assertEqual(before["wins"], [1, 0])
        MatchRound.objects.filter(pk=recorded.pk).update(winner=self.seeds[1].player)
        corrected = self.presentation()
        self.assertEqual(corrected["wins"], [0, 1])
        self.assertNotEqual(corrected["digest"], before["digest"])
        MatchRound.objects.filter(match=self.match, num=2).delete()
        self.assertEqual(len(self.presentation()["round_history"]), 1)

    def test_invalid_corp_history_is_reviewable_without_erasing_known_points(self):
        recorded = self.add_round(winner=self.seeds[0].player)
        self.assertTrue(self.source()["history_valid"])
        for update in (
            {"picked_id": self.seeds[1].player_id},
            {"loser_id": self.seeds[0].player_id},
            {"chart_id": self.charts[0].pk},
        ):
            original = {key: getattr(recorded, key) for key in update}
            MatchRound.objects.filter(pk=recorded.pk).update(**update)
            viewer = self.presentation()
            self.assertEqual(viewer["state"], "needs_review")
            self.assertIn("corp_history_invalid", viewer["quality"])
            self.assertEqual(viewer["wins"], [1, 0])
            MatchRound.objects.filter(pk=recorded.pk).update(**original)
        self.match.match_bans.filter(num=1).update(saved=True)
        self.assertFalse(self.source()["history_valid"])
        self.assertNotIn("loser_id", self.source()["rounds"][0])

    def test_finalization_stays_distinct_from_screenshot_and_export_flags(self):
        for number in range(1, 5):
            self.add_round(number, winner=self.seeds[0].player, chart=self.charts[number + 3])
        Match.objects.filter(pk=self.match.pk).update(complete=True, winner=self.seeds[0].player, finished=False, submitted=False)
        viewer = self.presentation()
        self.assertEqual(viewer["state"], "complete")
        self.assertEqual(viewer["wins"], [4, 0])
        self.assertFalse(viewer["status"]["evidence_finished"])
        self.assertFalse(viewer["status"]["export_recorded"])

    def test_null_and_cross_context_roster_do_not_reveal_foreign_alias(self):
        GroupSeed.objects.filter(pk=self.seeds[0].pk).update(player=None)
        self.assertEqual(self.presentation()["state"], "setup_incomplete")
        other, other_seeds, charts = create_corp_match(match_id="other-context")
        self.match.players.set([other_seeds[0], self.seeds[1]])
        source = self.source()
        self.assertIsNone(next(player for player in source["players"] if player["seed_id"] == str(other_seeds[0].pk))["name"])
        self.assertFalse(source["rules"]["profile_supported"])

    def test_selected_read_query_count_is_independent_of_unrelated_matches_and_read_only(self):
        self.add_round()
        with CaptureQueriesContext(connection) as before:
            self.source()
        Match.objects.bulk_create([Match(id=f"unrelated-{number}", group=self.match.group) for number in range(30)])
        with CaptureQueriesContext(connection) as after:
            self.source()
        self.assertEqual(len(before), len(after))
        self.assertLessEqual(len(after), 12)
        for query in after:
            self.assertTrue(query["sql"].lstrip().upper().startswith(("SELECT", "SAVEPOINT", "RELEASE")), query["sql"])

    def test_index_is_scoped_paginated_and_has_display_names_without_charts(self):
        Match.objects.bulk_create([Match(id=f"listed-{number}", group=self.match.group) for number in range(27)])
        stranger, seeds, charts = create_corp_match(match_id="not-authorized")
        first = discover_matches(self.staff.pk)
        second = discover_matches(self.staff.pk, 2)
        self.assertEqual(len(first["matches"]), 25)
        self.assertEqual(len(second["matches"]), 3)
        self.assertEqual(first["next_page"], 2)
        summaries = first["matches"] + second["matches"]
        self.assertNotIn(stranger.pk, [item["match_id"] for item in summaries])
        self.assertEqual(next(item for item in summaries if item["match_id"] == self.match.pk)["players_label"], "Higher Seed vs Lower Seed")
        self.assertNotIn("Fixture Song", json.dumps(first))
        self.guild.referees.clear()
        self.assertEqual(discover_matches(self.staff.pk)["matches"], [])

    def test_unapproved_or_invalid_profiles_have_no_trusted_target(self):
        rules = self.match.group.bracket.ruleset
        for update in ({"tb_ruleset": "single"}, {"num_bans": 1}, {"seed_inversions": True}):
            rules.refresh_from_db()
            original = {key: getattr(rules, key) for key in update}
            type(rules).objects.filter(pk=rules.pk).update(**update)
            self.assertFalse(self.source()["rules"]["profile_supported"])
            self.assertIsNone(self.presentation()["target"])
            type(rules).objects.filter(pk=rules.pk).update(**original)


class ViewerTransactionProtocolTests(UnitTestCase):
    def test_alias_parser_rejects_malformed_and_preserves_full_label(self):
        self.assertIsNone(read_alias("not json"))
        self.assertIsNone(read_alias({"names_list": []}))
        self.assertEqual(read_alias({"names_list": [{"ch_name": "[LOS] Biscuit"}]}), "[LOS] Biscuit")

    def create_connection(self, *, nested=False, autocommit=True, sql_failure=False):
        events = []

        @contextmanager
        def cursor():
            def execute(sql):
                events.append(sql)
                if sql_failure:
                    raise DatabaseError("fixture SQL failure")
            yield SimpleNamespace(execute=execute)

        fake = SimpleNamespace(
            vendor="mysql", in_atomic_block=nested,
            get_autocommit=lambda: autocommit, cursor=cursor,
            close=lambda: events.append("close"),
        )

        @contextmanager
        def atomic(**arguments):
            events.append("begin")
            try:
                yield
            finally:
                events.append("end")

        return fake, atomic, events

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_mysql_protocol_sets_next_transaction_then_closes_connection(self):
        fake, atomic, events = self.create_connection()
        with patch("corpoch.match_viewer_reader.connections", {"default": fake}), patch("corpoch.match_viewer_reader.transaction.atomic", atomic):
            with match_read_transaction():
                events.append("read")
        self.assertEqual(events, ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", "begin", "read", "end", "close"])

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_mysql_connection_closes_after_query_or_body_failure(self):
        for sql_failure in (True, False):
            fake, atomic, events = self.create_connection(sql_failure=sql_failure)
            with patch("corpoch.match_viewer_reader.connections", {"default": fake}), patch("corpoch.match_viewer_reader.transaction.atomic", atomic):
                with self.assertRaises(DatabaseError):
                    with match_read_transaction():
                        raise DatabaseError("fixture read failure")
            self.assertEqual(events[-1], "close")

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_existing_mysql_transaction_is_rejected_without_closing_callers_connection(self):
        for options in ({"nested": True}, {"autocommit": False}):
            fake, atomic, events = self.create_connection(**options)
            with patch("corpoch.match_viewer_reader.connections", {"default": fake}):
                with self.assertRaises(ViewerReadError):
                    with match_read_transaction():
                        self.fail("Rejected transaction cannot be read")
            self.assertEqual(events, [])

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=False)
    def test_mysql_default_gate_and_unverified_backend_do_not_issue_commands(self):
        fake, atomic, events = self.create_connection()
        for vendor in ("mysql", "postgresql"):
            fake.vendor = vendor
            with patch("corpoch.match_viewer_reader.connections", {"default": fake}):
                with self.assertRaises(ViewerReadError):
                    with match_read_transaction():
                        self.fail("Unverified connection cannot be read")
            self.assertEqual(events, [])
