"""Exercises pure CORP Cup rules against independent match examples."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from corpoch.match_rules import (
    OpeningAction,
    build_corp_cup_selection,
    opening_actor,
    opening_choices,
    validate_corp_cup_profile,
    validate_opening_actions,
)


class CorpCupRulesTests(unittest.TestCase):
    """Checks rule behavior without a bot, database or screenshot service."""

    def setUp(self):
        """Loads hand-authored source actions and expected outcomes."""
        fixture_path = Path(__file__).parent / "fixtures" / "corp_cup_rules.json"
        self.fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.profiles = {
            profile["profile_id"]: profile for profile in self.fixtures["profiles"]
        }
        self.sequences = {
            sequence["sequence_id"]: sequence
            for sequence in self.fixtures["opening_sequences"]
        }
        self.player_ids = ("higher_seed", "lower_seed")
        self.chart_ids = tuple(self.profiles["group_stage"]["setlist_chart_ids"])

    def build_actions(self, sequence_id):
        """Converts one independent fixture to immutable rule inputs.

        :param str sequence_id: named fixture opening sequence"""
        return tuple(
            OpeningAction(action["num"], action["actor"], action["chart_id"], action["saved"])
            for action in self.sequences[sequence_id]["actions"]
        )

    def build_selection(self, played=(), winners=(), sequence_id="four_bans"):
        """Builds a group-stage selection from explicit completed rounds.

        :param tuple played: completed round chart IDs
        :param tuple winners: completed round winner IDs
        :param str sequence_id: opening fixture identifier"""
        return build_corp_cup_selection(
            self.player_ids, self.chart_ids, self.build_actions(sequence_id),
            played, winners, 7,
        )

    def test_confirmed_profiles_accept_targets_independently_of_effective_bans(self):
        for profile in self.profiles.values():
            with self.subTest(profile=profile["profile_id"]):
                validate_corp_cup_profile(
                    2, profile["num_rounds"], 2, "bansave", "loserpicks",
                    profile["setlist_size"],
                )

    def test_unsupported_profile_configurations_are_rejected(self):
        baseline = {
            "num_players": 2, "num_rounds": 7, "num_bans": 2,
            "ban_ruleset": "bansave", "pick_ruleset": "loserpicks",
            "setlist_size": 11, "defer": False,
        }
        for field, value in [
            ("num_players", 3), ("num_rounds", 8), ("num_rounds", 5),
            ("num_bans", 3), ("ban_ruleset", "default"),
            ("pick_ruleset", "alternate"), ("setlist_size", 13), ("defer", True),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_corp_cup_profile(**{**baseline, field: value})

    def test_all_opening_sequences_follow_higher_lower_lower_higher(self):
        for sequence_id in self.sequences:
            actions = self.build_actions(sequence_id)
            with self.subTest(sequence=sequence_id):
                validate_opening_actions(self.player_ids, self.chart_ids, actions)
                self.assertEqual(
                    [opening_actor(self.player_ids, actions[:index]) for index in range(5)],
                    ["higher_seed", "lower_seed", "lower_seed", "higher_seed", None],
                )

    def test_only_opponents_immediately_preceding_ban_can_be_saved(self):
        actions = self.build_actions("four_bans")
        self.assertEqual(opening_choices(self.player_ids, self.chart_ids, ())[1], ())
        self.assertEqual(opening_choices(self.player_ids, self.chart_ids, actions[:1])[1], ("a",))
        self.assertEqual(opening_choices(self.player_ids, self.chart_ids, actions[:2])[1], ())
        self.assertEqual(opening_choices(self.player_ids, self.chart_ids, actions[:3])[1], ("c",))
        self.assertEqual(opening_choices(self.player_ids, self.chart_ids, actions), ((), ()))
        for bad_actions in (
            (replace(actions[0], saved=True),),
            (actions[0], replace(actions[1], chart_id="c", saved=True)),
            (*actions[:2], replace(actions[2], saved=True)),
            (*actions[:3], replace(actions[3], chart_id="a", saved=True)),
            (actions[0], replace(actions[1], player_id="higher_seed", chart_id="a", saved=True)),
        ):
            with self.subTest(actions=bad_actions), self.assertRaises(ValueError):
                validate_opening_actions(self.player_ids, self.chart_ids, bad_actions)

    def test_saved_and_banned_charts_cannot_be_banned_again(self):
        for sequence_id in ("four_bans", "save_first_ban"):
            actions = self.build_actions(sequence_id)
            with self.subTest(sequence=sequence_id):
                ban_ids, save_ids = opening_choices(self.player_ids, self.chart_ids, actions[:2])
                self.assertNotIn("a", ban_ids)
                self.assertEqual(save_ids, ())
                with self.assertRaises(ValueError):
                    validate_opening_actions(
                        self.player_ids, self.chart_ids,
                        (*actions[:2], replace(actions[2], chart_id="a")),
                    )

    def test_no_fifth_opening_or_tiebreaker_ban_is_accepted(self):
        actions = (*self.build_actions("four_bans"), OpeningAction(4, "higher_seed", "k"))
        with self.assertRaises(ValueError):
            validate_opening_actions(self.player_ids, self.chart_ids, actions)
        with self.assertRaises(ValueError):
            opening_actor(self.player_ids, actions)

    def test_malformed_assignments_order_and_charts_are_rejected(self):
        actions = self.build_actions("four_bans")
        for players, charts, bad_actions in (
            (("higher_seed", None), self.chart_ids, actions),
            (("higher_seed", "higher_seed"), self.chart_ids, actions),
            (self.player_ids, (*self.chart_ids, "a"), actions),
            (self.player_ids, self.chart_ids, (replace(actions[0], num=1),)),
            (self.player_ids, self.chart_ids, (replace(actions[0], player_id="lower_seed"),)),
            (self.player_ids, self.chart_ids, (replace(actions[0], chart_id="outsider"),)),
            (self.player_ids, self.chart_ids, (replace(actions[0], saved=1),)),
        ):
            with self.subTest(actions=bad_actions), self.assertRaises(ValueError):
                validate_opening_actions(players, charts, bad_actions)

    def test_first_picker_is_higher_seed_for_all_save_patterns(self):
        for sequence_id in self.sequences:
            with self.subTest(sequence=sequence_id):
                selection = self.build_selection(sequence_id=sequence_id)
                self.assertEqual(selection.next_picker_id, "higher_seed")
                self.assertFalse(selection.is_tiebreaker)
                self.assertFalse(selection.complete)
                self.assertIsNone(selection.forced_chart_id)
                self.assertEqual(
                    set(selection.eligible_chart_ids),
                    set(self.chart_ids) - set(self.sequences[sequence_id]["expected"]["effective_ban_chart_ids"]),
                )

    def test_previous_song_loser_picks_including_consecutive_losses(self):
        for winners, expected_picker in (
            (("higher_seed",), "lower_seed"),
            (("lower_seed",), "higher_seed"),
            (("higher_seed", "higher_seed"), "lower_seed"),
            (("higher_seed", "lower_seed"), "higher_seed"),
        ):
            with self.subTest(winners=winners):
                selection = self.build_selection(("e", "f")[:len(winners)], winners)
                self.assertEqual(selection.next_picker_id, expected_picker)

    def test_corrected_winner_and_removed_round_change_next_picker(self):
        original = self.build_selection(("e", "f"), ("higher_seed", "lower_seed"))
        corrected = self.build_selection(("e", "f"), ("higher_seed", "higher_seed"))
        removed = self.build_selection(("e",), ("higher_seed",))
        self.assertEqual(original.next_picker_id, "higher_seed")
        self.assertEqual(corrected.next_picker_id, "lower_seed")
        self.assertEqual(removed.next_picker_id, "lower_seed")
        self.assertNotIn("f", corrected.eligible_chart_ids)
        self.assertIn("f", removed.eligible_chart_ids)

    def test_tiebreaker_candidates_and_picker_match_independent_examples(self):
        for case in self.fixtures["tiebreaker_cases"]:
            with self.subTest(case=case["case_id"]):
                profile = self.profiles[case["profile_id"]]
                expected = case["expected"]
                selection = build_corp_cup_selection(
                    self.player_ids, tuple(profile["setlist_chart_ids"]),
                    self.build_actions(case["opening_sequence_id"]),
                    tuple(case["played_chart_ids"]), tuple(case["recorded_winners"]),
                    profile["num_rounds"],
                )
                self.assertTrue(selection.is_tiebreaker)
                self.assertFalse(selection.complete)
                self.assertEqual(selection.next_picker_id, expected["pick_actor"])
                self.assertEqual(selection.eligible_chart_ids, tuple(expected["eligible_pick_chart_ids"]))
                self.assertEqual(selection.forced_chart_id, expected["forced_chart_id"])

    def test_recorded_match_win_ends_selection_without_screenshots_or_export(self):
        for winners in (("higher_seed",) * 4, ("lower_seed",) * 4):
            with self.subTest(winners=winners):
                selection = self.build_selection(("e", "f", "g", "h"), winners)
                self.assertTrue(selection.complete)
                self.assertFalse(selection.is_tiebreaker)
                self.assertEqual(selection.eligible_chart_ids, ())
                self.assertIsNone(selection.next_picker_id)

    def test_ineligible_saved_chart_cannot_be_recorded_as_completed_tiebreaker(self):
        winners = ("higher_seed", "lower_seed") * 3 + ("higher_seed",)
        with self.assertRaises(ValueError):
            self.build_selection(
                ("e", "f", "g", "h", "i", "j", "a"), winners,
                sequence_id="save_both_bans",
            )
        selection = self.build_selection(
            ("e", "f", "g", "h", "i", "j", "b"), winners,
            sequence_id="save_both_bans",
        )
        self.assertTrue(selection.complete)

    def test_invalid_completed_round_history_is_rejected(self):
        for played, winners in (
            (("e",), ()),
            (("e",), (None,)),
            (("e",), ("outsider",)),
            (("outsider",), ("higher_seed",)),
            (("a",), ("higher_seed",)),
            (("e", "e"), ("higher_seed", "lower_seed")),
            (("e", "f", "g", "h", "i"), ("higher_seed",) * 4 + ("lower_seed",)),
        ):
            with self.subTest(played=played, winners=winners), self.assertRaises(ValueError):
                self.build_selection(played, winners)

    def test_no_chart_selection_before_opening_actions_complete(self):
        with self.assertRaises(ValueError):
            build_corp_cup_selection(
                self.player_ids, self.chart_ids, self.build_actions("four_bans")[:3],
                (), (), 7,
            )
