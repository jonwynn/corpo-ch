"""Tests selected admin hooks without importing unrelated admin applications."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.test import TestCase

from corpoch.match_actions import (
    advance_match_revision, get_match_state_token, locked_match, record_opening_action,
    select_chart,
)
from corpoch.models import Match, MatchBan, MatchRound
from tests.match_fixtures import create_corp_match


def load_match_admin_module():
    """Loads the real match admin file without global admin autodiscovery."""
    path = Path(__file__).parents[1] / "corpoch" / "admin" / "match.py"
    specification = importlib.util.spec_from_file_location("isolated_match_admin", path)
    module = importlib.util.module_from_spec(specification)
    with patch("django.contrib.admin.register", side_effect=lambda *args, **kwargs: lambda value: value):
        specification.loader.exec_module(module)
    return module


class MatchAdminTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_module = load_match_admin_module()

    def setUp(self):
        self.match, self.seeds, self.charts = create_corp_match()
        self.model_admin = self.admin_module.MatchAdmin(Match, admin.AdminSite(name="isolated"))
        self.request = SimpleNamespace(
            match_viewer_locked=True,
            user=SimpleNamespace(is_superuser=True),
        )

    def build_selected_round(self):
        for actor, chart in (
            (self.seeds[0].player_id, self.charts[0]),
            (self.seeds[1].player_id, self.charts[1]),
            (self.seeds[1].player_id, self.charts[2]),
            (self.seeds[0].player_id, self.charts[3]),
        ):
            self.match = record_opening_action(
                self.match.pk, actor, chart.pk, expected_state=get_match_state_token(self.match),
            )
        self.match = select_chart(
            self.match.pk, self.charts[4].pk, player_id=self.seeds[0].player_id,
            expected_state=get_match_state_token(self.match),
        )
        return self.match.current_round

    def build_formset(self, instance, changed_data):
        return SimpleNamespace(
            save=lambda commit=False: [instance],
            deleted_objects=[],
            forms=[SimpleNamespace(instance=instance, changed_data=changed_data)],
            save_m2m=lambda: None,
        )

    def test_real_form_contains_hidden_rendered_state_token(self):
        form = self.admin_module.MatchAdminForm(instance=self.match)
        self.assertTrue(form.fields["match_state"].widget.is_hidden)
        self.assertEqual(form["match_state"].value(), get_match_state_token(self.match))

    def test_existing_match_identifier_is_readonly_but_new_match_can_set_it(self):
        self.assertIn("id", self.model_admin.get_readonly_fields(self.request, self.match))
        self.assertNotIn("id", self.model_admin.get_readonly_fields(self.request))

    def test_invalid_bound_form_retains_submitted_token_until_explicit_reload(self):
        previous_token = get_match_state_token(self.match)
        with locked_match(self.match.pk) as match:
            advance_match_revision(match)
        self.match.refresh_from_db()
        form = self.admin_module.MatchAdminForm(
            data={"id": self.match.pk, "group": "not-a-group", "match_state": previous_token},
            instance=self.match,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form["match_state"].value(), previous_token)
        reloaded = self.admin_module.MatchAdminForm(instance=Match.objects.get(pk=self.match.pk))
        self.assertNotEqual(reloaded["match_state"].value(), previous_token)

    def test_stale_admin_post_redirects_before_save_or_form_validation(self):
        token = get_match_state_token(self.match)
        with locked_match(self.match.pk) as match:
            advance_match_revision(match)
        request = SimpleNamespace(
            method="POST", POST={"match_state": token},
            path=f"/admin/corpoch/match/{self.match.pk}/change/",
        )
        with (
            patch.object(self.model_admin, "has_change_permission", return_value=True),
            patch.object(self.model_admin, "message_user") as message_user,
            patch.object(admin.ModelAdmin, "changeform_view") as inherited_view,
        ):
            response = self.model_admin.changeform_view(request, self.match.pk)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, request.path)
        inherited_view.assert_not_called()
        message_user.assert_called_once()

    def test_match_scalar_save_updates_only_changed_fields(self):
        stale = Match.objects.get(pk=self.match.pk)
        Match.objects.filter(pk=self.match.pk).update(submitted=True)
        stale.message = 321
        form = SimpleNamespace(changed_data=["message"])
        self.model_admin.save_model(self.request, stale, form, change=True)
        current = Match.objects.get(pk=self.match.pk)
        self.assertEqual(current.message, 321)
        self.assertTrue(current.submitted)

    def test_admin_chart_replacement_invalidates_selection_without_decoding(self):
        selected = self.build_selected_round()
        MatchRound.objects.filter(pk=selected.pk).update(screenshot="retained-reference.png")
        selected.refresh_from_db()
        selected.chart = self.charts[5]
        formset = self.build_formset(selected, ["chart"])
        form = SimpleNamespace(instance=self.match)
        self.model_admin.save_formset(self.request, form, formset, change=True)
        current = MatchRound.objects.get(pk=selected.pk)
        self.assertEqual(current.chart_id, self.charts[5].pk)
        self.assertEqual(current.selection_kind, "unknown")
        self.assertIsNone(current.steg)

    def test_admin_ban_replacement_invalidates_action_phase(self):
        self.build_selected_round()
        action = self.match.match_bans.first()
        action.chart = self.charts[8]
        self.model_admin.save_formset(
            self.request, SimpleNamespace(instance=self.match),
            self.build_formset(action, ["chart"]), change=True,
        )
        self.assertEqual(MatchBan.objects.get(pk=action.pk).action_phase, "unknown")

    def test_admin_result_correction_preserves_provenance_and_export_flag(self):
        selected = self.build_selected_round()
        Match.objects.filter(pk=self.match.pk).update(complete=True, finished=True, submitted=True)
        selected.winner_id = self.seeds[1].player_id
        selected.loser_id = self.seeds[0].player_id
        self.model_admin.save_formset(
            self.request, SimpleNamespace(instance=self.match),
            self.build_formset(selected, ["winner", "loser"]), change=True,
        )
        current = MatchRound.objects.get(pk=selected.pk)
        self.assertEqual(current.selection_kind, "player")
        self.assertEqual(current.winner_id, self.seeds[1].player_id)
        self.match.refresh_from_db()
        self.assertFalse(self.match.complete)
        self.assertFalse(self.match.finished)
        self.assertTrue(self.match.submitted)

    def test_assignment_edit_invalidates_all_provenance_and_advances_revision(self):
        self.build_selected_round()
        initial_revision = self.match.action_revision
        form = SimpleNamespace(
            instance=self.match, changed_data=["players"],
            save_m2m=lambda: None, has_changed=lambda: True,
        )
        self.model_admin.save_related(self.request, form, [], change=True)
        self.match.refresh_from_db()
        self.assertGreater(self.match.action_revision, initial_revision)
        self.assertEqual(set(self.match.match_bans.values_list("action_phase", flat=True)), {"unknown"})
        self.assertEqual(self.match.current_round.selection_kind, "unknown")
