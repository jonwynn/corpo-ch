"""Builds detached DEV controls around the existing referee callbacks."""

import logging
from types import SimpleNamespace

import discord

from corpoch.dbot.view.reftool import (
    DiscordMatchView,
    PlayerRoundSelect,
    SongRoundSelect,
)
from corpoch.match_actions import (
    finalize_match,
    record_round_winner,
    select_chart,
    undo_match_action,
)


class ControlMatch:
    """Adapts a verified match snapshot without exposing model instances."""

    def __init__(self, snapshot, perform):
        self.snapshot = snapshot
        self.perform = perform
        self.is_corp_cup = True
        self.render_state = snapshot["token"]
        self.matchDb = SimpleNamespace(pk=snapshot["match_id"])
        self.current_round = SimpleNamespace(chart=snapshot["current_chart"])
        self.picking_player = next((
            SimpleNamespace(pk=player["id"], ch_name=player["name"])
            for player in snapshot["players"]
            if player["id"] == snapshot["next_picker_id"]
        ), None)
        self.seeding = tuple(
            SimpleNamespace(
                player_id=player["id"], seed=player["seed"],
                player=SimpleNamespace(
                    ch_name=player["name"][:100 - len(f" ({player['seed']})")],
                    name=player["name"][:99],
                ),
            )
            for player in snapshot["players"]
        )

    async def apply_match_action(self, interaction, action, *arguments, **options):
        """
        Routes only the four supported referee actions to the guarded adapter

        :param object interaction: Current Discord interaction
        :param function action: Existing match action selected by the callback
        :param tuple arguments: Callback match and selection identifiers
        :param dict options: Callback state token and optional picker identifier"""
        snapshot = self.snapshot
        if (
            not arguments or arguments[0] != snapshot["match_id"]
            or options.get("expected_state") != snapshot["token"]
        ):
            raise ValueError("The local match controls no longer match this snapshot.")
        parameters = {}
        if action is select_chart:
            if (
                len(arguments) != 2 or set(options) != {"expected_state", "player_id"}
                or snapshot["complete"] or snapshot["current_chart"] is not None
                or snapshot["next_picker_id"] is None
                or options["player_id"] != snapshot["next_picker_id"]
                or arguments[1] not in {chart["id"] for chart in snapshot["charts"]}
            ):
                raise ValueError("This chart selection is unavailable in the local sample.")
            action_name = "pick"
            parameters = {"chart_id": arguments[1], "player_id": options["player_id"]}
        elif action is record_round_winner:
            if (
                len(arguments) != 2 or set(options) != {"expected_state"}
                or snapshot["complete"] or snapshot["can_finalize"]
                or snapshot["current_chart"] is None
                or arguments[1] not in {player["id"] for player in snapshot["players"]}
            ):
                raise ValueError("This winner selection is unavailable in the local sample.")
            action_name = "winner"
            parameters = {"player_id": arguments[1]}
        elif action is undo_match_action or action is finalize_match:
            action_name = "undo" if action is undo_match_action else "finalize"
            available = snapshot["can_undo"] if action_name == "undo" else snapshot["can_finalize"]
            if (
                len(arguments) != 1 or set(options) != {"expected_state"}
                or not available or (action_name == "finalize" and snapshot["complete"])
            ):
                raise ValueError("This action is unavailable in the local sample.")
        else:
            raise ValueError("This referee action is unavailable in the local sample.")
        return await self.perform(interaction, action_name, snapshot["token"], **parameters)


class ControlSongSelect(SongRoundSelect):
    """Uses the production song callback with local, emoji-free options."""

    async def init(self):
        """Creates eligible chart options without querying models or Discord."""
        snapshot = self.match.snapshot
        charts = snapshot["charts"]
        if snapshot["current_chart"] is not None:
            charts = (snapshot["current_chart"],)
        if not charts:
            raise ValueError("The local sample has no available chart options.")
        self.retOpts = {str(chart["id"]): SimpleNamespace(pk=chart["id"]) for chart in charts}
        options = [
            discord.SelectOption(
                label=chart["name"][:100], value=str(chart["id"]),
                description=(chart.get("description") or "Synthetic test chart")[:100],
            )
            for chart in charts
        ]
        picker = self.match.picking_player
        placeholder = f"{picker.ch_name} picks" if picker else "Chart selection"
        discord.ui.Select.__init__(
            self, placeholder=placeholder[:150], options=options, max_values=1,
            custom_id="roundsong_sel", disabled=self.dis, row=0,
        )


