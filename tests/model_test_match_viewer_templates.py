"""Verifies deterministic viewer markup under the guarded Django bootstrap."""

from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from django.template.loader import render_to_string

from corpoch.match_viewer import build_match_presentation


class RenderedHtml(HTMLParser):
    """Collects rendered elements and text without a browser or external I/O."""

    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.open_elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        for parent in self.open_elements:
            parent["text"] += " "
        element = {"tag": tag, "attrs": dict(attrs), "text": ""}
        self.elements.append(element)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self.open_elements.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.open_elements) - 1, -1, -1):
            if self.open_elements[index]["tag"] == tag:
                del self.open_elements[index:]
                break

    def handle_data(self, data):
        for element in self.open_elements:
            element["text"] += data

    def find(self, attribute, value):
        """
        Selects elements by an exact attribute

        :param str attribute: Attribute name
        :param str value: Attribute value
        :return: Matching elements"""
        return [element for element in self.elements if element["attrs"].get(attribute) == value]

    def text_for(self, attribute, value):
        """
        Reads normalized visible text from the first matching element

        :param str attribute: Attribute name
        :param str value: Attribute value
        :return: Normalized text"""
        return " ".join(self.find(attribute, value)[0]["text"].split())


class MatchViewerTemplateTests(unittest.TestCase):
    """Checks approved states, privacy, accessibility hooks and unavailable data."""

    @classmethod
    def setUpClass(cls):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "match_viewer_cases.json").read_text(encoding="utf-8"),
        )
        cls.cases = {case["case_id"]: case for case in fixture["cases"]}

    def render_case(self, case_id, **context):
        """
        Renders a fixture through the real presenter and Django template

        :param str case_id: Hand-authored fixture identifier
        :param dict context: Additional presentation-only template context
        :return: Rendered markup"""
        case = self.cases[case_id]
        viewer = build_match_presentation(case["source"], case["request"]["pins"])
        return render_to_string("match_viewer/state.html", {"viewer": viewer, **context})

    def test_all_fixture_states_render_once_without_duplicate_ids(self):
        for case_id in self.cases:
            with self.subTest(case=case_id):
                document = RenderedHtml(self.render_case(case_id))
                identifiers = [element["attrs"]["id"] for element in document.elements if "id" in element["attrs"]]
                self.assertEqual(len(identifiers), len(set(identifiers)))
                self.assertEqual(len(document.find("id", "match-viewer-state")), 1)
                self.assertEqual(len(document.find("id", "match-viewer-details")), 1)

    def test_approved_initial_state_shows_both_players_and_actions(self):
        markup = self.render_case("approved_opening")
        document = RenderedHtml(markup)
        self.assertEqual(document.text_for("id", "match-viewer-name-p1"), "GSBeast")
        self.assertEqual(document.text_for("id", "match-viewer-name-p2"), "[LOS] Biscuit")
        self.assertIn("Magnolia", document.text_for("data-player-slot", "p1"))
        self.assertIn("Pacesetter", document.text_for("data-player-slot", "p1"))
        self.assertIn("Opus", document.text_for("data-player-slot", "p2"))
        self.assertIn("Vanguard", document.text_for("data-player-slot", "p2"))
        self.assertEqual(document.text_for("data-score-slot", "p1"), "0")
        self.assertEqual(document.text_for("data-score-slot", "p2"), "0")
        self.assertIn("First to 4", markup)
        self.assertIn("Each player needs 4 more wins", markup)
        self.assertEqual(document.text_for("id", "match-viewer-current-title"), "Waiting for the first song selection")
        self.assertIn("No rounds recorded", markup)

    def test_blank_first_round_keeps_opening_panels(self):
        document = RenderedHtml(self.render_case("completed_ban_quota_blank_round"))
        self.assertIn("Initial bans and saves", document.text_for("data-player-slot", "p1"))
        self.assertIn("Awaiting selection", document.text_for("data-round-id", "round-1"))
        self.assertEqual(document.text_for("data-score-slot", "p1"), "0")

    def test_first_pick_switches_both_panels_without_a_point(self):
        document = RenderedHtml(self.render_case("approved_first_pick"))
        self.assertIn("Latest pick Unwritten Round 1", document.text_for("data-player-slot", "p1"))
        self.assertIn("Latest pick No pick recorded", document.text_for("data-player-slot", "p2"))
        self.assertEqual(document.text_for("data-score-slot", "p1"), "0")
        self.assertEqual(document.text_for("data-score-slot", "p2"), "0")
        self.assertIn("Pending", document.text_for("data-round-id", "round-1"))

    def test_later_generic_visual_state_shows_winner_and_pending_round(self):
        document = RenderedHtml(self.render_case("approved_second_pick"))
        self.assertEqual(document.text_for("data-score-slot", "p1"), "0")
        self.assertEqual(document.text_for("data-score-slot", "p2"), "1")
        self.assertIn("P2 [LOS] Biscuit", document.text_for("data-round-id", "round-1"))
        self.assertIn("Pending", document.text_for("data-round-id", "round-2"))
        self.assertEqual(document.text_for("id", "match-viewer-current-title"), "Trinity")
        self.assertIn("Ban and save history", document.text_for("id", "match-viewer-details"))
        self.assertIn("Glass Castle", document.text_for("id", "match-viewer-details"))

    def test_unknown_score_is_an_em_dash_not_an_invented_zero(self):
        markup = self.render_case("outsider_winner")
        document = RenderedHtml(markup)
        self.assertEqual(document.text_for("data-score-slot", "p1"), "—")
        self.assertEqual(document.text_for("data-score-slot", "p2"), "—")
        self.assertIn("Score unavailable", markup)
        self.assertIn("Recorded match needs review", markup)

    def test_names_titles_and_metadata_attributes_are_escaped(self):
        source = deepcopy(self.cases["approved_first_pick"]["source"])
        source["players"][0]["name"] = '<script>alert("name")</script>'
        source["rounds"][0]["chart_title"] = '<img src=x onerror="run()">'
        source["match_id"] = 'match" onload="run()'
        markup = render_to_string("match_viewer/state.html", {"viewer": build_match_presentation(source)})
        document = RenderedHtml(markup)
        self.assertFalse(any(element["tag"] in {"script", "img"} for element in document.elements))
        self.assertFalse(any("onload" in element["attrs"] or "onerror" in element["attrs"] for element in document.elements))
        self.assertIn("&lt;script&gt;", markup)
        self.assertEqual(document.find("id", "match-viewer-state")[0]["attrs"]["data-match-id"], source["match_id"])

    def test_withheld_titles_never_reappear_in_rendered_details_or_attributes(self):
        markup = self.render_case("unrevealed_selected_chart", details_open=True)
        for record in self.cases["unrevealed_selected_chart"]["source"]["actions"]:
            self.assertNotIn(record["chart_title"], markup)
            self.assertNotIn(record["chart_id"], markup)
        self.assertNotIn("Unwritten", markup)
        self.assertNotIn("unwritten", markup)
        self.assertIn("Chart withheld", markup)

    def test_legacy_attribution_is_neutral_and_explained(self):
        document = RenderedHtml(self.render_case("legacy_unknown_picker"))
        self.assertIn("No pick recorded", document.text_for("data-player-slot", "p1"))
        self.assertIn("Picker unconfirmed", document.text_for("id", "match-viewer-state"))
        self.assertIn("confirmed chooser", document.text_for("id", "match-viewer-details"))

    def test_saved_actions_are_labeled_and_historical_phase_is_not_invented(self):
        saved = self.render_case("saved_duplicate_bans_and_tiebreaker")
        self.assertIn("Saved", saved)
        self.assertIn("Tiebreaker", saved)
        legacy = RenderedHtml(self.render_case("legacy_unknown_ban_phase"))
        self.assertIn("Recorded bans and saves", legacy.text_for("data-player-slot", "p1"))
        self.assertNotIn("Initial bans", legacy.text_for("data-player-slot", "p1"))

    def test_empty_player_action_list_is_explicit(self):
        source = deepcopy(self.cases["approved_opening"]["source"])
        source["actions"] = source["actions"][:1]
        markup = render_to_string("match_viewer/state.html", {"viewer": build_match_presentation(source)})
        document = RenderedHtml(markup)
        self.assertIn("No actions recorded", document.text_for("data-player-slot", "p2"))

    def test_completed_match_stays_visible_before_evidence_collection(self):
        markup = self.render_case("complete_before_screenshots")
        document = RenderedHtml(markup)
        self.assertEqual(document.text_for("data-score-slot", "p1"), "4")
        self.assertIn("GSBeast wins the match", document.text_for("id", "match-viewer-state"))
        self.assertIn("Not recorded as finished", document.text_for("id", "match-viewer-details"))
        self.assertIn("No file reference recorded", markup)

    def test_details_can_reopen_without_preserving_stale_contents(self):
        document = RenderedHtml(self.render_case("approved_second_pick", details_open=True))
        details = document.find("id", "match-viewer-details")[0]
        self.assertIn("open", details["attrs"])
        self.assertIn("data-preserve-open", details["attrs"])
        self.assertNotIn("hx-preserve", details["attrs"])
        self.assertEqual(document.find("id", "match-viewer-details-summary")[0]["tag"], "summary")

    def test_denial_or_unselected_state_has_no_score_or_protected_players(self):
        source = deepcopy(self.cases["approved_first_pick"]["source"])
        source["access"]["active"] = False
        for viewer in (None, build_match_presentation(source)):
            with self.subTest(viewer=viewer):
                markup = render_to_string("match_viewer/state.html", {"viewer": viewer})
                self.assertNotIn("GSBeast", markup)
                self.assertNotIn("data-score-slot", markup)
                self.assertNotIn("match-viewer-details", markup)

    def test_shell_uses_local_assets_and_default_dark_theme(self):
        markup = render_to_string("match_viewer/page.html", {
            "viewer": build_match_presentation(self.cases["approved_opening"]["source"]),
        })
        document = RenderedHtml(markup)
        body = [element for element in document.elements if element["tag"] == "body"][0]
        self.assertEqual(body["attrs"]["data-theme"], "dark")
        self.assertIn("corpoch/match_viewer.css", markup)
        self.assertIn("match-viewer-connection", markup)
        self.assertFalse(any(
            element["tag"] in {"script", "link", "img"}
            and any(value and value.startswith(("https://", "http://", "//")) for value in element["attrs"].values())
            for element in document.elements
        ))

    def test_saved_theme_context_selects_the_matching_option(self):
        for theme in ("light", "system"):
            with self.subTest(theme=theme):
                document = RenderedHtml(render_to_string("match_viewer/page.html", {"selected_theme": theme}))
                self.assertTrue(any(element["tag"] == "body" and element["attrs"]["data-theme"] == theme for element in document.elements))
                selected = [element for element in document.elements if element["tag"] == "option" and "selected" in element["attrs"]]
                self.assertEqual([element["attrs"]["value"] for element in selected], [theme])

    def test_production_shell_omits_design_annotations_and_fixture_controls(self):
        markup = render_to_string("match_viewer/page.html", {
            "viewer": build_match_presentation(self.cases["approved_opening"]["source"]),
        })
        for annotation in ("B · Center score", "Example target", "Static design preview", "Example match states", "live updates off"):
            self.assertNotIn(annotation, markup)

    def test_preview_navigation_is_separate_from_the_production_fragment(self):
        markup = render_to_string("match_viewer/page.html", {
            "viewer": build_match_presentation(self.cases["approved_opening"]["source"]),
            "preview_mode": True,
            "fixture_choices": [{"url": "?state=opening", "label": "Opening", "selected": True}],
        })
        document = RenderedHtml(markup)
        self.assertIn("live updates off", markup)
        self.assertEqual(document.find("aria-current", "page")[0]["text"], "Opening")
        self.assertNotIn("live updates off", document.text_for("id", "match-viewer-state"))
