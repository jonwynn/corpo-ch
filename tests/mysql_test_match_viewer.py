"""Real MySQL checks, loaded only by the explicit disposable-database runner."""

import os
from queue import Queue
import re
import threading
from unittest.mock import patch

if (
    re.fullmatch(r"corpo_viewer_validation_[0-9a-f]{32}", os.environ.get("CORPO_MYSQL_VIEWER_CHECK", "")) is None
    or os.environ.get("DJANGO_SETTINGS_MODULE") != "tests.viewer_test_settings"
    or os.environ.get("CORPO_VIEWER_TEST_MODE") != "isolated-model"
):
    raise RuntimeError("Use python -m tests.mysql_viewer_check with explicit disposable-database opt-in.")

from django.conf import settings
from django.db import connection, connections
from django.test import RequestFactory, TransactionTestCase, override_settings

from corpoch import match_actions
from corpoch.dbot.models import Guilds
from corpoch.match_actions import (
    StaleMatchAction, get_match_state_token, record_opening_action,
    record_round_winner, select_chart,
)
from corpoch.match_viewer import build_match_presentation
from corpoch.match_viewer_reader import read_match_snapshot
from corpoch.match_viewer_views import match_viewer_state
from corpoch.models import Bracket, DiscordUser, Match, Tournament
from tests.match_fixtures import create_corp_match


