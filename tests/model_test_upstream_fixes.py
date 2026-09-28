"""Exercises upstream admin search and bot recovery without external services."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from django.contrib import admin
from django.test import TestCase as DjangoTestCase

import corpoch.dbot
from corpoch.dbot.models import Channels, Guilds, Roles
from corpoch.models import Match


@contextmanager
def load_bot_module(filename):
    """Loads an actual bot module with its service dependencies replaced.

    :param str filename: Module filename inside the bot package"""
    settings = ModuleType("corpoch.dbot.settings")
    tasks = ModuleType("corpoch.dbot.tasks")
    bot_tasks = ModuleType("corpoch.dbot.bot_tasks")
    bot_tasks.run_tasks = Mock()
    bot_tasks.run_tasks.is_running.return_value = False
    replacements = {
        "corpoch.dbot.settings": settings,
        "corpoch.dbot.tasks": tasks,
        "corpoch.dbot.bot_tasks": bot_tasks,
    }
    path = Path(__file__).parents[1] / "corpoch" / "dbot" / f"{filename}.py"
    specification = importlib.util.spec_from_file_location(f"isolated_bot_{filename}", path)
    module = importlib.util.module_from_spec(specification)
    with (
        patch.dict(sys.modules, replacements),
        patch.object(corpoch.dbot, "settings", settings, create=True),
        patch.object(corpoch.dbot, "tasks", tasks, create=True),
        patch.object(corpoch.dbot, "bot_tasks", bot_tasks, create=True),
        patch("django.contrib.admin.register", side_effect=lambda *args, **kwargs: lambda value: value),
    ):
        specification.loader.exec_module(module)
        yield module


class DiscordAdminSearchTests(DjangoTestCase):
    """Evaluates real admin queries against temporary channel and role rows."""

    def test_channel_search_accepts_id_and_name(self):
        self.verify_search(Channels, "ChannelAdmin")

    def test_role_search_accepts_id_and_name(self):
        self.verify_search(Roles, "RoleAdmin")

    def verify_search(self, model, admin_name):
        guild = Guilds.objects.create(id=8100, name="Fixture Guild")
        selected = model.objects.create(id=8101, guild=guild, name="Fixture Search Target")
        model.objects.create(id=8102, guild=guild, name="Unrelated")
        with load_bot_module("admin") as module:
            model_admin = getattr(module, admin_name)(model, admin.AdminSite())
            for term in ("8101", "search target", "no matching item"):
                with self.subTest(term=term):
                    results, may_have_duplicates = model_admin.get_search_results(
                        SimpleNamespace(), model.objects.all(), term,
                    )
                    expected = [] if term == "no matching item" else [selected.pk]
                    self.assertEqual(list(results.values_list("pk", flat=True)), expected)
                    self.assertFalse(may_have_duplicates)


class AsyncMatchList:
    """Supplies fake match records through the startup async query boundary."""

    def __init__(self, records):
        self.records = records

    async def __aiter__(self):
        for record in self.records:
            yield record


class BotStartupRecoveryTests(TestCase):
    """Runs startup awaits inline without constructing a Discord client."""

    def run_callback(self, callback, *args):
        coroutine = callback(*args)
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        self.fail("The callback requires an unmocked asynchronous operation.")

    def test_failed_match_does_not_block_other_matches_or_background_loops(self):
        bot = SimpleNamespace(
            done_startup=False,
            user=SimpleNamespace(name="Fixture Bot", discriminator="0", id=8000),
            retrieve_owners=AsyncMock(),
            matches={},
            message_consumer=Mock(),
            poll_queue=Mock(),
            switch_status=Mock(),
            log=Mock(),
        )
        # This attribute is part of the existing bot integration contract.
        setattr(bot, "_bot", bot)
        broken_view = SimpleNamespace(init=AsyncMock())
        healthy_view = SimpleNamespace(init=AsyncMock(return_value=True))

        async def fail_after_partial_registration():
            bot.matches["broken"] = broken_view
            raise RuntimeError("Fixture missing message")

        broken_view.init.side_effect = fail_after_partial_registration
        records = AsyncMatchList([
            SimpleNamespace(id="no-message", message=None),
            SimpleNamespace(id="broken", message=8100),
            SimpleNamespace(id="healthy", message=8101),
        ])
        with (
            load_bot_module("bot") as module,
            patch.object(Match.objects, "exclude") as matches,
            patch(
                "corpoch.dbot.cogs.tourneycmds.DiscordMatch",
                side_effect=[broken_view, healthy_view],
            ) as create_view,
        ):
            matches.return_value.filter.return_value = records
            self.run_callback(module.CorpoDbot.on_ready, bot)
            self.assertEqual(bot.matches, {"healthy": healthy_view})
            self.assertTrue(bot.done_startup)
            self.assertEqual(create_view.call_count, 2)
            broken_view.init.assert_awaited_once()
            healthy_view.init.assert_awaited_once()
            bot.log.exception.assert_called_once_with(
                "Unable to restore match %s; continuing startup.", "broken",
            )
            bot.message_consumer.consume.assert_called_once_with(no_ack=False)
            bot.poll_queue.start.assert_called_once_with()
            bot.switch_status.start.assert_called_once_with()
            self.run_callback(module.CorpoDbot.on_ready, bot)
            self.assertEqual(create_view.call_count, 2)
            bot.retrieve_owners.assert_awaited_once()
            bot.poll_queue.start.assert_called_once_with()
