"""Checks the hand-authored viewer examples before production code consumes them.

These checks validate fixture integrity and independent answer keys. They do not
exercise Django, a presenter, HTTP, a browser, database writes or sporting rules.
"""

import copy
import json
import unittest
from pathlib import Path


class MatchViewerContractTests(unittest.TestCase):
    """Checks the source records and their independently specified answers."""

    def setUp(self):
        """Loads only the local JSON fixture; no application imports are needed."""
        fixture_path = Path(__file__).parent / "fixtures" / "match_viewer_cases.json"
        self.fixture_document = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.cases = {
            case["case_id"]: case for case in self.fixture_document["cases"]
        }
        self.scenarios = {
            scenario["scenario_id"]: scenario
            for scenario in self.fixture_document["scenario_examples"]
        }

    def validate_case(self, case):
        """Checks cross-references and arithmetic in a fixture answer key.

        :param dict case: hand-authored source records and expected values"""
        source = case["source"]
        expected = case["expected"]
        self.assertEqual(
            set(expected),
            {
                "panel_mode", "state", "slots", "wins", "target", "remaining",
                "current_round", "round_history", "opening_action_ids",
                "effective_ban_chart_ids", "latest_picks", "quality", "access",
                "hidden_chart_ids",
            },
        )
        self.assertTrue(case["description"])
        self.assertTrue(case["family_ids"])
        rounds = source["rounds"]
        round_ids = [item["round_id"] for item in rounds]
        self.assertEqual(len(round_ids), len(set(round_ids)))
        self.assertEqual(
            [item["round_id"] for item in expected["round_history"]],
            round_ids,
        )
        self.assertEqual(
            [item["number"] for item in expected["round_history"]],
            [item["num"] for item in rounds],
        )
        if expected["current_round"] is not None:
            self.assertIn(expected["current_round"], round_ids)
        action_ids = [item["action_id"] for item in source["actions"]]
        self.assertEqual(len(action_ids), len(set(action_ids)))
        self.assertEqual(
            expected["opening_action_ids"],
            [
                item["action_id"] for item in source["actions"]
                if item["action_phase"] == "opening"
            ],
        )
        for action in source["actions"]:
            self.assertIn(action["action_phase"], {"unknown", "opening", "tiebreaker"})
            self.assertIsInstance(action["saved"], bool)
        if not rounds and any(
            action["action_phase"] == "tiebreaker" for action in source["actions"]
        ):
            self.assertEqual(expected["state"], "needs_review")
            self.assertIn("tiebreaker_provenance_inconsistent", expected["quality"])
        for recorded_round in rounds:
            self.assertIn(
                recorded_round["selection_kind"],
                {"unknown", "player", "referee", "automatic"},
            )
            if recorded_round["chart_id"] is None:
                self.assertIsNone(recorded_round["chart_title"])
        if expected["state"] in {"awaiting_selection", "awaiting_next_round"}:
            current_round = next(
                item for item in rounds
                if item["round_id"] == expected["current_round"]
            )
            if expected["state"] == "awaiting_selection":
                self.assertIsNone(current_round["chart_id"])
                self.assertIsNone(current_round["winner_id"])
            else:
                self.assertIsNotNone(current_round["chart_id"])
                self.assertIsNotNone(current_round["winner_id"])
                self.assertEqual(current_round["round_id"], rounds[-1]["round_id"])

        slots = expected["slots"]
        if slots is None:
            self.assertIsNone(expected["wins"])
            self.assertIsNone(expected["remaining"])
            self.assertIsNone(expected["latest_picks"])
        else:
            self.assertEqual(len(slots), 2)
            self.assertEqual(len(set(slots)), 2)
            self.assertNotIn(None, slots)
            self.assertEqual(
                set(slots),
                {player["player_id"] for player in source["players"]},
            )
            for slot_index, round_id in enumerate(expected["latest_picks"]):
                if round_id is None:
                    continue
                chosen_round = next(item for item in rounds if item["round_id"] == round_id)
                self.assertEqual(chosen_round["selection_kind"], "player")
                self.assertEqual(chosen_round["picked_id"], slots[slot_index])

        wins = expected["wins"]
        if wins is not None:
            self.assertEqual(len(wins), 2)
            self.assertTrue(all(type(value) is int and value >= 0 for value in wins))
            winners = [item["winner_id"] for item in rounds if item["winner_id"] is not None]
            self.assertTrue(all(winner in slots for winner in winners))
            self.assertEqual(wins, [winners.count(slots[0]), winners.count(slots[1])])
            for recorded_round, history_item in zip(rounds, expected["round_history"]):
                winner_id = recorded_round["winner_id"]
                winner_slot = None if winner_id is None else f"p{slots.index(winner_id) + 1}"
                self.assertEqual(history_item["winner_slot"], winner_slot)
        else:
            self.assertTrue(expected["quality"])

        target = expected["target"]
        if target is not None:
            self.assertGreater(target, 0)
            self.assertEqual(target, (source["rules"]["num_rounds"] + 1) // 2)
        remaining = expected["remaining"]
        if wins is None or target is None:
            self.assertIsNone(remaining)
        else:
            self.assertEqual(remaining, [max(target - value, 0) for value in wins])

    def test_case_identifiers_and_family_coverage(self):
        """Keeps important cases visible instead of silently dropping a family."""
        self.assertEqual(len(self.cases), len(self.fixture_document["cases"]))
        self.assertEqual(len(self.scenarios), len(self.fixture_document["scenario_examples"]))
        family_ids = {
            family_id
            for example in [*self.cases.values(), *self.scenarios.values()]
            for family_id in example["family_ids"]
        }
        self.assertEqual(family_ids, {f"V{number:02d}" for number in range(1, 16)})
        self.assertEqual(self.fixture_document["contract_version"], "1.0.0")

    def test_all_fixture_cross_references_and_answer_arithmetic(self):
        for case_id, case in self.cases.items():
            with self.subTest(case_id=case_id):
                self.validate_case(case)

    def test_approved_states_and_ban_quota_checkpoint(self):
        expected_states = [
            ("approved_opening", 4, 0, "initial_actions", [0, 0]),
            ("completed_ban_quota_blank_round", 6, 1, "initial_actions", [0, 0]),
            ("approved_first_pick", 6, 1, "latest_picks", [0, 0]),
            ("approved_second_pick", 6, 2, "latest_picks", [0, 1]),
        ]
        for case_id, action_count, round_count, panel_mode, wins in expected_states:
            with self.subTest(case_id=case_id):
                case = self.cases[case_id]
                self.assertEqual(len(case["source"]["actions"]), action_count)
                self.assertEqual(len(case["source"]["rounds"]), round_count)
                self.assertEqual(case["source"]["rules"]["num_bans"], 3)
                self.assertEqual(case["source"]["rules"]["pick_ruleset"], "alternate")
                self.assertEqual(case["expected"]["panel_mode"], panel_mode)
                self.assertEqual(case["expected"]["wins"], wins)
                self.assertEqual(case["expected"]["target"], 4)
        first = self.cases["approved_first_pick"]
        self.assertEqual(first["source"]["rounds"][0]["chart_title"], "Unwritten")
        second = self.cases["approved_second_pick"]
        self.assertEqual(second["source"]["rounds"][0]["winner_id"], "player-b")
        self.assertEqual(second["source"]["rounds"][1]["picked_id"], "player-b")
        self.assertEqual(second["source"]["rounds"][1]["chart_title"], "Trinity")

    def test_no_fixture_approves_a_live_sporting_profile(self):
        for case in self.cases.values():
            rules = case["source"]["rules"]
            if rules is not None:
                self.assertTrue(rules["fixture_only"])
                self.assertFalse(rules["live_profile_approved"])

    def test_fixture_rule_identifiers_use_verified_existing_values(self):
        """Checks source vocabulary without executing sporting-rule behavior."""
        for case in self.cases.values():
            with self.subTest(case_id=case["case_id"]):
                self.assertIsInstance(case["source"]["defer"], bool)
                rules = case["source"]["rules"]
                if rules is None:
                    continue
                self.assertIn(rules["ban_ruleset"], {"default", "deferban", "deferboth", "bansave"})
                self.assertIn(rules["pick_ruleset"], {"alternate", "loserpicks"})
                self.assertIn(rules["tb_ruleset"], {"single", "csc", "banpick", "refdecide", "bansave"})

    def test_known_targets_are_independent_of_ban_quota(self):
        for case_id, target in [
            ("target_best_of_3", 2),
            ("target_best_of_5", 3),
            ("approved_opening", 4),
            ("target_best_of_9", 5),
        ]:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.cases[case_id]["expected"]["target"], target)
                self.assertEqual(self.cases[case_id]["source"]["rules"]["num_bans"], 3)
        self.assertIsNone(self.cases["unsupported_even_target"]["expected"]["target"])
        self.assertIsNone(self.cases["missing_rules"]["expected"]["target"])

    def test_missing_data_is_distinct_from_known_zero(self):
        self.assertEqual(self.cases["approved_opening"]["expected"]["wins"], [0, 0])
        for case_id in ["missing_participant", "null_player_relationship", "outsider_winner"]:
            with self.subTest(case_id=case_id):
                self.assertIsNone(self.cases[case_id]["expected"]["wins"])
        self.assertEqual(self.cases["participant_without_account"]["expected"]["wins"], [0, 0])

    def test_reversed_slots_and_pins_preserve_identity(self):
        reversed_case = self.cases["reversed_initial_slots"]
        self.assertEqual(reversed_case["expected"]["slots"], ["player-b", "player-a"])
        self.assertEqual(reversed_case["expected"]["wins"], [1, 0])
        pinned = self.cases["pinned_seed_correction"]
        self.assertGreater(
            pinned["source"]["players"][0]["seed"],
            pinned["source"]["players"][1]["seed"],
        )
        self.assertEqual(pinned["expected"]["slots"], ["player-a", "player-b"])
        self.assertEqual(
            [pin["player_id"] for pin in pinned["request"]["pins"]],
            pinned["expected"]["slots"],
        )
        self.assertEqual(
            self.cases["defer_does_not_swap_slots"]["expected"]["slots"],
            ["player-a", "player-b"],
        )

    def test_optional_label_is_not_inferred_from_name(self):
        for case in self.cases.values():
            for player in case["source"]["players"]:
                self.assertNotIn("overline", player)
                self.assertNotIn("clan", player)
        self.assertEqual(
            self.cases["approved_opening"]["source"]["players"][1]["name"],
            "[LOS] Biscuit",
        )

    def test_initial_action_history_and_effective_bans_are_distinct(self):
        for case in self.cases.values():
            with self.subTest(case_id=case["case_id"]):
                actions = case["source"]["actions"]
                saved_charts = {action["chart_id"] for action in actions if action["saved"]}
                effective_charts = {
                    action["chart_id"] for action in actions
                    if action["chart_id"] is not None and action["chart_id"] not in saved_charts
                }
                self.assertEqual(
                    set(case["expected"]["effective_ban_chart_ids"]),
                    effective_charts,
                )
        saved = self.cases["saved_duplicate_bans_and_tiebreaker"]
        self.assertEqual(saved["expected"]["effective_ban_chart_ids"], ["opus"])
        self.assertIn("save-1", saved["expected"]["opening_action_ids"])
        self.assertNotIn("tb-1", saved["expected"]["opening_action_ids"])
        self.assertEqual(saved["source"]["rounds"], [])
        self.assertEqual(saved["expected"]["state"], "needs_review")
        self.assertIn("tiebreaker_provenance_inconsistent", saved["expected"]["quality"])
        self.assertEqual(
            self.cases["legacy_unknown_ban_phase"]["expected"]["opening_action_ids"],
            [],
        )

    def test_neutral_and_unknown_picks_are_not_personal_picks(self):
        for case_id in ["referee_selection", "automatic_selection", "legacy_unknown_picker"]:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.cases[case_id]["expected"]["latest_picks"], [None, None])
                self.assertEqual(self.cases[case_id]["expected"]["panel_mode"], "latest_picks")
                recorded_round = self.cases[case_id]["source"]["rounds"][0]
                self.assertEqual(recorded_round["picked_id"], "player-a")
                self.assertNotEqual(recorded_round["selection_kind"], "player")
        self.assertEqual(
            self.cases["blank_next_round_retains_latest_pick"]["expected"]["latest_picks"],
            ["round-1", None],
        )

    def test_deleted_chart_and_intentional_undo_have_different_answers(self):
        deleted = self.cases["deleted_selected_chart"]
        undone = self.cases["undo_only_chart_selection"]
        self.assertIsNone(deleted["source"]["rounds"][0]["chart_id"])
        self.assertIsNone(undone["source"]["rounds"][0]["chart_id"])
        self.assertEqual(deleted["source"]["rounds"][0]["selection_kind"], "player")
        self.assertEqual(undone["source"]["rounds"][0]["selection_kind"], "unknown")
        self.assertEqual(deleted["expected"]["panel_mode"], "latest_picks")
        self.assertEqual(undone["expected"]["panel_mode"], "initial_actions")

    def test_corrections_change_the_answer_without_append_only_history(self):
        self.assertEqual(self.cases["corrected_first_winner"]["expected"]["wins"], [1, 0])
        self.assertEqual(self.cases["undo_recorded_winner"]["expected"]["wins"], [0, 0])
        removed = self.cases["undo_pending_round"]["expected"]
        self.assertEqual(removed["latest_picks"], ["round-1", None])
        self.assertEqual([item["round_id"] for item in removed["round_history"]], ["round-1"])
        self.assertEqual(removed["state"], "awaiting_next_round")
        self.assertEqual(
            self.cases["blank_next_round_retains_latest_pick"]["expected"]["state"],
            "awaiting_selection",
        )

    def test_completion_does_not_depend_on_screenshots_or_export(self):
        awaiting = self.cases["target_reached_not_finalized"]
        complete = self.cases["complete_before_screenshots"]
        finished = self.cases["evidence_finished_not_exported"]
        self.assertEqual(awaiting["expected"]["state"], "awaiting_finalization")
        self.assertEqual(complete["expected"]["state"], "complete")
        self.assertFalse(complete["source"]["lifecycle"]["finished"])
        self.assertTrue(finished["source"]["lifecycle"]["finished"])
        self.assertFalse(finished["source"]["lifecycle"]["submitted"])
        self.assertEqual(complete["expected"]["wins"], [4, 0])
        self.assertTrue(
            all(not item["screenshot_present"] for item in complete["source"]["rounds"]),
        )

    def test_staff_access_answer_matrix_includes_inactive_accounts(self):
        matrix = self.scenarios["staff_access_matrix"]
        for example in matrix["examples"]:
            with self.subTest(example=example):
                permitted = (
                    example["authenticated"]
                    and example["active"]
                    and (
                        example["superuser"]
                        or example["same_guild_role"] in {"admin", "referee"}
                    )
                )
                self.assertEqual(example["allowed"], permitted)
        self.assertTrue(matrix["expected"]["denial_clears_protected_content"])
        self.assertTrue(matrix["expected"]["check_every_request"])

    def test_hidden_titles_are_marked_for_every_selected_bracket_reference(self):
        case = self.cases["unrevealed_selected_chart"]
        referenced_charts = {
            item["chart_id"]
            for item in [*case["source"]["actions"], *case["source"]["rounds"]]
            if item["chart_id"] is not None
        }
        self.assertEqual(set(case["expected"]["hidden_chart_ids"]), referenced_charts)
        self.assertFalse(case["source"]["bracket_revealed"])
        self.assertEqual(case["expected"]["access"], "allowed")

    def test_screenshot_examples_do_not_supply_gameplay_metrics(self):
        scenario = self.scenarios["evidence_presence_only"]
        self.assertFalse(scenario["expected"]["publish_gameplay_metrics"])
        self.assertIsNone(scenario["expected"]["missing_metric_value"])
        self.assertFalse(scenario["expected"]["change_official_wins"])
        self.assertFalse(scenario["expected"]["presence_proves_file_readable"])
        self.assertEqual(self.cases["dummy_screenshot_metadata"]["expected"]["wins"], [0, 0])
        for case in self.cases.values():
            self.assertNotIn("accuracy", case["expected"])
            self.assertNotIn("combo", case["expected"])

    def test_refresh_examples_preserve_data_and_accept_corrections(self):
        initial = self.scenarios["initial_refresh_failure"]["expected"]
        failed = self.scenarios["failed_refresh_keeps_score"]["expected"]
        recovered = self.scenarios["correction_after_reconnect"]["expected"]
        self.assertIsNone(initial["visible_wins"])
        self.assertEqual(failed["visible_wins"], [2, 1])
        self.assertFalse(failed["replace_match"])
        self.assertEqual(recovered["visible_wins"], [1, 1])
        self.assertTrue(recovered["replace_match"])
        self.assertFalse(
            self.scenarios["late_response_from_old_match"]["expected"]["install_response"],
        )

    def test_refresh_timing_examples_do_not_stale_flash_on_normal_completed_poll(self):
        refresh = self.fixture_document["defaults"]["refresh"]
        self.assertEqual(refresh["active_interval_seconds"], 2)
        self.assertEqual(refresh["completed_interval_seconds"], 10)
        self.assertEqual(refresh["request_timeout_seconds"], 8)
        self.assertEqual(refresh["active_stale_after_seconds"], 10)
        self.assertEqual(refresh["completed_stale_after_seconds"], 20)
        self.assertEqual(refresh["retry_delay_seconds"], [2, 4, 8, 10])
        self.assertEqual(refresh["max_in_flight"], 1)
        self.assertGreater(
            refresh["completed_stale_after_seconds"],
            refresh["completed_interval_seconds"],
        )
        self.assertFalse(self.scenarios["completed_cadence"]["expected"]["stale"])

    def test_visual_and_accessibility_examples_remain_explicit_future_requirements(self):
        text_example = self.scenarios["long_escaped_names"]
        self.assertIn(320, text_example["input"]["viewport_widths"])
        self.assertEqual(text_example["input"]["zoom_percent"], 200)
        self.assertFalse(text_example["expected"]["interpret_markup"])
        self.assertFalse(text_example["expected"]["horizontal_page_overflow"])
        details = self.scenarios["details_survive_refresh"]
        self.assertTrue(details["expected"]["details_open"])
        self.assertEqual(details["expected"]["detail_rows"], details["input"]["new_detail_rows"])
        theme = self.scenarios["saved_theme_and_forced_colors"]["expected"]
        self.assertFalse(theme["override_forced_colors"])
        self.assertFalse(theme["color_only_identity"])

    def test_integrity_check_rejects_points_awarded_for_pending_pick(self):
        case = copy.deepcopy(self.cases["approved_first_pick"])
        case["expected"]["wins"] = [1, 0]
        case["expected"]["remaining"] = [3, 4]
        with self.assertRaises(AssertionError):
            self.validate_case(case)

    def test_integrity_check_rejects_unknown_phase_as_opening_history(self):
        case = copy.deepcopy(self.cases["legacy_unknown_ban_phase"])
        case["expected"]["opening_action_ids"] = ["ban-1"]
        with self.assertRaises(AssertionError):
            self.validate_case(case)

    def test_integrity_check_rejects_stale_history_after_round_removal(self):
        case = copy.deepcopy(self.cases["undo_pending_round"])
        case["expected"]["round_history"].append(
            {"round_id": "round-2", "number": 2, "state": "awaiting_result", "winner_slot": None},
        )
        with self.assertRaises(AssertionError):
            self.validate_case(case)

    def test_integrity_check_rejects_automatic_selection_as_player_pick(self):
        case = copy.deepcopy(self.cases["automatic_selection"])
        case["expected"]["latest_picks"] = ["round-1", None]
        with self.assertRaises(AssertionError):
            self.validate_case(case)

    def test_integrity_check_rejects_tiebreaker_action_as_ordinary_opening(self):
        case = copy.deepcopy(self.cases["saved_duplicate_bans_and_tiebreaker"])
        case["expected"]["state"] = "opening_bans"
        case["expected"]["quality"] = []
        with self.assertRaises(AssertionError):
            self.validate_case(case)

    def test_integrity_check_rejects_missing_next_round_as_blank_selection(self):
        case = copy.deepcopy(self.cases["undo_pending_round"])
        case["expected"]["state"] = "awaiting_selection"
        with self.assertRaises(AssertionError):
            self.validate_case(case)


