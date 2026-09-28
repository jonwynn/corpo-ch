"""Checks referee authorization with fake Discord responses and no service access."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from staging.referee_check import RefereeCheckError, RefereeTransport, verify_referee


class RefereeResponse:
    """Supplies bounded fake response chunks and records connection closure."""

    def __init__(self, document=None, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else json.dumps(document).encode("utf-8")
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.closed = True
        return False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]


class StagingRefereeCheckTests(unittest.TestCase):
    """Exercises request ordering, membership decisions and safe failure paths."""

    def setUp(self):
        self.secret = "fixture-bot-secret-never-print"
        self.bot_id = "111111111111111111"
        self.guild_id = "222222222222222222"
        self.account_id = "333333333333333333"
        self.role_id = "444444444444444444"
        self.second_role_id = "555555555555555555"
        self.other_role_id = "666666666666666666"
        self.roles = [
            {"id": self.guild_id, "name": "@everyone"},
            {"id": self.role_id, "name": "Referees", "permissions": "0"},
            {"id": self.second_role_id, "name": "Head referees"},
        ]
        self.member = {
            "user": {"id": self.account_id, "bot": False},
            "roles": [self.role_id],
            "pending": False,
        }
        self.responses = self.build_responses()
        self.calls = []
        self.closed = False
        self.session = SimpleNamespace(trust_env=True, request=self.request, close=self.close)
        session_patch = patch("staging.referee_check.requests.Session", return_value=self.session)
        self.session_factory = session_patch.start()
        self.addCleanup(session_patch.stop)

    def build_responses(self):
        return [
            RefereeResponse({"id": self.bot_id, "bot": True}),
            RefereeResponse(self.roles),
            RefereeResponse(self.member),
        ]

    def request(self, method, url, **keywords):
        self.calls.append((method, url, keywords))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True

    def run_check(self, **changes):
        arguments = {
            "bot_token": self.secret, "expected_bot_id": self.bot_id,
            "guild_id": self.guild_id, "account_id": self.account_id,
            "role_ids": (self.role_id,),
            **changes,
        }
        output = io.StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(output):
                return verify_referee(**arguments)
        except RefereeCheckError as error:
            for private in (
                self.secret, self.bot_id, self.guild_id, self.account_id,
                self.role_id, self.second_role_id,
            ):
                self.assertNotIn(private, str(error))
            raise
        finally:
            self.assertEqual(output.getvalue(), "")
            self.assertTrue(all(method == "GET" for method, url, keywords in self.calls))

    def test_success_uses_only_three_bounded_gets_and_closes_connections(self):
        responses = list(self.responses)
        self.assertEqual(self.run_check(), ({"id": self.role_id, "name": "Referees"},))
        self.assertEqual(
            [(method, url) for method, url, keywords in self.calls],
            [
                ("GET", "https://discord.com/api/v10/users/@me"),
                ("GET", f"https://discord.com/api/v10/guilds/{self.guild_id}/roles"),
                ("GET", f"https://discord.com/api/v10/guilds/{self.guild_id}/members/{self.account_id}"),
            ],
        )
        self.assertFalse(self.session.trust_env)
        self.assertTrue(self.closed)
        self.assertTrue(all(response.closed for response in responses))
        for method, url, keywords in self.calls:
            self.assertEqual(keywords["headers"], {"Authorization": f"Bot {self.secret}", "Accept": "application/json"})
            self.assertEqual(keywords["timeout"], (5, 15))
            self.assertFalse(keywords["allow_redirects"])
            self.assertTrue(keywords["verify"])
            self.assertTrue(keywords["stream"])
            self.assertNotIn("data", keywords)
            self.assertNotIn("json", keywords)
            self.assertNotIn("params", keywords)

    def test_two_configured_roles_return_in_supplied_order_with_one_membership(self):
        self.assertEqual(
            self.run_check(role_ids=[self.second_role_id, self.role_id]),
            (
                {"id": self.second_role_id, "name": "Head referees"},
                {"id": self.role_id, "name": "Referees"},
            ),
        )

    def test_omitted_optional_false_flags_allow_a_human_member(self):
        self.responses[2] = RefereeResponse({"user": {"id": self.account_id}, "roles": [self.role_id]})
        self.assertEqual(len(self.run_check()), 1)

    def test_invalid_snowflakes_stop_before_creating_a_session(self):
        for name in ("expected_bot_id", "guild_id", "account_id"):
            for invalid in (
                None, True, int(self.account_id), "0", "1", "0" + self.account_id,
                " " + self.account_id, self.account_id + "\n", "9" * 20,
                str(2 ** 63), "１" * 18, "https://example.invalid/" + self.secret,
            ):
                with self.subTest(field=name, value=invalid), self.assertRaises(RefereeCheckError):
                    self.run_check(**{name: invalid})
        self.session_factory.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_largest_signed_database_id_is_accepted(self):
        self.responses[2] = RefereeResponse({**self.member, "user": {"id": str(2 ** 63 - 1)}})
        self.assertEqual(len(self.run_check(account_id=str(2 ** 63 - 1))), 1)

    def test_invalid_credentials_stop_before_creating_a_session(self):
        for invalid in (None, b"token", "", " " + self.secret, self.secret + "\r\nInjected: value", "x" * 1025):
            with self.subTest(value=invalid), self.assertRaises(RefereeCheckError):
                self.run_check(bot_token=invalid)
        self.session_factory.assert_not_called()

    def test_invalid_role_count_duplicates_everyone_and_types_are_rejected(self):
        for invalid in (
            None, self.role_id, {self.role_id}, [], [self.role_id, self.second_role_id, self.other_role_id],
            [self.role_id, self.role_id], [self.guild_id], [str(2 ** 63)], [int(self.role_id)], [[]],
        ):
            with self.subTest(roles=invalid), self.assertRaises(RefereeCheckError):
                self.run_check(role_ids=invalid)
        self.session_factory.assert_not_called()

    def test_wrong_bot_or_nonbot_identity_stops_before_guild_reads(self):
        for identity in (
            [], None, {}, {"id": self.account_id, "bot": True},
            {"id": int(self.bot_id), "bot": True}, {"id": self.bot_id},
            {"id": self.bot_id, "bot": False}, {"id": self.bot_id, "bot": 1},
            {"id": self.bot_id, "bot": "true"},
        ):
            self.responses = [RefereeResponse(identity)]
            self.calls.clear()
            with self.subTest(identity=identity), self.assertRaisesRegex(RefereeCheckError, "No server resources"):
                self.run_check()
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(self.closed)

    def test_malformed_or_missing_configured_roles_stop_before_member_read(self):
        for roles in (
            None, {}, [], [None], [{"id": self.role_id}],
            [{"id": self.role_id, "name": None}], [{"id": self.role_id, "name": " "}],
            [{"id": self.role_id, "name": "x" * 101}],
            [{"id": str(2 ** 63), "name": "Referees"}],
            [self.roles[1], self.roles[1]],
            [{"id": self.second_role_id, "name": "Other role"}],
        ):
            self.responses = [self.build_responses()[0], RefereeResponse(roles)]
            self.calls.clear()
            with self.subTest(roles=roles), self.assertRaises(RefereeCheckError):
                self.run_check()
            self.assertEqual(len(self.calls), 2)

    def test_all_configured_roles_must_exist_even_when_member_holds_one(self):
        self.responses[1] = RefereeResponse([self.roles[1]])
        with self.assertRaisesRegex(RefereeCheckError, "configured referee role is unavailable"):
            self.run_check(role_ids=(self.role_id, self.second_role_id))
        self.assertEqual(len(self.calls), 2)

    def test_wrong_nonhuman_pending_or_malformed_members_are_denied(self):
        members = [
            None, [], {}, {**self.member, "user": None},
            {**self.member, "user": {"id": self.bot_id}},
            {**self.member, "user": {"id": int(self.account_id)}},
        ]
        members.extend({**self.member, "user": {"id": self.account_id, "bot": value}} for value in (True, None, 0, "false"))
        members.extend({**self.member, "pending": value} for value in (True, None, 0, "false"))
        members.extend(
            {**self.member, "roles": value}
            for value in (None, self.role_id, [], [self.other_role_id], [self.guild_id], [self.role_id, self.role_id], [self.role_id, None])
        )
        for member in members:
            self.responses = [*self.build_responses()[:2], RefereeResponse(member)]
            self.calls.clear()
            with self.subTest(member=member), self.assertRaisesRegex(RefereeCheckError, "authorized human referee"):
                self.run_check()
            self.assertEqual(len(self.calls), 3)
            self.assertTrue(self.closed)

    def test_http_errors_and_redirects_stop_without_retrying_or_following(self):
        for position in range(3):
            for status in (301, 302, 307, 401, 403, 404, 429, 500):
                self.responses = self.build_responses()
                self.responses[position] = RefereeResponse({"message": self.secret}, status=status)
                self.calls.clear()
                with self.subTest(position=position, status=status), self.assertRaises(RefereeCheckError):
                    self.run_check()
                self.assertEqual(len(self.calls), position + 1)
                self.assertTrue(self.closed)

    def test_network_failures_have_sanitized_diagnostics_and_no_retry(self):
        for position in range(3):
            for failure in (requests.ConnectionError, requests.Timeout, requests.exceptions.SSLError):
                self.responses = self.build_responses()
                self.responses[position] = failure(self.secret)
                self.calls.clear()
                with self.subTest(position=position, failure=failure), self.assertRaises(RefereeCheckError) as caught:
                    self.run_check()
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(len(self.calls), position + 1)
                self.assertTrue(self.closed)

    def test_invalid_unicode_json_and_duplicate_fields_are_sanitized(self):
        for body in (
            self.secret.encode(), b"\xff", b"{",
            json.dumps({"id": self.bot_id, "bot": True}).encode()[:-1] + b',"bot":true}',
            b"[" * 1500 + b"]" * 1500,
        ):
            self.responses = [RefereeResponse(data=body)]
            self.calls.clear()
            with self.subTest(body=body[:25]), self.assertRaises(RefereeCheckError):
                self.run_check()
            self.assertEqual(len(self.calls), 1)

    def test_response_over_one_mebibyte_is_rejected(self):
        response = RefereeResponse(data=b" " * 1048577)
        self.responses = [response]
        with self.assertRaisesRegex(RefereeCheckError, "size or time limit"):
            self.run_check()
        self.assertTrue(response.closed)
        self.assertEqual(len(self.calls), 1)

    def test_response_at_one_mebibyte_is_accepted(self):
        document = json.dumps({"id": self.bot_id, "bot": True}).encode()
        self.responses[0] = RefereeResponse(data=document + b" " * (1048576 - len(document)))
        self.assertEqual(len(self.run_check()), 1)

    def test_elapsed_limit_applies_during_stream_and_after_empty_response(self):
        for data in (b"{}", b""):
            self.responses = [RefereeResponse(data=data)]
            self.calls.clear()
            with patch("staging.referee_check.time.monotonic", side_effect=[0, 31]):
                with self.assertRaisesRegex(RefereeCheckError, "size or time limit"):
                    self.run_check()
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(self.closed)

    def test_transport_blocks_unlisted_or_nonofficial_destinations(self):
        transport = RefereeTransport(self.guild_id, self.account_id)
        try:
            for url in (
                "https://example.invalid/" + self.secret,
                "http://discord.com/api/v10/users/@me",
                "https://discord.com/api/v10/users/@me?extra=value",
                f"https://discord.com/api/v10/guilds/{self.guild_id}/members/{self.bot_id}",
            ):
                with self.subTest(url=url), self.assertRaises(RefereeCheckError):
                    transport.get_json(url, self.secret)
            self.assertEqual(self.calls, [])
        finally:
            transport.close()

    def test_unexpected_session_or_transport_errors_do_not_expose_values(self):
        self.session_factory.side_effect = RuntimeError(self.secret)
        with self.assertRaises(RefereeCheckError) as caught:
            self.run_check()
        self.assertTrue(caught.exception.__suppress_context__)
        self.session_factory.side_effect = None
        self.responses = [RuntimeError(self.secret)]
        with self.assertRaises(RefereeCheckError):
            self.run_check()
        self.assertTrue(self.closed)

    def test_stream_errors_and_close_failures_are_sanitized(self):
        response = self.responses[0]
        with patch.object(response, "iter_content", side_effect=RuntimeError(self.secret)):
            with self.assertRaises(RefereeCheckError):
                self.run_check()
        self.assertTrue(response.closed)
        self.assertTrue(self.closed)
        self.responses = self.build_responses()
        with patch.object(self.session, "close", side_effect=RuntimeError(self.secret)):
            with self.assertRaises(RefereeCheckError) as caught:
                self.run_check()
        self.assertTrue(caught.exception.__suppress_context__)