class ControlView(discord.ui.View):
    """Limits a short-lived, ephemeral session to the approved match controls."""

    def __init__(self, match, authorize, notify=None):
        super().__init__(timeout=900)
        self.log = logging.getLogger(__name__)
        self.match = match
        self.authorize = authorize
        self.notify = notify
        self.expected_state = match.render_state
        self.match_id = match.matchDb.pk

    async def interaction_check(self, interaction):
        """Checks the bound actor and DEV destination before dispatching a component."""
        return await self.authorize(interaction)

    async def handle_back(self, interaction):
        """Invokes the existing correction callback through the guarded adapter."""
        await DiscordMatchView.backBtn(self, interaction)

    async def handle_finalize(self, interaction):
        """Invokes the existing finalization callback through the guarded adapter."""
        await DiscordMatchView.submitBtn(self, interaction)

    async def on_error(self, error, item, interaction):
        """Reports a bounded diagnostic without logging private interaction values."""
        self.log.warning("A local referee control failed; reopen the DEV sample controls.")
        if self.notify is not None:
            await self.notify(
                interaction,
                "A control could not finish. Inspect the saved viewer and reopen /viewer-pilot before retrying.",
            )


def build_control_embed(snapshot):
    """
    Describes the synthetic match without player mentions or media links

    :param dict snapshot: Detached match fields for one recorded state
    :return: Discord embed for the private referee controls"""
    embed = discord.Embed(title="Local sample match controls", colour=0x80B7F6)
    embed.description = "Synthetic test match. Changes appear in the local match viewer."
    for index, player in enumerate(snapshot["players"]):
        name = discord.utils.escape_markdown(player["name"])
        embed.add_field(
            name=f"P{index + 1} · Seed {player['seed']}",
            value=f"{name}\nRound wins: {snapshot['wins'][index]}"[:1024],
        )
    chart = snapshot["current_chart"]
    if snapshot["complete"]:
        status = "Match result finalized. Screenshot processing and exports are disabled."
    elif snapshot["can_finalize"]:
        status = "Win target reached. Finalize the recorded match result."
    elif chart is not None:
        status = f"Round {snapshot['round_number']}: {discord.utils.escape_markdown(chart['name'])}\nAwaiting recorded winner."
    else:
        status = f"Round {snapshot['round_number']}: awaiting chart selection."
    embed.add_field(name=f"First to {snapshot['target']}", value=status[:1024], inline=False)
    embed.set_footer(text="Controls expire after 15 minutes of inactivity. Reopen the sample command to continue.")
    return embed


async def create_controls(snapshot, authorize, perform, notify=None):
    """
    Builds query-free controls that reuse the existing match mutation callbacks

    :param dict snapshot: Plain match data verified by the local fixture reader
    :param function authorize: Async interaction authorization and acknowledgement
    :param function perform: Async guarded mutation and redraw adapter
    :param function notify: Optional async notifier restricted to the DEV interaction
    :return: View and embed for an ephemeral Discord response"""
    match = ControlMatch(snapshot, perform)
    view = ControlView(match, authorize, notify)
    if not snapshot["complete"] and not snapshot["can_finalize"]:
        song = ControlSongSelect(match, snapshot["current_chart"] is not None)
        await song.init()
        view.add_item(song)
        winner = PlayerRoundSelect(match, snapshot["current_chart"] is None)
        await winner.init()
        winner.row = 1
        view.add_item(winner)
    if snapshot["can_undo"]:
        back = discord.ui.Button(
            label="Reopen match" if snapshot["complete"] else "Undo last action",
            style=discord.ButtonStyle.secondary, custom_id="backBtn", row=2,
        )
        back.callback = view.handle_back
        view.add_item(back)
    if snapshot["can_finalize"] and not snapshot["complete"]:
        finalize = discord.ui.Button(
            label="Finalize result", style=discord.ButtonStyle.success,
            custom_id="submitBtn", row=2,
        )
        finalize.callback = view.handle_finalize
        view.add_item(finalize)
    return view, build_control_embed(snapshot)
