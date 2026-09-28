"""Exercises the admin actions fixed by the upstream missing-import patch."""

from contextlib import contextmanager
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.contrib import admin
from django.utils import timezone

import corpoch
import corpoch.dbot
from corpoch.models import Bracket, DiscordUser, QualifierSubmission


@contextmanager
def load_admin_module(name):
    """Loads a real admin module with outbound task dispatch replaced by mocks.

    :param str name: Fixed admin module filename"""
    tasks = ModuleType("corpoch.tasks")
    tasks.update_gsheet = Mock()
    bot_tasks = ModuleType("corpoch.dbot.tasks")
    bot_tasks.update_user = Mock()
    path = Path(__file__).parents[1] / "corpoch" / "admin" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"isolated_admin_{name}", path)
    module = importlib.util.module_from_spec(specification)
    with (
        patch.dict(sys.modules, {"corpoch.tasks": tasks, "corpoch.dbot.tasks": bot_tasks}),
        patch.object(corpoch, "tasks", tasks, create=True),
        patch.object(corpoch.dbot, "tasks", bot_tasks, create=True),
        patch("django.contrib.admin.register", side_effect=lambda *args, **kwargs: lambda value: value),
    ):
        specification.loader.exec_module(module)
        yield module, bot_tasks


class AdminImportTests(TestCase):
    """Verifies actual action and permission branches without sending tasks."""

    def test_discord_user_action_dispatches_selected_ids(self):
        with load_admin_module("misc") as (module, tasks):
            model_admin = module.DiscordUserAdmin(DiscordUser, admin.AdminSite())
            model_admin.update_discord_user(None, [SimpleNamespace(id=17), SimpleNamespace(id=23)])
            self.assertEqual([call.args for call in tasks.update_user.call_args_list], [(17,), (23,)])

    def test_missing_staff_membership_uses_the_existing_readonly_branch(self):
        with load_admin_module("tournament") as (module, tasks):
            inline = module.BracketRulesInline(Bracket, admin.AdminSite())
            membership = Mock()
            membership.get.side_effect = DiscordUser.DoesNotExist
            setlist = Mock()
            setlist.all.return_value.filter.return_value = []
            bracket = SimpleNamespace(tournament=SimpleNamespace(guild=SimpleNamespace(admins=membership)), setlist=setlist)
            request = SimpleNamespace(user=SimpleNamespace(id=17, is_superuser=False))
            self.assertIn("num_rounds", inline.get_readonly_fields(request, bracket))

    def test_qualifier_visibility_compares_the_actual_current_time(self):
        with load_admin_module("tournament") as (module, tasks):
            model_admin = module.QualifierSubmissionAdmin(QualifierSubmission, admin.AdminSite())
            membership = Mock()
            membership.get.side_effect = DiscordUser.DoesNotExist
            request = SimpleNamespace(user=SimpleNamespace(id=17, is_superuser=False))
            for offset, hidden in [(timedelta(days=1), True), (timedelta(days=-1), False)]:
                submission = SimpleNamespace(id=5, qualifier=SimpleNamespace(
                    tournament=SimpleNamespace(guild=SimpleNamespace(admins=membership)),
                    end_time=timezone.now() + offset,
                ))
                queryset = Mock()
                queryset.__iter__ = Mock(return_value=iter([submission]))
                queryset.all.return_value.exclude.return_value = "filtered"
                with patch.object(admin.ModelAdmin, "get_queryset", return_value=queryset):
                    result = model_admin.get_queryset(request)
                if hidden:
                    self.assertEqual(result, "filtered")
                    queryset.all.return_value.exclude.assert_called_once_with(id=5)
                else:
                    self.assertIs(result, queryset)
                    queryset.all.assert_not_called()
