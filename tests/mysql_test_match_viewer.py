"""Real MySQL checks, loaded only by the explicit disposable-database runner."""

import os
from datetime import timedelta
from queue import Queue
import re
import threading
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

if (
    re.fullmatch(r"corpo_viewer_validation_[0-9a-f]{32}", os.environ.get("CORPO_MYSQL_VIEWER_CHECK", "")) is None
    or os.environ.get("DJANGO_SETTINGS_MODULE") != "tests.viewer_test_settings"
    or os.environ.get("CORPO_VIEWER_TEST_MODE") != "isolated-model"
):
    raise RuntimeError("Use python -m tests.mysql_viewer_check with explicit disposable-database opt-in.")

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection, connections, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from corpoch import discord_oauth, match_actions
from corpoch.dbot.models import Guilds
from corpoch.match_actions import (
    StaleMatchAction, assign_match_players, get_match_state_token, record_opening_action,
    record_round_winner, select_chart, undo_match_action,
)
from corpoch.match_viewer import build_match_presentation
from corpoch.match_viewer_reader import ViewerReadError, match_read_transaction, read_match_snapshot
from corpoch.match_viewer_views import match_viewer_state
from corpoch.models import (
    Bracket, DiscordToken, DiscordUser, Group, GroupSeed, Match, MatchBan, MatchRound,
    Tournament, TournamentPlayer,
)
from corpoch.types import CH_Name, PlayerConfig
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

    def read_during_commit(self, operation):
        """Reads across a committed change and returns coherent old and new snapshots.

        :param callable operation: Supported writer to run after the first snapshot read
        :return: Snapshots from before and after the committed change"""
        expected = read_match_snapshot(self.staff.pk, self.match.pk)
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

        reader = self.start_worker("correction-reader", read_snapshot, outcomes)
        try:
            self.assertTrue(first_read.wait(timeout=10), "Reader never established its first SELECT snapshot.")
            operation()
        finally:
            writer_finished.set()
        name, previous = self.collect_outcome(outcomes)
        reader.join(timeout=20)
        self.assertFalse(reader.is_alive())
        self.assertEqual(previous, expected)
        current = read_match_snapshot(self.staff.pk, self.match.pk)
        self.assertTrue(previous["history_valid"] and current["history_valid"])
        return previous, current

    def assert_fresh_read_committed_connection(self, previous_connection):
        """Checks that a closed snapshot connection cannot retain its isolation setting.

        :param int previous_connection: MySQL identifier before the snapshot read"""
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

    @override_settings(
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        BOT_ID="8900",
        REDIRECT_URI="https://viewer.invalid/auth",
        AUTH_URL_DISCORD=(
            "https://discord.com/oauth2/authorize?client_id=8900&response_type=code"
            "&redirect_uri=https%3A%2F%2Fviewer.invalid%2Fauth&scope=identify+guilds"
        ),
    )
    def create_browser_attempt(self):
        """Stores one real browser session and its separate authorization attempt.

        :return: Browser session key and one-use authorization state"""
        request = RequestFactory().get("/auth/start")
        request.session = SessionStore()
        destination = discord_oauth.create_discord_authorization_url(request)
        request.session.save()
        nonce = parse_qs(urlsplit(destination).query)["state"][0]
        return request.session.session_key, nonce

    def create_expired_discord_token(self):
        """Creates stored fixture credentials that require immediate renewal.

        :return: Expired token for the existing fixture account"""
        return DiscordToken.objects.create(
            user=self.staff,
            access_token="fixture-old-access",
            refresh_token="fixture-old-refresh",
            scopes="identify guilds",
            expires=timezone.now() - timedelta(days=1),
        )

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

    def test_oauth_attempt_is_consumed_once_with_two_preloaded_browser_sessions(self):
        browser_key, nonce = self.create_browser_attempt()
        callbacks = []
        for unused_index in range(2):
            request = RequestFactory().get("/auth", {"state": nonce})
            request.session = SessionStore(browser_key)
            self.assertEqual(request.session.get("discord_oauth_state"), nonce)
            callbacks.append(request)
        both_loaded = threading.Barrier(2)
        outcomes = Queue()
        original_load = discord_oauth.load_discord_attempt

        def load_before_competing_delete(request, returned_nonce):
            attempt = original_load(request, returned_nonce)
            if attempt is None:
                raise AssertionError("Both callbacks must read the unconsumed attempt before deletion.")
            both_loaded.wait(timeout=10)
            return attempt

        with patch("corpoch.discord_oauth.load_discord_attempt", side_effect=load_before_competing_delete):
            try:
                for index, request in enumerate(callbacks):
                    self.start_worker(
                        f"oauth-consumer-{index}",
                        lambda request=request: discord_oauth.consume_discord_attempt(request, nonce),
                        outcomes,
                    )
                results = [self.collect_outcome(outcomes)[1] for unused_index in range(2)]
            finally:
                both_loaded.abort()
                for worker in self.workers:
                    worker.join(timeout=20)
        self.assertCountEqual(results, [True, False])
        self.assertFalse(Session.objects.filter(session_key=nonce).exists())
        self.assertTrue(Session.objects.filter(session_key=browser_key).exists())

    def test_browser_and_scheduled_refresh_share_one_rotated_token(self):
        stored = self.create_expired_discord_token()
        browser_token = DiscordToken.objects.get(pk=stored.pk)
        scheduled_token = DiscordToken.objects.get(pk=stored.pk)
        self.assertEqual(browser_token.expires, scheduled_token.expires)
        first_locked = threading.Event()
        first_http_started = threading.Event()
        scheduled_attempted_lock = threading.Event()
        scheduled_finished = threading.Event()
        release_refresh = threading.Event()
        outcomes = Queue()
        payload = {
            "access_token": "fixture-refreshed-access",
            "refresh_token": "fixture-refreshed-refresh",
            "scope": "identify guilds",
            "expires_in": 7 * 24 * 60 * 60,
        }

        def blocked_refresh(*arguments, **keywords):
            self.assertTrue(first_locked.is_set(), "Refresh HTTP must run after acquiring the token lock.")
            first_http_started.set()
            if not release_refresh.wait(timeout=10):
                raise AssertionError("The first token refresh was not released.")
            return Mock(status_code=200, json=Mock(return_value=payload))

        def browser_refresh():
            def mark_acquired_lock(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if "FOR UPDATE" in sql.upper() and "corpoch_discordtoken" in sql:
                    first_locked.set()
                return result

            browser_token.login()
            with connections["default"].execute_wrapper(mark_acquired_lock):
                browser_token.refresh_expired_token()
            return browser_token.access_token, browser_token.refresh_token

        def scheduled_refresh():
            def mark_lock_attempt(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper() and "corpoch_discordtoken" in sql:
                    scheduled_attempted_lock.set()
                return execute(sql, params, many, context)

            try:
                scheduled_token.login()
                with connections["default"].execute_wrapper(mark_lock_attempt):
                    scheduled_token.update_code()
                return scheduled_token.access_token, scheduled_token.refresh_token
            finally:
                scheduled_finished.set()

        with patch("corpoch.models.misc.Session") as session_type:
            session_type.return_value.post.side_effect = blocked_refresh
            self.start_worker("browser-refresh", browser_refresh, outcomes)
            try:
                self.assertTrue(first_http_started.wait(timeout=10), "The browser did not reach its locked refresh.")
                self.start_worker("scheduled-refresh", scheduled_refresh, outcomes)
                self.assertTrue(scheduled_attempted_lock.wait(timeout=10), "Scheduled renewal did not attempt the token lock.")
                self.assertFalse(scheduled_finished.wait(timeout=0.2), "Scheduled renewal bypassed the held token lock.")
            finally:
                release_refresh.set()
                for worker in self.workers:
                    worker.join(timeout=20)
            results = [self.collect_outcome(outcomes)[1] for unused_index in range(2)]
            session_type.return_value.post.assert_called_once()
            self.assertEqual(session_type.return_value.post.call_args.kwargs["data"], {
                "grant_type": "refresh_token", "refresh_token": "fixture-old-refresh",
            })
            session_type.return_value.get.assert_not_called()
        self.assertEqual(results, [(payload["access_token"], payload["refresh_token"])] * 2)
        stored.refresh_from_db()
        self.assertEqual((stored.access_token, stored.refresh_token), results[0])
        self.assertEqual(browser_token.expires, scheduled_token.expires)
        self.assertGreater(stored.expires, timezone.now() + timedelta(days=6))

    @override_settings(ROOT_URLCONF="tests.model_test_discord_auth")
    def test_callback_tokens_survive_overlapping_stored_token_refresh(self):
        from tests.model_test_discord_auth import load_auth_views

        stored = self.create_expired_discord_token()
        browser_token = DiscordToken.objects.get(pk=stored.pk)
        browser_key, nonce = self.create_browser_attempt()
        callback = RequestFactory().get("/auth", {"code": "fixture-code", "state": nonce})
        callback.session = SessionStore(browser_key)
        self.assertEqual(callback.session.get("discord_oauth_state"), nonce)
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        code_exchanged = threading.Event()
        identity_loaded = threading.Event()
        callback_attempted_lock = threading.Event()
        callback_finished = threading.Event()
        outcomes = Queue()
        refreshed = {
            "access_token": "fixture-renewed-access",
            "refresh_token": "fixture-renewed-refresh",
            "scope": "identify guilds",
            "expires_in": 7 * 24 * 60 * 60,
        }
        callback_tokens = {
            **refreshed,
            "access_token": "fixture-callback-access",
            "refresh_token": "fixture-callback-refresh",
        }

        def exchange_grant(*arguments, **keywords):
            grant = keywords["data"]["grant_type"]
            if grant == "refresh_token":
                refresh_started.set()
                if not release_refresh.wait(timeout=10):
                    raise AssertionError("The stored-token refresh was not released.")
                payload = refreshed
            elif grant == "authorization_code":
                self.assertFalse(connections["default"].in_atomic_block)
                code_exchanged.set()
                payload = callback_tokens
            else:
                raise AssertionError("An unexpected OAuth grant was requested.")
            return Mock(status_code=200, json=Mock(return_value=payload))

        def read_identity(*arguments, **keywords):
            self.assertTrue(code_exchanged.is_set())
            self.assertFalse(connections["default"].in_atomic_block)
            identity_loaded.set()
            return Mock(status_code=200, json=Mock(return_value={
                "id": str(self.staff.pk), "global_name": "Fixture Staff", "avatar": None,
            }))

        def refresh():
            browser_token.login()
            browser_token.refresh_expired_token()
            return browser_token.access_token

        with load_auth_views() as views, patch("corpoch.models.misc.Session") as session_type:
            session_type.return_value.post.side_effect = exchange_grant
            session_type.return_value.get.side_effect = read_identity

            def complete_callback():
                def mark_callback_lock(execute, sql, params, many, context):
                    if "FOR UPDATE" in sql.upper() and "corpoch_discordtoken" in sql:
                        self.assertTrue(code_exchanged.is_set() and identity_loaded.is_set())
                        callback_attempted_lock.set()
                    return execute(sql, params, many, context)

                try:
                    with connections["default"].execute_wrapper(mark_callback_lock):
                        response = views.auth(callback)
                    return response.status_code, response.url
                finally:
                    callback_finished.set()

            self.start_worker("refresh-before-callback", refresh, outcomes)
            try:
                self.assertTrue(refresh_started.wait(timeout=10), "The existing token did not begin renewal.")
                self.start_worker("callback-after-refresh", complete_callback, outcomes)
                self.assertTrue(callback_attempted_lock.wait(timeout=10), "The callback did not attempt its stored-token lock.")
                self.assertTrue(code_exchanged.is_set() and identity_loaded.is_set())
                self.assertFalse(callback_finished.wait(timeout=0.2), "Callback storage bypassed the held token lock.")
            finally:
                release_refresh.set()
                for worker in self.workers:
                    worker.join(timeout=20)
            results = dict(self.collect_outcome(outcomes) for unused_index in range(2))
            self.assertEqual(session_type.return_value.post.call_count, 2)
            self.assertCountEqual(
                [call.kwargs["data"]["grant_type"] for call in session_type.return_value.post.call_args_list],
                ["refresh_token", "authorization_code"],
            )
            session_type.return_value.get.assert_called_once()
            views.update_user.assert_not_called()
        self.assertEqual(results["refresh-before-callback"], refreshed["access_token"])
        self.assertEqual(results["callback-after-refresh"], (302, "/auth/user"))
        stored.refresh_from_db()
        self.assertEqual(stored.access_token, callback_tokens["access_token"])
        self.assertEqual(stored.refresh_token, callback_tokens["refresh_token"])
        self.assertEqual(callback.session["access_token"], callback_tokens["access_token"])
        self.assertFalse(Session.objects.filter(session_key=nonce).exists())

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

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_snapshots_keep_corrected_winners_and_removed_rounds_coherent(self):
        self.select_first_chart()
        self.match = record_round_winner(
            self.match.pk, self.seeds[0].player_id,
            expected_state=get_match_state_token(self.match),
        )

        def correct_winner():
            with transaction.atomic():
                self.match = undo_match_action(self.match.pk, expected_state=get_match_state_token(self.match))
                self.match = record_round_winner(
                    self.match.pk, self.seeds[1].player_id,
                    expected_state=get_match_state_token(self.match),
                )

        previous, corrected = self.read_during_commit(correct_winner)
        self.assertEqual(build_match_presentation(previous)["wins"], [1, 0])
        self.assertEqual(build_match_presentation(corrected)["wins"], [0, 1])
        self.assertEqual(len(previous["rounds"]), 2)
        self.assertEqual(len(corrected["rounds"]), 2)
        self.assertEqual(previous["rounds"][0]["round_id"], corrected["rounds"][0]["round_id"])
        self.assertNotEqual(previous["rounds"][1]["round_id"], corrected["rounds"][1]["round_id"])

        def remove_pending_round():
            self.match = undo_match_action(self.match.pk, expected_state=get_match_state_token(self.match))

        previous, removed = self.read_during_commit(remove_pending_round)
        self.assertEqual(previous, corrected)
        self.assertEqual(build_match_presentation(removed)["wins"], [0, 0])
        self.assertEqual(len(removed["rounds"]), 1)
        self.assertEqual(removed["rounds"][0]["round_id"], corrected["rounds"][0]["round_id"])
        self.assertIsNone(removed["rounds"][0]["winner_id"])
        self.assertEqual(removed["rounds"][0]["chart_id"], self.charts[4].pk)

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_snapshot_keeps_player_reassignment_coherent(self):
        replacement = TournamentPlayer.objects.create(
            tournament=self.match.group.bracket.tournament, name="Replacement Player", is_active=True,
            config=PlayerConfig(names_list=[CH_Name(ch_name="Replacement Player", is_primary=True)]),
        )
        replacement_seed = GroupSeed.objects.create(group=self.match.group, player=replacement, seed=3)

        def reassign_player():
            self.match = assign_match_players(
                self.match.pk, [self.seeds[0].pk, replacement_seed.pk],
                expected_state=get_match_state_token(self.match),
            )

        previous, reassigned = self.read_during_commit(reassign_player)
        previous_viewer = build_match_presentation(previous)
        current_viewer = build_match_presentation(reassigned)
        self.assertEqual(previous_viewer["slots"], [str(seed.player_id) for seed in self.seeds])
        self.assertEqual(current_viewer["slots"], [str(self.seeds[0].player_id), str(replacement.pk)])
        self.assertEqual(current_viewer["players"][1]["name"], "Replacement Player")
        self.assertEqual(current_viewer["state"], "opening_bans")
        pinned_viewer = build_match_presentation(reassigned, previous_viewer["assignment"])
        self.assertEqual(pinned_viewer["state"], "setup_changed")
        self.assertIsNone(pinned_viewer["wins"])

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
        self.assert_fresh_read_committed_connection(previous_connection)

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_closes_connection_after_native_sql_and_body_failures(self):
        for failure in ("transaction_setup", "query", "body"):
            with self.subTest(failure=failure):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT CONNECTION_ID()")
                    previous_connection = cursor.fetchone()[0]

                def fail_transaction_setup(execute, sql, params, many, context):
                    if failure == "transaction_setup" and sql.startswith("SET TRANSACTION ISOLATION LEVEL"):
                        return execute("SELECT * FROM corpo_viewer_missing_table", (), many, context)
                    return execute(sql, params, many, context)

                expected_error = RuntimeError if failure == "body" else DatabaseError
                with self.assertRaises(expected_error), connection.execute_wrapper(fail_transaction_setup):
                    with match_read_transaction():
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT id FROM corpoch_match WHERE id = %s", [self.match.pk])
                            self.assertEqual(cursor.fetchone()[0], self.match.pk)
                            if failure == "query":
                                cursor.execute("SELECT * FROM corpo_viewer_missing_table")
                        raise RuntimeError("Controlled snapshot body failure.")
                self.assert_fresh_read_committed_connection(previous_connection)
                self.assertEqual(read_match_snapshot(self.staff.pk, self.match.pk)["match_id"], self.match.pk)

    @override_settings(MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_reader_rejects_existing_native_transactions_without_disrupting_them(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                previous_connection = cursor.fetchone()[0]
            with self.assertRaises(ViewerReadError):
                read_match_snapshot(self.staff.pk, self.match.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                self.assertEqual(cursor.fetchone()[0], previous_connection)
            self.assertTrue(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)

        connection.set_autocommit(False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                previous_connection = cursor.fetchone()[0]
            with self.assertRaises(ViewerReadError):
                read_match_snapshot(self.staff.pk, self.match.pk)
            self.assertFalse(connection.get_autocommit())
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                self.assertEqual(cursor.fetchone()[0], previous_connection)
        finally:
            connection.rollback()
            connection.set_autocommit(True)
        self.assertEqual(read_match_snapshot(self.staff.pk, self.match.pk)["match_id"], self.match.pk)

    @override_settings(MATCH_VIEWER_ENABLED=True, MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_disabled_account_is_denied_with_a_previously_loaded_session_user(self):
        self.select_first_chart()
        request = RequestFactory().get("/match-viewer/local-check/state/")
        request.user = self.staff
        self.assertEqual(match_viewer_state(request, self.match.pk).status_code, 200)
        DiscordUser.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertTrue(request.user.is_active)
        response = match_viewer_state(request, self.match.pk)
        self.assertEqual(response.status_code, 403)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, "data-score-slot", status_code=403)
        self.assertNotContains(response, self.seeds[0].player.ch_name, status_code=403)
        DiscordUser.objects.filter(pk=self.staff.pk).update(is_active=True)
        self.assertEqual(match_viewer_state(request, self.match.pk).status_code, 200)

    @override_settings(MATCH_VIEWER_ENABLED=True, MATCH_VIEWER_MYSQL_VERIFIED=True)
    def test_selected_fragment_query_count_and_payload_stay_unchanged_with_unrelated_matches(self):
        self.select_first_chart()
        request = RequestFactory().get("/match-viewer/local-check/state/")
        request.user = self.staff
        with CaptureQueriesContext(connection) as before:
            initial_response = match_viewer_state(request, self.match.pk)
        self.assertEqual(initial_response.status_code, 200)
        self.assertIn("no-store", initial_response["Cache-Control"])
        self.assertContains(initial_response, self.charts[4].name)
        self.assertIsNone(connection.connection)

        other_group = Group.objects.create(bracket=self.match.group.bracket, name="B")
        other_seeds = [
            GroupSeed.objects.create(group=other_group, player=seed.player, seed=seed.seed)
            for seed in self.seeds
        ]
        unrelated_matches = [
            Match(
                id=f"unrelated-populated-{number}",
                group=self.match.group if number < 80 else other_group,
            )
            for number in range(100)
        ]
        Match.objects.bulk_create(unrelated_matches)
        assignments = []
        bans = []
        rounds = []
        for match in unrelated_matches:
            seeds = self.seeds if match.group_id == self.match.group_id else other_seeds
            assignments.extend(
                Match.players.through(match_id=match.pk, groupseed_id=seed.pk)
                for seed in seeds
            )
            bans.extend(
                MatchBan(
                    match=match, num=number, player_id=seeds[owner].player_id,
                    chart=self.charts[number], action_phase="opening",
                )
                for number, owner in enumerate((0, 1, 1, 0))
            )
            rounds.append(MatchRound(
                match=match, num=1, chart=self.charts[4],
                picked_id=seeds[0].player_id, selection_kind="player",
            ))
        Match.players.through.objects.bulk_create(assignments)
        MatchBan.objects.bulk_create(bans)
        MatchRound.objects.bulk_create(rounds)

        with CaptureQueriesContext(connection) as after:
            populated_response = match_viewer_state(request, self.match.pk)
        self.assertEqual(populated_response.status_code, 200)
        self.assertEqual(populated_response.content, initial_response.content)
        self.assertEqual(populated_response["Cache-Control"], initial_response["Cache-Control"])
        self.assertIsNone(connection.connection)
        self.assertEqual(len(before), len(after))
        select_counts = []
        for queries in (before, after):
            statements = [query["sql"].strip().upper() for query in queries]
            select_counts.append(sum(statement.startswith("SELECT") for statement in statements))
            for statement in statements:
                self.assertTrue(
                    statement.startswith("SELECT")
                    or statement in {"BEGIN", "COMMIT", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"},
                    "A selected fragment issued an unexpected non-read statement.",
                )
        self.assertGreater(select_counts[0], 0)
        self.assertEqual(select_counts[0], select_counts[1])
        self.assertLessEqual(select_counts[1], 12)
        print(
            f"Selected fragment with 100 unrelated populated matches: {len(after)} queries, "
            f"{select_counts[1]} SELECTs, {len(populated_response.content)} bytes; "
            "query count and payload unchanged. This is not a capacity benchmark."
        )

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