class MysqlMatchViewerChecks(TransactionTestCase):
    """Uses separate connections and controlled interleavings on real InnoDB."""

    def setUp(self):
        database_name = os.environ.get("CORPO_MYSQL_VIEWER_CHECK")
        self.assertIsNotNone(database_name, "Use python -m tests.mysql_viewer_check with explicit opt-in.")
        self.assertEqual(connection.vendor, "mysql")
        self.assertEqual(connection.settings_dict["NAME"], database_name)
        with connection.cursor() as cursor:
            cursor.execute("SELECT ENGINE FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('corpoch_match', 'corpoch_matchround')")
            self.assertEqual([row[0].lower() for row in cursor.fetchall()], ["innodb", "innodb"])
        self.match, self.seeds, self.charts = create_corp_match()
        self.guild = Guilds.objects.create(id=510, name="Local validation staff")
        Tournament.objects.filter(pk=self.match.tournament.pk).update(guild=self.guild)
        self.staff = DiscordUser.objects.create(id=710, is_active=True)
        self.guild.referees.add(self.staff)
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.join(timeout=20)
            self.assertFalse(worker.is_alive(), "A database worker did not stop; do not drop its database.")
        connections.close_all()
        super().tearDown()

    def start_worker(self, name, operation, outcomes):
        """Runs an operation with a thread-owned Django database connection.

        :param str name: Worker name suffix
        :param callable operation: Database operation
        :param Queue outcomes: Worker result or failure delivery"""
        def run():
            connections.close_all()
            try:
                outcomes.put((name, operation(), None))
            except BaseException as error:
                outcomes.put((name, None, error))
            finally:
                connections.close_all()

        worker = threading.Thread(target=run, name=f"corpo-mysql-check-{name}", daemon=True)
        self.workers.append(worker)
        worker.start()
        return worker

    def collect_outcome(self, outcomes):
        """Raises a worker failure in the main test thread.

        :param Queue outcomes: Worker result delivery"""
        name, result, error = outcomes.get(timeout=20)
        if error is not None:
            raise error
        return name, result

    def select_first_chart(self):
        for index, owner in enumerate((0, 1, 1, 0)):
            self.match = record_opening_action(
                self.match.pk, self.seeds[owner].player_id, self.charts[index].pk,
                expected_state=get_match_state_token(self.match),
            )
        self.match = select_chart(
            self.match.pk, self.charts[4].pk, player_id=self.seeds[0].player_id,
            expected_state=get_match_state_token(self.match),
        )

    def test_runner_starts_with_all_viewer_gates_disabled(self):
        self.assertFalse(settings.MATCH_VIEWER_ENABLED)
        self.assertFalse(settings.MATCH_VIEWER_POLLING_ENABLED)
        self.assertFalse(settings.MATCH_VIEWER_MYSQL_VERIFIED)
        self.assertEqual(settings.CELERY_BROKER_URL, "memory://")

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_sees_one_snapshot_while_result_and_next_round_commit(self):
        self.select_first_chart()
        first_read = threading.Event()
        writer_finished = threading.Event()
        outcomes = Queue()

        def read_snapshot():
            def pause_after_account_read(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if sql.lstrip().upper().startswith("SELECT") and "corpoch_discorduser" in sql and not first_read.is_set():
                    first_read.set()
                    if not writer_finished.wait(timeout=10):
                        raise AssertionError("The writer did not commit before the snapshot continued.")
                return result

            with connections["default"].execute_wrapper(pause_after_account_read):
                return read_match_snapshot(self.staff.pk, self.match.pk)

        reader = self.start_worker("snapshot-reader", read_snapshot, outcomes)
        try:
            self.assertTrue(first_read.wait(timeout=10), "Reader never established its first SELECT snapshot.")
            self.match = record_round_winner(
                self.match.pk, self.seeds[0].player_id,
                expected_state=get_match_state_token(self.match),
            )
        finally:
            writer_finished.set()
        name, previous = self.collect_outcome(outcomes)
        reader.join(timeout=20)
        self.assertFalse(reader.is_alive())
        current = read_match_snapshot(self.staff.pk, self.match.pk)
        self.assertEqual(build_match_presentation(previous)["wins"], [0, 0])
        self.assertEqual(len(previous["rounds"]), 1)
        self.assertIsNone(previous["rounds"][0]["winner_id"])
        self.assertEqual(build_match_presentation(current)["wins"], [1, 0])
        self.assertEqual(len(current["rounds"]), 2)
        self.assertIsNone(current["rounds"][1]["chart_id"])
        self.assertTrue(previous["history_valid"] and current["history_valid"])

    def test_two_actions_with_one_token_allow_exactly_one_commit(self):
        token = get_match_state_token(self.match)
        first_locked = threading.Event()
        second_attempted_lock = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        outcomes = Queue()
        original_token = match_actions.get_match_state_token

        def token_under_lock(match):
            value = original_token(match)
            if threading.current_thread().name.endswith("first-action"):
                first_locked.set()
                if not release_first.wait(timeout=10):
                    raise AssertionError("The first action was not released.")
            return value

        def action(chart_index, mark_competitor=False):
            def mark_lock_attempt(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper():
                    second_attempted_lock.set()
                return execute(sql, params, many, context)

            try:
                if mark_competitor:
                    with connections["default"].execute_wrapper(mark_lock_attempt):
                        record_opening_action(
                            self.match.pk, self.seeds[0].player_id, self.charts[chart_index].pk,
                            expected_state=token,
                        )
                else:
                    record_opening_action(
                        self.match.pk, self.seeds[0].player_id, self.charts[chart_index].pk,
                        expected_state=token,
                    )
                return "committed"
            except StaleMatchAction:
                return "stale"
            finally:
                if mark_competitor:
                    second_finished.set()

        with patch("corpoch.match_actions.get_match_state_token", side_effect=token_under_lock):
            self.start_worker("first-action", lambda: action(0), outcomes)
            try:
                self.assertTrue(first_locked.wait(timeout=10), "First writer did not acquire its row lock.")
                self.start_worker("second-action", lambda: action(1, True), outcomes)
                self.assertTrue(second_attempted_lock.wait(timeout=10), "Second writer did not attempt the same row lock.")
                self.assertFalse(second_finished.wait(timeout=0.2), "Second writer completed while the first held the match lock.")
            finally:
                release_first.set()
            results = [self.collect_outcome(outcomes)[1] for index in range(2)]
        self.assertCountEqual(results, ["committed", "stale"])
        self.assertEqual(self.match.match_bans.count(), 1)
        self.assertEqual(self.match.match_bans.get().chart_id, self.charts[0].pk)
        self.assertEqual(Match.objects.get(pk=self.match.pk).action_revision, 1)

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_closes_connection_and_does_not_leak_repeatable_read(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT CONNECTION_ID()")
            previous_connection = cursor.fetchone()[0]
        read_match_snapshot(self.staff.pk, self.match.pk)
        self.assertIsNone(connection.connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT CONNECTION_ID()")
            self.assertNotEqual(cursor.fetchone()[0], previous_connection)
            cursor.execute("SHOW VARIABLES LIKE 'transaction_isolation'")
            row = cursor.fetchone()
            if row is None:
                cursor.execute("SHOW VARIABLES LIKE 'tx_isolation'")
                row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1].replace("_", "-").upper(), "READ-COMMITTED")

    @override_settings(MATCH_VIEWER_ENABLED=True, MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_revocation_and_chart_privacy_are_rechecked_on_render(self):
        self.select_first_chart()
        Bracket.objects.filter(pk=self.match.group.bracket_id).update(revealed=False)
        request = RequestFactory().get("/match-viewer/local-check/state/")
        request.user = self.staff
        response = match_viewer_state(request, self.match.pk)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.charts[4].name)
        self.assertContains(response, "Chart withheld")
        self.assertIn("no-store", response["Cache-Control"])
        self.guild.referees.remove(self.staff)
        response = match_viewer_state(request, self.match.pk)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "data-score-slot", status_code=403)
        self.assertNotContains(response, self.seeds[0].player.ch_name, status_code=403)