class CorpCupRulesContractTests(unittest.TestCase):
    """Checks confirmed rule examples without importing sporting-rule code."""

    def setUp(self):
        """Loads the separate CORP corpus, retaining the generic visual cases."""
        fixture_path = Path(__file__).parent / "fixtures" / "corp_cup_rules.json"
        self.fixture_document = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.profiles = {
            profile["profile_id"]: profile
            for profile in self.fixture_document["profiles"]
        }
        self.sequences = {
            sequence["sequence_id"]: sequence
            for sequence in self.fixture_document["opening_sequences"]
        }
        self.cases = {
            case["case_id"]: case
            for case in self.fixture_document["tiebreaker_cases"]
        }

    def validate_tiebreaker_case(self, case):
        """Checks arithmetic and references in a hand-authored tiebreaker answer.

        :param dict case: source examples and independent expected answer"""
        profile = self.profiles[case["profile_id"]]
        opening = self.sequences[case["opening_sequence_id"]]["expected"]
        expected = case["expected"]
        participants = ["higher_seed", "lower_seed"]
        played_charts = case["played_chart_ids"]
        winners = case["recorded_winners"]
        self.assertEqual(len(played_charts), profile["num_rounds"] - 1)
        self.assertEqual(len(played_charts), len(set(played_charts)))
        self.assertEqual(len(winners), len(played_charts))
        self.assertTrue(set(winners).issubset(participants))
        self.assertEqual(
            expected["score"],
            [winners.count(participant) for participant in participants],
        )
        self.assertEqual(expected["score"], [profile["target"] - 1] * 2)
        self.assertTrue(set(played_charts).issubset(profile["setlist_chart_ids"]))
        self.assertFalse(set(played_charts) & set(opening["effective_ban_chart_ids"]))
        remaining = set(profile["setlist_chart_ids"]) - set(played_charts)
        remaining -= set(opening["effective_ban_chart_ids"])
        self.assertEqual(set(expected["raw_remaining_chart_ids"]), remaining)
        self.assertEqual(expected["raw_remaining_count"], len(remaining))
        self.assertEqual(
            expected["raw_remaining_count"],
            5 - opening["effective_ban_count"],
        )
        unplayed_saves = set(opening["saved_chart_ids"]) - set(played_charts)
        self.assertEqual(set(expected["unplayed_saved_chart_ids"]), unplayed_saves)
        self.assertNotIn("tiebreaker_ban_chart_id", case)
        self.assertIsNone(expected["ban_actor"])
        self.assertNotIn("eligible_ban_chart_ids", expected)
        self.assertNotIn("raw_after_ban_chart_ids", expected)
        if len(remaining) == 1:
            self.assertEqual(expected["selection_kind"], "automatic")
            self.assertIn(expected["forced_chart_id"], remaining)
            self.assertIsNone(expected["pick_actor"])
        else:
            self.assertEqual(expected["selection_kind"], "player")
            self.assertEqual(
                expected["pick_actor"],
                next(player for player in participants if player != winners[-1]),
            )
            self.assertIsNone(expected["forced_chart_id"])
        pickable = remaining - unplayed_saves if len(unplayed_saves) == 2 else remaining
        self.assertEqual(set(expected["eligible_pick_chart_ids"]), pickable)

    def test_numeric_profiles_and_proposed_action_quota(self):
        self.assertEqual(set(self.profiles), {"group_stage", "playoffs"})
        for profile_id, setlist_size, best_of, target in [
            ("group_stage", 11, 7, 4),
            ("playoffs", 13, 9, 5),
        ]:
            with self.subTest(profile_id=profile_id):
                profile = self.profiles[profile_id]
                self.assertEqual(profile["setlist_size"], setlist_size)
                self.assertEqual(len(set(profile["setlist_chart_ids"])), setlist_size)
                self.assertEqual(profile["num_players"], 2)
                self.assertEqual(profile["num_rounds"], best_of)
                self.assertEqual(profile["target"], target)
                self.assertEqual(profile["target"], (best_of + 1) // 2)
                self.assertEqual(profile["opening_action_count"], 4)
                mapping = profile["proposed_model_mapping"]
                self.assertEqual(mapping["num_bans"], 2)
                self.assertEqual(
                    profile["num_players"] * mapping["num_bans"],
                    profile["opening_action_count"],
                )
                self.assertEqual(mapping["ban_ruleset"], "bansave")
                self.assertEqual(mapping["tb_ruleset"], "corp_cup")
                self.assertEqual(mapping["pick_ruleset"], "loserpicks")

    def test_four_opening_action_sequences_and_effective_bans(self):
        self.assertEqual(len(self.sequences), 4)
        save_patterns = set()
        for sequence in self.sequences.values():
            with self.subTest(sequence_id=sequence["sequence_id"]):
                actions = sequence["actions"]
                expected = sequence["expected"]
                self.assertEqual([action["num"] for action in actions], [0, 1, 2, 3])
                self.assertEqual(
                    [action["actor"] for action in actions],
                    ["higher_seed", "lower_seed", "lower_seed", "higher_seed"],
                )
                self.assertFalse(actions[0]["saved"])
                self.assertFalse(actions[2]["saved"])
                save_patterns.add((actions[1]["saved"], actions[3]["saved"]))
                banned_charts = set()
                saved_charts = set()
                for index, action in enumerate(actions):
                    if action["saved"]:
                        self.assertIn(index, {1, 3})
                        previous = actions[index - 1]
                        self.assertNotEqual(action["actor"], previous["actor"])
                        self.assertEqual(action["chart_id"], previous["chart_id"])
                        saved_charts.add(action["chart_id"])
                    else:
                        self.assertNotIn(action["chart_id"], banned_charts)
                        self.assertNotIn(action["chart_id"], saved_charts)
                        banned_charts.add(action["chart_id"])
                self.assertEqual(set(expected["saved_chart_ids"]), saved_charts)
                self.assertEqual(
                    set(expected["effective_ban_chart_ids"]),
                    banned_charts - saved_charts,
                )
                self.assertEqual(
                    expected["effective_ban_count"],
                    4 - 2 * len(saved_charts),
                )
        self.assertEqual(
            save_patterns,
            {(False, False), (False, True), (True, False), (True, True)},
        )

    def test_tiebreaker_case_integrity_and_coverage(self):
        self.assertEqual(len(self.cases), len(self.fixture_document["tiebreaker_cases"]))
        for case in self.cases.values():
            with self.subTest(case_id=case["case_id"]):
                self.validate_tiebreaker_case(case)
        for profile_id in self.profiles:
            self.assertEqual(
                {
                    case["expected"]["raw_remaining_count"]
                    for case in self.cases.values()
                    if case["profile_id"] == profile_id
                },
                {1, 3, 5},
            )

    def test_two_remaining_saves_are_excluded_from_direct_tiebreaker_choices(self):
        for case_id in ["group_no_bans_two_saved", "playoffs_no_bans_two_saved"]:
            with self.subTest(case_id=case_id):
                expected = self.cases[case_id]["expected"]
                self.assertEqual(expected["raw_remaining_count"], 5)
                self.assertIsNone(expected["ban_actor"])
                self.assertEqual(len(expected["eligible_pick_chart_ids"]), 3)
                for saved_chart in expected["unplayed_saved_chart_ids"]:
                    self.assertIn(saved_chart, expected["raw_remaining_chart_ids"])
                    self.assertNotIn(saved_chart, expected["eligible_pick_chart_ids"])

    def test_played_saves_do_not_count_as_remaining_saved_charts(self):
        one_remaining = self.cases["group_no_bans_one_save_already_played"]["expected"]
        none_remaining = self.cases["group_no_bans_both_saves_already_played"]["expected"]
        self.assertEqual(one_remaining["unplayed_saved_chart_ids"], ["c"])
        self.assertIn("c", one_remaining["eligible_pick_chart_ids"])
        self.assertEqual(none_remaining["unplayed_saved_chart_ids"], [])
        self.assertEqual(len(one_remaining["eligible_pick_chart_ids"]), 5)
        self.assertEqual(len(none_remaining["eligible_pick_chart_ids"]), 5)

    def test_confirmed_pick_order_preserves_referee_decisions_for_tied_scores(self):
        confirmation = self.fixture_document["confirmation"]
        self.assertEqual(confirmation["status"], "match_flow_confirmed")
        self.assertFalse(confirmation["live_profile_approved"])
        self.assertFalse(confirmation["implementation_verified"])
        self.assertEqual(confirmation["ordinary_first_picker"], "higher_seed")
        self.assertEqual(confirmation["ordinary_later_picker"], "previous_song_loser")
        self.assertIsNone(confirmation["tied_song_score_resolution"])
        self.assertFalse(confirmation["tiebreaker_extra_ban_allowed"])
        self.assertEqual(
            confirmation["tiebreaker_picker"],
            "previous_song_loser_when_multiple_eligible_charts",
        )
        self.assertFalse(confirmation["csc_reference_is_existing_csc_tiebreaker_mode"])
        self.assertFalse(confirmation["defer_allowed"])
        self.assertFalse(confirmation["opening_saved_chart_reban_allowed"])
        self.assertFalse(confirmation["save_own_ban_allowed"])

    def test_integrity_rejects_saved_charts_in_two_save_tiebreaker_choices(self):
        case = copy.deepcopy(self.cases["group_no_bans_two_saved"])
        case["expected"]["eligible_pick_chart_ids"] = ["a", "b", "c", "d", "k"]
        with self.assertRaises(AssertionError):
            self.validate_tiebreaker_case(case)

    def test_integrity_rejects_adding_a_tiebreaker_ban(self):
        case = copy.deepcopy(self.cases["group_two_bans_one_saved"])
        case["tiebreaker_ban_chart_id"] = "b"
        case["expected"]["ban_actor"] = "lower_seed"
        with self.assertRaises(AssertionError):
            self.validate_tiebreaker_case(case)


if __name__ == "__main__":
    unittest.main()
