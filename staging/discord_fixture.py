"""Exposes bounded referee actions for the existing isolated sample match."""

from corpoch.match_actions import (
    MatchActionError, finalize_match, get_match_state_token, load_corp_context,
    locked_match, record_round_winner, select_chart, undo_match_action,
)
from corpoch.models import DiscordUser
from staging.configuration import StagingConfigurationError
from staging.viewer_fixture import fixture_definition, read_fixture_marker, validate_fixture, validate_identifier


def validate_actor(match, account_id, guild_id, shared_referees=False):
    """
    Requires the owner or an explicitly enabled active local referee

    :param Match match: Validated sample match
    :param int account_id: Independently checked human identity
    :param int guild_id: Independently checked DEV server identity
    :param bool shared_referees: Whether explicit shared-mode referee grants are accepted"""
    marker = read_fixture_marker(match)
    if (
        type(account_id) is not int or type(guild_id) is not int
        or guild_id != marker["guild_id"] or type(shared_referees) is not bool
    ):
        raise StagingConfigurationError("This account or server does not own the local sample.")
    if shared_referees:
        if not match.group.bracket.tournament.guild.referees.filter(pk=account_id, is_active=True).exists():
            raise StagingConfigurationError("This account does not have active local referee access.")
    elif account_id != marker["account_id"]:
        raise StagingConfigurationError("This account or server does not own the local sample.")


def grant_shared_referee(account_id, guild_id):
    """
    Stores a minimal local grant after the caller verifies current Discord roles

    :param int account_id: Freshly verified human Discord identity
    :param int guild_id: Freshly verified pinned DEV guild"""
    validate_identifier(account_id)
    validate_identifier(guild_id)
    with locked_match(fixture_definition()["match_id"]):
        match = validate_fixture()
        if guild_id != read_fixture_marker(match)["guild_id"]:
            raise StagingConfigurationError("The shared referee does not belong to the sample server.")
        account = DiscordUser.objects.filter(pk=account_id).first()
        if account is None:
            account = DiscordUser(pk=account_id, is_active=True, is_staff=False, is_superuser=False)
            account.set_unusable_password()
            account.save()
        if not account.is_active:
            raise StagingConfigurationError("This local account is disabled.")
        match.group.bracket.tournament.guild.referees.add(account)


def revoke_shared_referee(account_id, guild_id):
    """
    Removes only one caller's local grant after confirmed Discord role removal

    :param int account_id: Previously authorized human identity
    :param int guild_id: Pinned DEV guild identity"""
    validate_identifier(account_id)
    validate_identifier(guild_id)
    with locked_match(fixture_definition()["match_id"]):
        match = validate_fixture(require_access=False)
        if guild_id != read_fixture_marker(match)["guild_id"]:
            raise StagingConfigurationError("The shared referee does not belong to the sample server.")
        match.group.bracket.tournament.guild.referees.remove(account_id)


def create_control_snapshot(match):
    """
    Copies only synthetic display data and bounded action identifiers

    :param Match match: Validated match held under its action lock
    :return: Detached dictionaries for asynchronous Discord rendering"""
    marker = read_fixture_marker(match)
    context = load_corp_context(match)
    selection = context.selection
    current_round = context.rounds[-1] if context.rounds else None
    eligible_ids = set(selection.eligible_chart_ids) if selection else set()
    current_chart = next(
        (chart for chart in context.charts if current_round and chart.pk == current_round.chart_id), None,
    )
    return {
        "match_id": str(match.pk),
        "token": get_match_state_token(match),
        "account_id": marker["account_id"],
        "guild_id": marker["guild_id"],
        "role_ids": tuple(role["id"] for role in marker["roles"]),
        "players": tuple({"id": seed.player_id, "name": seed.player.ch_name, "seed": seed.seed} for seed in context.players),
        "charts": tuple(
            {"id": chart.pk, "name": chart.tournament_name, "description": chart.description}
            for chart in context.charts if chart.pk in eligible_ids
        ),
        "next_picker_id": selection.next_picker_id if selection else None,
        "round_number": len(context.rounds),
        "current_chart": {"id": current_chart.pk, "name": current_chart.tournament_name} if current_chart else None,
        "wins": tuple(match.score_int),
        "target": match.ruleset.wins_needed,
        "complete": match.complete,
        "can_finalize": bool(selection and selection.complete and not match.complete),
        "can_undo": bool(match.complete or len(context.rounds) > 1 or current_chart),
    }


def build_control_snapshot(account_id=None, guild_id=None, shared_referees=False):
    """
    Reads the owned sample without exposing ORM objects to the event loop

    :param int account_id: Optional expected human identity
    :param int guild_id: Optional expected DEV server identity
    :param bool shared_referees: Whether explicit shared-mode referee grants are accepted
    :return: Detached control snapshot"""
    with locked_match(fixture_definition()["match_id"]):
        match = validate_fixture()
        if account_id is not None or guild_id is not None:
            validate_actor(match, account_id, guild_id, shared_referees)
        return create_control_snapshot(match)


def apply_control_action(action, expected_state, account_id, guild_id, chart_id=None, player_id=None, shared_referees=False):
    """
    Applies one existing referee transition inside the fixture ownership boundary

    :param str action: pick, winner, undo or finalize
    :param str expected_state: State captured when the controls were displayed
    :param int account_id: Freshly authorized Discord member
    :param int guild_id: Expected DEV server
    :param int chart_id: Explicit chart selected by the referee
    :param int player_id: Captured picker or selected round winner
    :param bool shared_referees: Whether explicit shared-mode referee grants are accepted
    :return: Updated detached control snapshot"""
    if action not in {"pick", "winner", "undo", "finalize"}:
        raise StagingConfigurationError("This action is unavailable in the local Discord pilot.")
    if not isinstance(expected_state, str) or not expected_state:
        raise StagingConfigurationError("Open fresh sample controls before changing the match.")
    with locked_match(fixture_definition()["match_id"], expected_state):
        match = validate_fixture()
        validate_actor(match, account_id, guild_id, shared_referees)
        context = load_corp_context(match)
        if action == "pick":
            if type(chart_id) is not int or type(player_id) is not int:
                raise MatchActionError("Select an available chart for the current picker.")
            select_chart(match.pk, chart_id, player_id=player_id, expected_state=expected_state)
        elif action == "winner":
            if chart_id is not None or type(player_id) is not int:
                raise MatchActionError("Select one of the sample players as the winner.")
            record_round_winner(match.pk, player_id, expected_state=expected_state)
        else:
            if chart_id is not None or player_id is not None:
                raise MatchActionError("This control does not accept a chart or player.")
            if action == "finalize":
                finalize_match(match.pk, expected_state=expected_state)
            else:
                if not match.complete and len(context.rounds) == 1 and context.rounds[0].chart_id is None:
                    raise MatchActionError("The initial fixture bans cannot be undone.")
                undo_match_action(match.pk, expected_state=expected_state)
        return create_control_snapshot(validate_fixture())
