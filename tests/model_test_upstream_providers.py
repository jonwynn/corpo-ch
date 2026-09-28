"""Runs production provider methods with a mocked Sheets boundary and no services."""

import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Literal, Union
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from django.conf import settings

from corpoch.models import Chart, Match, QualifierSubmission, Tournament, TournamentPlayer
from corpoch.types import CH_DIFFICULTIES


def load_provider_class(name):
    """
    Executes the unchanged class definition without importing service modules

    :param str name: Class to select from the production source
    :return: Production class in an isolated test namespace"""
    path = Path(__file__).parents[1] / "corpoch" / "providers.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    namespace = {
        "gspread": SimpleNamespace(Worksheet=object), "settings": settings,
        "Match": Match, "QualifierSubmission": QualifierSubmission,
        "Tournament": Tournament, "TournamentPlayer": TournamentPlayer,
        "Chart": Chart, "Union": Union, "Literal": Literal,
        "CH_DIFFICULTIES": CH_DIFFICULTIES, "EncoreClient": Mock,
    }
    exec(compile(ast.Module(body=[selected], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class FakeSpreadsheet:
    """Models atomic range replacement separately from a stale Worksheet cache."""

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.calls = []
        self.metadata_reads = []
        self.reject_batch = False

    def get_worksheet_by_id(self, identifier):
        self.metadata_reads.append(identifier)
        return SimpleNamespace(id=identifier, row_count=len(self.rows))

    def batch_update(self, body):
        self.calls.append(deepcopy(body))
        if self.reject_batch:
            raise RuntimeError("Fixture request rejected")
        updated = deepcopy(self.rows)
        for request in body["requests"]:
            if "appendDimension" in request:
                dimension = request["appendDimension"]
                assert dimension == {"sheetId": 42, "dimension": "ROWS", "length": dimension["length"]}
                assert dimension["length"] > 0
                updated.extend([[""] * 5 for number in range(dimension["length"])])
                continue
            update = request["updateCells"]
            assert update["fields"] == "userEnteredValue"
            assert update["range"] == {"sheetId": 42, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4}
            for row in updated[1:]:
                row[:4] = [""] * 4
            for index, row in enumerate(update["rows"], 1):
                assert all(set(cell["userEnteredValue"]) == {"stringValue"} for cell in row["values"])
                updated[index][:4] = [cell["userEnteredValue"]["stringValue"] for cell in row["values"]]
        self.rows = updated


class UpstreamProviderTests(TestCase):
    def setUp(self):
        self.provider_type = load_provider_class("GSheets")
        self.provider = self.provider_type()

    def create_roster_sheet(self, row_count=5, cached_rows=None):
        rows = [["Group", "CH name", "Discord name", "Discord ID", "Header note"]]
        rows += [[f"old-{index}", "Old name", "Old Discord", "old-id", f"note-{index}"] for index in range(1, row_count)]
        sheet = FakeSpreadsheet(rows)
        self.provider._sheet = sheet
        self.provider._ws = SimpleNamespace(id=42, row_count=cached_rows if cached_rows is not None else row_count)
        return sheet

    def submit_roster(self, rows):
        with patch.object(self.provider_type, "player_lines", new_callable=PropertyMock, return_value=rows):
            self.provider.submit_players()

    def test_qualifier_create_and_final_sheet_include_all_fifteen_columns(self):
        for final in (False, True):
            with self.subTest(final=final):
                self.provider._final = final
                self.provider._submission = SimpleNamespace(qualifier="Fixture Qualifier")
                self.provider._url = "https://viewer.invalid/fixture"
                self.provider._sheet = Mock()
                worksheet = self.provider.setup_qualifier_sheet()
                self.assertEqual(self.provider._sheet.add_worksheet.call_args.kwargs["cols"], 15)
                self.assertGreater(self.provider._sheet.add_worksheet.call_args.kwargs["rows"], 1)
                header, target = worksheet.update.call_args.args
                self.assertEqual(target, "A1:O1")
                self.assertEqual(len(header[0]), 15)
                self.assertEqual(header[0][-1], "Game Version")
                worksheet.format.assert_called_once_with("A1:O1", self.provider._format_header)
                worksheet.freeze.assert_called_once_with(1)

    def test_qualifier_update_covers_actual_game_version_column(self):
        timestamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        player = SimpleNamespace(score=100, notes_missed=2, notes_hit=98, is_fc=False,
            gamepad_mode=False, excess_hits=1, frets_ghosted=0, sp_phrases_earned=4)
        self.provider._submission = SimpleNamespace(
            id="fixture-submission", display_profile_name="Player", player=SimpleNamespace(name="Discord"),
            steg=SimpleNamespace(players=[player], score_timestamp=timestamp), submit_time=timestamp,
            screenshot=SimpleNamespace(url="/fixture.png"),
            qualifier=SimpleNamespace(tournament=SimpleNamespace(config=SimpleNamespace(version="fixture-version"))),
        )
        self.provider._ws = Mock()
        self.provider._ws.find.return_value = SimpleNamespace(row=7)
        self.provider.update_qualifier()
        values, target = self.provider._ws.update.call_args.args
        self.assertEqual(target, "A7:O7")
        self.assertEqual(len(values[0]), 15)
        self.assertEqual(values[0][-1], "fixture-version")
        self.assertIn('", "Screenshot Link")', values[0][-2])
        self.assertEqual(self.provider._ws.update.call_args.kwargs, {"raw": False})

    def test_roster_replacement_clears_tail_preserves_header_and_unrelated_columns(self):
        sheet = self.create_roster_sheet()
        header = deepcopy(sheet.rows[0])
        notes = [row[4] for row in sheet.rows]
        self.submit_roster([["A", "P1", "D1", "'123"], ["B", "P2", "D2", "'456"]])
        self.assertEqual(sheet.rows[0], header)
        self.assertEqual([row[4] for row in sheet.rows], notes)
        self.assertEqual(sheet.rows[1][:4], ["A", "P1", "D1", "123"])
        self.assertEqual(sheet.rows[2][:4], ["B", "P2", "D2", "456"])
        self.assertEqual([row[:4] for row in sheet.rows[3:]], [[""] * 4, [""] * 4])
        self.assertEqual(len(sheet.calls), 1)
        self.assertEqual(len(sheet.calls[0]["requests"]), 1)

    def test_repeated_growing_roster_uses_fresh_dimensions_despite_stale_cache(self):
        sheet = self.create_roster_sheet(row_count=2)
        roster = [["A", f"Player {number}", "Discord", str(number)] for number in range(4)]
        self.submit_roster(roster)
        first = deepcopy(sheet.rows)
        self.submit_roster(roster)
        self.assertEqual(sheet.rows, first)
        self.assertEqual(len(sheet.rows), 5)
        self.assertEqual(self.provider._ws.row_count, 2)
        self.assertEqual(sheet.metadata_reads, [42, 42])
        self.assertEqual(sheet.calls[0]["requests"][0], {"appendDimension": {"sheetId": 42, "dimension": "ROWS", "length": 3}})
        self.assertEqual(len(sheet.calls[1]["requests"]), 1)

    def test_roster_shrink_and_empty_clear_owned_cells_without_deleting_rows(self):
        sheet = self.create_roster_sheet()
        self.submit_roster([["A", "Only player", "Discord", "123"]])
        self.assertEqual([row[:4] for row in sheet.rows[2:]], [[""] * 4] * 3)
        self.submit_roster([])
        self.assertEqual([row[:4] for row in sheet.rows[1:]], [[""] * 4] * 4)
        self.assertEqual(len(sheet.rows), 5)
        self.assertEqual(sheet.rows[0][0], "Group")
        self.assertEqual(sheet.rows[-1][-1], "note-4")

    def test_shortened_server_grid_is_grown_using_fresh_metadata(self):
        sheet = self.create_roster_sheet(row_count=2, cached_rows=100)
        self.submit_roster([["A", "P1", "D1", "1"], ["B", "P2", "D2", "2"]])
        self.assertEqual(len(sheet.rows), 3)
        self.assertEqual(sheet.calls[0]["requests"][0]["appendDimension"]["length"], 1)

    def test_actual_player_lines_preserve_large_ids_and_formula_like_names_as_text(self):
        submission = Mock(spec=Tournament)
        seed = SimpleNamespace(
            player_ch_name="=SUM(1,2)",
            player=SimpleNamespace(name="+Discord", user=SimpleNamespace(id=1234567890123456789)),
        )
        group = Mock()
        group.__str__ = Mock(return_value="Fixture Group")
        group.seeding.all.return_value = [seed]
        bracket = Mock()
        bracket.groups.all.return_value = [group]
        submission.brackets.all.return_value = [bracket]
        self.provider._submission = submission
        sheet = self.create_roster_sheet()
        self.provider.submit_players()
        self.assertEqual(sheet.rows[1][:4], ["Fixture Group", "=SUM(1,2)", "+Discord", "1234567890123456789"])
        self.assertIsInstance(sheet.rows[1][3], str)

    def test_failed_batch_leaves_mock_server_roster_unchanged_and_propagates_failure(self):
        sheet = self.create_roster_sheet()
        before = deepcopy(sheet.rows)
        sheet.reject_batch = True
        with self.assertRaisesRegex(RuntimeError, "Fixture request rejected"):
            self.submit_roster([["A", "P1", "D1", "1"]])
        self.assertEqual(sheet.rows, before)

    def test_invalid_roster_shape_never_sends_a_write(self):
        sheet = self.create_roster_sheet()
        with self.assertRaises(ValueError):
            self.submit_roster([["A", "missing columns"]])
        self.assertEqual(sheet.calls, [])

    def test_completed_export_retains_existing_freeze_behavior(self):
        self.provider._submission = SimpleNamespace(tournament=SimpleNamespace(short_name="FIX"))
        self.provider._url = "https://viewer.invalid/fixture"
        self.provider._sheet = Mock()
        worksheet = self.provider.setup_completed_sheet()
        worksheet.freeze.assert_called_once_with(1, 17)

    def test_completed_export_keeps_existing_hyperlink_separator(self):
        stats = SimpleNamespace(
            profile_name="Winner", score=100, notes_missed=0, notes_hit=100, is_fc=True,
            gamepad_mode=False, excess_hits=0, frets_ghosted=0, sp_phrases_earned=4,
        )
        winner = SimpleNamespace(ch_name="Winner", check_ch_name=lambda name: name == "Winner")
        round_record = SimpleNamespace(
            steg=SimpleNamespace(players=[stats]), picked=winner, winner=winner,
            loser=SimpleNamespace(ch_name="Loser"), chart=SimpleNamespace(tournament_name="Song"),
            created=datetime(2026, 1, 2, tzinfo=timezone.utc),
            screenshot=SimpleNamespace(url="/fixture.png"),
        )
        self.provider._submission = SimpleNamespace(
            id="fixture-match", bracket="Bracket", group="A", short_name_no_seeds="Winner vs Loser",
            rounds=[round_record],
        )
        lines = self.provider.completed_lines
        self.assertEqual(len(lines[0]), 18)
        self.assertEqual(lines[0][-1], '=HYPERLINK("https://viewer.invalid/fixture.png", "Screenshot Link")')

    def test_match_export_does_not_bypass_guarded_local_publication(self):
        self.provider._submission = Mock(spec=Match)
        self.provider._submission.submitted = False
        self.provider._ws = Mock()
        self.provider._switch_match_sheet = Mock()
        with patch.object(self.provider_type, "completed_lines", new_callable=PropertyMock, return_value=[["match"]]), patch.object(
            self.provider_type, "ban_lines", new_callable=PropertyMock, return_value=[["ban"]],
        ):
            self.provider.submit_completed()
        self.assertFalse(self.provider._submission.submitted)
        self.provider._submission.save.assert_not_called()

    def test_match_correction_preserves_saved_bans_and_existing_row_locations(self):
        for ban_ruleset, last_column in (("bansave", "G"), ("default", "F")):
            with self.subTest(ban_ruleset=ban_ruleset):
                winner = SimpleNamespace(ch_name="Winner", check_ch_name=lambda name: name == "Winner")
                loser = SimpleNamespace(ch_name="Loser")
                player_stats = [
                    SimpleNamespace(
                        profile_name=name, score=score, notes_missed=2, notes_hit=98,
                        is_fc=False, gamepad_mode=False, excess_hits=1,
                        frets_ghosted=0, sp_phrases_earned=4,
                    )
                    for name, score in (("Winner", 200), ("Loser", 100))
                ]
                round_record = SimpleNamespace(
                    steg=SimpleNamespace(players=player_stats), picked=loser,
                    winner=winner, loser=loser,
                    chart=SimpleNamespace(tournament_name="Selected Song"),
                    created=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    screenshot=SimpleNamespace(url="/fixture.png"),
                )
                bracket = Mock()
                bracket.__str__ = Mock(return_value="Fixture Bracket")
                bracket.ruleset = SimpleNamespace(ban_ruleset=ban_ruleset)
                bans = Mock()
                bans.all.return_value = [
                    SimpleNamespace(
                        player=winner, chart=SimpleNamespace(tournament_name="Saved Song"), saved=True,
                    ),
                    SimpleNamespace(
                        player=loser, chart=SimpleNamespace(tournament_name="Banned Song"), saved=False,
                    ),
                ]
                self.provider._submission = SimpleNamespace(
                    id="fixture-match", bracket=bracket, group="A",
                    short_name_no_seeds="Winner vs Loser", rounds=[round_record],
                    match_bans=bans, tournament=SimpleNamespace(short_name="FIX"),
                )
                match_worksheet = Mock(title="FIX - Match Data")
                match_worksheet.find.return_value = SimpleNamespace(row=7)
                bans_worksheet = Mock(title="FIX - Bans Data")
                bans_worksheet.find.return_value = SimpleNamespace(row=12)
                self.provider._sheet = Mock()
                self.provider._sheet.worksheet.return_value = bans_worksheet
                self.provider._ws = match_worksheet

                self.provider.update_match()

                match_worksheet.find.assert_called_once_with("fixture-match")
                bans_worksheet.find.assert_called_once_with("fixture-match")
                self.provider._sheet.worksheet.assert_called_once_with("FIX - Bans Data")
                match_calls = match_worksheet.update.call_args_list
                self.assertEqual([request.args[1] for request in match_calls], ["A7:R7", "A8:R8"])
                self.assertEqual([len(request.args[0][0]) for request in match_calls], [18, 18])
                self.assertEqual(
                    [request.args[0][0][6:9] for request in match_calls],
                    [["Winner", 200, "W"], ["Loser", 100, "L"]],
                )
                ban_calls = bans_worksheet.update.call_args_list
                self.assertEqual(
                    [request.args[1] for request in ban_calls],
                    [f"A12:{last_column}12", f"A13:{last_column}13"],
                )
                expected_rows = [
                    ["fixture-match", "Fixture Bracket", "A", "Winner vs Loser", "Winner", "Saved Song"],
                    ["fixture-match", "Fixture Bracket", "A", "Winner vs Loser", "Loser", "Banned Song"],
                ]
                if ban_ruleset == "bansave":
                    expected_rows[0].append(True)
                    expected_rows[1].append(False)
                self.assertEqual([request.args[0] for request in ban_calls], [[row] for row in expected_rows])
                self.assertTrue(all(request.kwargs == {"raw": False} for request in match_calls + ban_calls))
                match_worksheet.append_rows.assert_not_called()
                bans_worksheet.append_rows.assert_not_called()

    def test_hydra_helper_is_loaded_only_when_analysis_is_requested(self):
        hydra_type = load_provider_class("Hydra")
        provider = hydra_type()
        real_import = __import__

        def import_without_hydra(name, *args, **kwargs):
            if name == "corpoch.utils.hydra.hydra.hyutil":
                raise ModuleNotFoundError("Fixture missing helper", name="corpoch.utils.hydra")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_hydra):
            with self.assertRaisesRegex(RuntimeError, "Initialize the Hydra submodule"):
                provider.gen_path(None)
        self.assertNotIn("corpoch.providers", sys.modules)
