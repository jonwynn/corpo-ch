"""Tests the pure presentation under the guarded application test bootstrap."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from corpoch.match_viewer import build_match_presentation


class MatchPresentationTests(unittest.TestCase):
    """Verifies independent fixture answers and presentation safety boundaries."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "match_viewer_cases.json").read_text(encoding="utf-8"),
        )
        cls.cases = {case["case_id"]: case for case in cls.fixture["cases"]}

    def create_source(self, case_id="approved_first_pick"):
        """
        Copies an independent fixture for one isolated assertion

        :param str case_id: Fixture case identifier
        :return: Mutable source copy"""
        return deepcopy(self.cases[case_id]["source"])

    def test_all_hand_authored_presentation_answers(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["case_id"]):
                result = build_match_presentation(case["source"], case["request"]["pins"])
                hidden_ids = set(case["expected"]["hidden_chart_ids"])
                for key, expected in case["expected"].items():
                    if key == "hidden_chart_ids":
                        self.assertEqual(result["hidden_chart_count"], len(hidden_ids))
                    elif key == "effective_ban_chart_ids":
                        self.assertEqual(result[key], [item for item in expected if item not in hidden_ids])
                    else:
                        self.assertEqual(result[key], expected, key)

    def test_projection_does_not_mutate_input_or_pins(self):
        case = deepcopy(self.cases["pinned_seed_correction"])
        original = deepcopy(case)
        build_match_presentation(case["source"], case["request"]["pins"])
        self.assertEqual(case, original)

    def test_account_and_private_source_data_never_reach_output(self):
        source = self.create_source()
        source["private_configuration"] = "DO_NOT_PUBLISH_CONFIGURATION"
        source["players"][0]["aliases"] = ["DO_NOT_PUBLISH_ALIAS"]
        source["rounds"][0]["screenshot_url"] = "https://invalid.example/private-image"
        source["rounds"][0]["steg"] = {"token": "DO_NOT_PUBLISH_RAW_METADATA"}
        source["context"] = {"tournament": "Example Cup", "sheet_url": "DO_NOT_PUBLISH_SHEET"}
        output = json.dumps(build_match_presentation(source))
        for private_value in (
            "user-a", "user-b", "DO_NOT_PUBLISH", "private-image", "screenshot_url", '"steg"',
        ):
            self.assertNotIn(private_value, output)

    def test_unrevealed_titles_and_ids_are_absent_from_every_output_field(self):
        source = self.create_source("unrevealed_selected_chart")
        result = build_match_presentation(source)
        output = json.dumps(result)
        for record in source["actions"] + source["rounds"]:
            self.assertNotIn(record["chart_title"], output)
            self.assertNotIn(record["chart_id"], output)
        self.assertNotIn("hidden_chart_ids", result)
        self.assertEqual(result["rounds"][0]["chart"], {"visibility": "withheld", "label": "Chart withheld"})

    def test_hidden_title_or_id_edits_do_not_change_public_digest(self):
        source = self.create_source("unrevealed_selected_chart")
        before = build_match_presentation(source)
        source["rounds"][0]["chart_title"] = "Another private title"
        source["rounds"][0]["chart_id"] = "another-private-id"
        after = build_match_presentation(source)
        self.assertEqual(before, after)

    def test_record_visibility_can_withhold_a_chart_in_a_revealed_bracket(self):
        source = self.create_source()
        source["rounds"][0]["chart_visible"] = False
        result = build_match_presentation(source)
        self.assertEqual(result["hidden_chart_count"], 1)
        self.assertNotIn("Unwritten", json.dumps(result))
        self.assertNotIn("unwritten", json.dumps(result))

    def test_withheld_choice_also_redacts_its_opening_ban_and_save(self):
        source = self.create_source()
        source["rounds"][0]["chart_visible"] = False
        for index in (0, 1):
            source["actions"][index].update({"chart_id": "unwritten", "chart_title": "Unwritten"})
        source["actions"][1]["saved"] = True
        original = deepcopy(source)
        result = build_match_presentation(source)
        self.assertEqual(source, original)
        self.assertEqual(result["hidden_chart_count"], 1)
        self.assertNotIn("Unwritten", json.dumps(result))
        self.assertNotIn("unwritten", json.dumps(result))
        self.assertTrue(all(item["chart"]["visibility"] == "withheld" for item in result["actions"][:2]))
        for record in source["actions"][:2] + source["rounds"]:
            record["chart_title"] = "A different hidden title"
        self.assertEqual(result["digest"], build_match_presentation(source)["digest"])

    def test_access_denial_contains_no_match_or_participant_data(self):
        for change in (
            {"authenticated": False}, {"active": False, "superuser": True},
            {"same_guild_role": "other_guild_referee", "is_staff": True, "assigned_referee": True},
            {"same_guild_role": None, "is_staff": True, "assigned_referee": True},
            {"same_guild_role": {"role": "admin"}},
        ):
            with self.subTest(change=change):
                source = self.create_source()
                source["access"].update(change)
                result = build_match_presentation(source)
                self.assertEqual(result["access"], "denied")
                self.assertEqual(set(result), {"contract_version", "access", "state", "quality"})

    def test_only_active_superuser_can_bypass_membership(self):
        source = self.create_source()
        source["access"].update({"same_guild_role": None, "superuser": True})
        self.assertEqual(build_match_presentation(source)["access"], "allowed")
        source["access"]["active"] = False
        self.assertEqual(build_match_presentation(source)["access"], "denied")

    def test_contextual_pins_survive_seed_changes_but_not_group_or_reversal(self):
        source = self.create_source()
        source["group_id"] = "group-a"
        pins = build_match_presentation(source)["assignment"]
        source["players"][0]["seed"] = 8
        result = build_match_presentation(source, pins)
        self.assertEqual(result["slots"], ["player-a", "player-b"])
        self.assertEqual(result["players"][0]["seed"], 8)
        for key, value in (("group_id", "group-b"), ("rev_seeds", True)):
            with self.subTest(key=key):
                changed = deepcopy(source)
                changed[key] = value
                result = build_match_presentation(changed, pins)
                self.assertEqual(result["state"], "setup_changed")
                self.assertIsNone(result["wins"])
                self.assertIsNone(result["slots"])

    def test_changed_player_link_and_invalid_pins_do_not_silently_recolor(self):
        source = self.create_source()
        pins = build_match_presentation(source)["assignment"]
        source["players"][0]["player_id"] = "replacement-player"
        result = build_match_presentation(source, pins)
        self.assertEqual(result["state"], "setup_changed")
        self.assertIsNone(result["slots"])
        self.assertEqual(build_match_presentation(source, "invalid")["state"], "setup_changed")

    def test_names_are_unparsed_text_and_missing_alias_is_explicit(self):
        source = self.create_source()
        source["players"][0]["name"] = "<script>not executable</script>"
        result = build_match_presentation(source)
        self.assertEqual(result["players"][0]["name"], source["players"][0]["name"])
        self.assertEqual(result["players"][1]["name"], "[LOS] Biscuit")
        self.assertIsNone(result["players"][1]["overline"])
        source["players"][0]["name"] = {"unexpected": "configuration"}
        self.assertEqual(build_match_presentation(source)["players"][0]["name"], "Name unavailable")

    def test_distinct_players_with_same_name_keep_separate_scores(self):
        source = self.create_source("approved_second_pick")
        for player in source["players"]:
            player["name"] = "Same Name"
        result = build_match_presentation(source)
        self.assertEqual(result["wins"], [0, 1])
        self.assertEqual(result["round_history"][0]["winner_slot"], "p2")

    def test_numeric_tied_seed_order_is_deterministic_and_flagged(self):
        source = self.create_source("approved_opening")
        source["players"][0].update({"seed": 3, "seed_id": "10"})
        source["players"][1].update({"seed": 3, "seed_id": "2"})
        result = build_match_presentation(source)
        self.assertEqual(result["slots"], ["player-b", "player-a"])
        self.assertEqual(result["state"], "needs_review")
        self.assertIn("tied_seeds", result["quality"])

    def test_unapproved_production_profile_cannot_inherit_fixture_support(self):
        source = self.create_source()
        source["rules"].pop("fixture_only")
        result = build_match_presentation(source)
        self.assertEqual(result["state"], "unsupported_rules")
        self.assertIsNone(result["target"])
        source["rules"].update({
            "profile_supported": True,
            "tb_ruleset": "corp_cup",
            "pick_ruleset": "loserpicks",
            "ban_ruleset": "bansave",
            "num_bans": 2,
        })
        self.assertEqual(build_match_presentation(source)["target"], 4)

    def test_null_and_malformed_rule_values_are_unavailable(self):
        for key, value in (("num_rounds", True), ("num_rounds", []), ("pick_ruleset", {}), ("num_bans", None)):
            with self.subTest(key=key):
                source = self.create_source()
                source["rules"][key] = value
                result = build_match_presentation(source)
                self.assertIsNone(result["target"])
                self.assertIn("unsupported_rules", result["quality"])

    def test_malformed_rounds_cannot_turn_a_valid_score_into_zero(self):
        for rounds in (None, [{}], [{"round_id": "bad", "num": [], "winner_id": "player-a"}]):
            with self.subTest(rounds=rounds):
                source = self.create_source("approved_second_pick")
                source["rounds"] = rounds
                result = build_match_presentation(source)
                self.assertEqual(result["state"], "needs_review")
                self.assertIsNone(result["wins"])

    def test_missing_collection_is_not_a_confirmed_empty_history(self):
        for field in ("rounds", "actions"):
            with self.subTest(field=field):
                source = self.create_source("approved_second_pick")
                source.pop(field)
                result = build_match_presentation(source)
                self.assertEqual(result["state"], "needs_review")
                self.assertIn(f"invalid_{field}", result["quality"])
                if field == "rounds":
                    self.assertIsNone(result["wins"])

    def test_malformed_non_null_round_identity_cannot_erase_result(self):
        for field in ("winner_id", "picked_id", "chart_id"):
            for value in ({"id": "player-b"}, [], True, ""):
                with self.subTest(field=field, value=value):
                    source = self.create_source("approved_second_pick")
                    source["rounds"][0][field] = value
                    result = build_match_presentation(source)
                    self.assertEqual(result["state"], "needs_review")
                    self.assertIsNone(result["wins"])
                    self.assertIn("invalid_rounds_identity", result["quality"])

    def test_malformed_non_null_action_identity_is_not_unassigned_history(self):
        source = self.create_source()
        source["actions"][0]["player_id"] = {"id": "player-a"}
        result = build_match_presentation(source)
        self.assertEqual(result["state"], "needs_review")
        self.assertIn("invalid_actions_identity", result["quality"])

    def test_gapped_round_numbers_withhold_an_ambiguous_total(self):
        source = self.create_source("approved_second_pick")
        source["rounds"][1]["num"] = 3
        result = build_match_presentation(source)
        self.assertEqual(result["state"], "needs_review")
        self.assertIsNone(result["wins"])
        self.assertIn("noncontiguous_round_numbers", result["quality"])
        self.assertEqual([item["number"] for item in result["round_history"]], [1, 3])

    def test_pending_earlier_round_flags_review_while_retaining_known_points(self):
        source = self.create_source("approved_second_pick")
        source["rounds"][0]["winner_id"] = None
        source["rounds"][1]["winner_id"] = "player-b"
        result = build_match_presentation(source)
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["wins"], [0, 1])
        self.assertIn("nonfinal_pending_round", result["quality"])

    def test_round_after_decisive_result_requires_review(self):
        for winner in (None, "player-b"):
            with self.subTest(winner=winner):
                source = self.create_source("complete_before_screenshots")
                extra = deepcopy(source["rounds"][0])
                extra.update({"round_id": "round-5", "num": 5, "winner_id": winner})
                source["rounds"].append(extra)
                result = build_match_presentation(source)
                self.assertEqual(result["state"], "needs_review")
                self.assertEqual(result["wins"], [4, int(winner is not None)])
                self.assertIn("round_after_decisive_result", result["quality"])

    def test_unassigned_action_owner_remains_explicit_in_details(self):
        for owner in (None, "outsider"):
            with self.subTest(owner=owner):
                source = self.create_source()
                source["actions"][0]["player_id"] = owner
                result = build_match_presentation(source)
                self.assertIn("action_owner_unavailable", result["quality"])
                self.assertIsNone(result["actions"][0]["player_slot"])
                self.assertEqual(result["actions"][0]["player_label"], "Player unavailable")

    def test_confirmed_corp_loser_picks_snapshots_keep_recorded_choice(self):
        for winner, picker, expected_latest in (
            ("player-a", "player-b", ["round-1", "round-2"]),
            ("player-b", "player-a", ["round-2", None]),
        ):
            with self.subTest(winner=winner):
                source = self.create_source("approved_second_pick")
                source["rules"].update({
                    "fixture_only": False,
                    "profile_supported": True,
                    "tb_ruleset": "corp_cup",
                    "pick_ruleset": "loserpicks",
                    "ban_ruleset": "bansave",
                    "num_bans": 2,
                })
                source["actions"] = source["actions"][:4]
                source["actions"][2]["player_id"] = "player-b"
                source["actions"][3]["player_id"] = "player-a"
                source["rounds"][0]["winner_id"] = winner
                source["rounds"][1]["picked_id"] = picker
                result = build_match_presentation(source)
                self.assertEqual(result["state"], "awaiting_result")
                self.assertEqual(result["latest_picks"], expected_latest)
                self.assertEqual(result["wins"], [int(winner == "player-a"), int(winner == "player-b")])
                self.assertEqual(result["rounds"][1]["picker_slot"], "p1" if picker == "player-a" else "p2")

    def test_deleted_chart_preserves_valid_winner_but_requires_review(self):
        source = self.create_source("deleted_selected_chart")
        source["rounds"][0]["winner_id"] = "player-b"
        result = build_match_presentation(source)
        self.assertEqual(result["wins"], [0, 1])
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["round_history"][0]["winner_slot"], "p2")
        self.assertEqual(result["rounds"][0]["chart"]["label"], "Chart unavailable")

    def test_result_corrections_replace_prior_score_and_digest(self):
        source = self.create_source("approved_second_pick")
        original = build_match_presentation(source)
        source["rounds"][0]["winner_id"] = None
        corrected = build_match_presentation(source)
        self.assertEqual(corrected["wins"], [0, 0])
        self.assertNotEqual(original["digest"], corrected["digest"])

    def test_evidence_and_export_never_override_official_results(self):
        source = self.create_source("complete_before_screenshots")
        original = build_match_presentation(source)
        for record in source["rounds"]:
            record.update({"screenshot_present": True, "metadata_kind": "dummy"})
        source["lifecycle"].update({"finished": True, "submitted": True})
        result = build_match_presentation(source)
        self.assertEqual(result["wins"], original["wins"])
        self.assertEqual(result["state"], "complete")
        self.assertTrue(result["status"]["export_recorded"])
        self.assertTrue(all(not item["verified"] for item in result["evidence"]))

    def test_contradictory_lifecycle_flags_are_not_presented_as_complete(self):
        source = self.create_source()
        source["lifecycle"]["finished"] = True
        result = build_match_presentation(source)
        self.assertEqual(result["state"], "needs_review")
        self.assertIn("lifecycle_inconsistent", result["quality"])

    def test_extra_fields_and_read_time_do_not_change_digest(self):
        source = self.create_source()
        original = build_match_presentation(source)
        source.update({"read_time": "2099-01-01T00:00:00Z", "unused": "ignored"})
        self.assertEqual(original["digest"], build_match_presentation(source)["digest"])
