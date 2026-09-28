"""Creates and advances one owned synthetic match in the guarded staging database."""

import json

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from corpoch.dbot.models import Guilds, Roles
from corpoch.match_actions import (
    MatchActionError, finalize_match, get_match_state_token, load_corp_context,
    locked_match, record_opening_action, record_round_winner, select_chart,
    undo_match_action,
)
from corpoch.models import (
    Bracket, Chart, CHIcon, DiscordToken, DiscordUser, ExhibitionMatch, Group,
    GroupSeed, Match, Tournament, TournamentPlayer,
)
from corpoch.types import CH_Name, PlayerConfig
from staging.configuration import StagingConfigurationError


def fixture_definition():
    """Returns the fixed synthetic identities used by this staging checkpoint."""
    return {
        "match_id": "local-viewer-pilot",
        "tournament": "Synthetic Local Viewer Pilot",
        "short_name": "LOCAL-PILOT",
        "bracket": "Synthetic Group Stage",
        "group": "A",
        "icon": "local_viewer_pilot_icon",
        "players": ("Blue Player", "Coral Player"),
        "charts": tuple(f"Synthetic Song {number:02d}" for number in range(1, 12)),
    }


def fixture_error():
    """Creates a safe error for absent, foreign or changed fixture records."""
    return StagingConfigurationError(
        "The local viewer fixture is missing or changed. No fixture records were replaced."
    )


def validate_identifier(value):
    """
    Requires an explicit positive database identity

    :param int value: Verified Discord identity
    :return: Validated identity"""
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise StagingConfigurationError("The fixture requires verified DEV identities.")
    return value


def validate_referee_roles(referee_roles):
    """
    Normalizes already verified DEV role metadata without contacting Discord

    :param list referee_roles: Verified dictionaries containing id and name
    :return: Roles ordered by their unique identity"""
    if not isinstance(referee_roles, list) or not referee_roles:
        raise StagingConfigurationError("At least one verified DEV referee role is required.")
    normalized = []
    for role in referee_roles:
        if (
            not isinstance(role, dict) or set(role) != {"id", "name"}
            or not isinstance(role["name"], str) or not 1 <= len(role["name"]) <= 100
        ):
            raise StagingConfigurationError("The verified DEV referee roles are invalid.")
        normalized.append({"id": validate_identifier(role["id"]), "name": role["name"]})
    if len({role["id"] for role in normalized}) != len(normalized):
        raise StagingConfigurationError("The verified DEV referee roles contain duplicates.")
    return sorted(normalized, key=lambda role: role["id"])


def validate_fixture_account(account_id):
    """
    Requires the existing active OAuth account without loading its credentials

    :param int account_id: Previously authenticated account identity
    :return: Existing active account"""
    account = DiscordUser.objects.filter(pk=account_id, is_active=True).first()
    if account is None or not DiscordToken.objects.filter(user_id=account_id).exists():
        raise StagingConfigurationError("Sign in with the verified DEV account before preparing the fixture.")
    return account


def read_fixture_marker(match):
    """
    Reads the fixture's versioned ownership record from its tournament configuration

    :param Match match: Fixed synthetic match
    :return: Recorded graph identities"""
    try:
        marker = json.loads(match.group.bracket.tournament.config.rules)
    except (ValueError, TypeError, ObjectDoesNotExist):
        raise fixture_error() from None
    fields = {
        "kind", "guild_id", "account_id", "roles", "tournament_id", "bracket_id",
        "group_id", "player_ids", "seed_ids", "chart_ids",
    }
    if not isinstance(marker, dict) or set(marker) != fields or marker["kind"] != "local-viewer-pilot-v1":
        raise fixture_error()
    return marker


def validate_fixture(require_access=True):
    """
    Requires the complete owned fixture and its current local referee grant

    :param bool require_access: Whether current active OAuth and referee access is required
    :return: Validated fixed match, including its referee identity"""
    definition = fixture_definition()
    if Tournament.objects.count() != 1 or Match.objects.count() != 1 or ExhibitionMatch.objects.exists():
        raise fixture_error()
    match = Match.objects.select_related("group__bracket__tournament__config", "group__bracket__ruleset").filter(
        pk=definition["match_id"],
    ).first()
    if match is None:
        raise fixture_error()
    marker = read_fixture_marker(match)
    tournament = match.group.bracket.tournament
    bracket = match.group.bracket
    try:
        roles = validate_referee_roles(marker["roles"])
        validate_identifier(marker["guild_id"])
        validate_identifier(marker["account_id"])
        account = (
            validate_fixture_account(marker["account_id"])
            if require_access else DiscordUser.objects.get(pk=marker["account_id"])
        )
        context = load_corp_context(match)
    except (StagingConfigurationError, MatchActionError, ObjectDoesNotExist):
        raise fixture_error() from None
    guild = Guilds.objects.filter(pk=marker["guild_id"], deleted=False).first()
    stored_roles = list(Roles.objects.filter(
        pk__in=[role["id"] for role in roles], guild_id=marker["guild_id"], deleted=False,
    ).order_by("pk").values("id", "name"))
    if (
        guild is None or (require_access and not guild.referees.filter(pk=account.pk).exists())
        or match.referee_id != account.pk or tournament.guild_id != guild.pk
        or guild.ref_role_id != roles[0]["id"]
        or set(guild.additional_ref_roles.values_list("pk", flat=True)) != {role["id"] for role in roles[1:]}
        or stored_roles != roles
    ):
        raise fixture_error()
    if (
        [tournament.pk, bracket.pk, match.group_id]
        != [marker["tournament_id"], marker["bracket_id"], marker["group_id"]]
        or (tournament.name, tournament.short_name, bracket.name, match.group.name) != (
            definition["tournament"], definition["short_name"], definition["bracket"], definition["group"],
        )
        or tournament.active or tournament.role_id is not None
        or tournament.config.gsheet or tournament.config.byos
        or tournament.brackets.count() != 1 or tournament.players.count() != 2
        or tournament.qualifier.exists() or bracket.groups.count() != 1
        or bracket.is_active or not bracket.revealed or bracket.score_log_id is not None
        or bracket.role_id is not None or match.group.role_id is not None
        or match.group.seeding.count() != 2 or match.channel_id is not None or match.message is not None
        or match.exhibition or match.finished or match.submitted
    ):
        raise fixture_error()
    rules = bracket.ruleset
    if (
        (rules.num_players, rules.num_rounds, rules.num_bans, rules.ban_ruleset, rules.pick_ruleset, rules.tb_ruleset)
        != (2, 7, 2, "bansave", "loserpicks", "corp_cup")
        or rules.boss_active or rules.boss_bannable
        or [seed.pk for seed in context.players] != marker["seed_ids"]
        or [seed.player_id for seed in context.players] != marker["player_ids"]
        or [chart.pk for chart in context.charts] != marker["chart_ids"]
    ):
        raise fixture_error()
    for number, seed in enumerate(context.players):
        expected_name = definition["players"][number]
        if (
            seed.seed != number + 1 or seed.eliminated or seed.player.user_id is not None
            or not seed.player.is_active or seed.player.name != expected_name
            or seed.player.config != PlayerConfig(names_list=[CH_Name(ch_name=expected_name, is_primary=True)])
        ):
            raise fixture_error()
    for number, chart in enumerate(context.charts):
        if (
            chart.name != definition["charts"][number] or chart.icon_id != definition["icon"]
            or chart.boss or chart.tiebreaker or chart.sngfile or chart.url
            or chart.speed != 100 or chart.modifiers != ["NM"]
            or chart.brackets.count() != 1
        ):
            raise fixture_error()
    expected_bans = [
        (number, context.players[owner].player_id, context.charts[number].pk, False, "opening")
        for number, owner in enumerate((0, 1, 1, 0))
    ]
    stored_bans = list(match.match_bans.order_by("num", "pk").values_list(
        "num", "player_id", "chart_id", "saved", "action_phase",
    ))
    if stored_bans != expected_bans:
        raise fixture_error()
    if any(round_record.screenshot or round_record.steg for round_record in context.rounds):
        raise fixture_error()
    if match.complete:
        if (
            context.selection is None or not context.selection.complete or match.ended_on is None
            or match.winner_id != context.rounds[-1].winner_id
            or match.loser_id != context.rounds[-1].loser_id
        ):
            raise fixture_error()
    elif match.winner_id is not None or match.loser_id is not None or match.ended_on is not None:
        raise fixture_error()
    return match


def prepare_fixture(guild_id, account_id, referee_roles):
    """
    Creates the synthetic match once, or validates and preserves its existing state

    :param int guild_id: Independently verified DEV guild identity
    :param int account_id: Existing account verified through DEV OAuth and membership
    :param list referee_roles: Already verified DEV role dictionaries
    :return: Fixed synthetic match identity"""
    guild_id = validate_identifier(guild_id)
    account_id = validate_identifier(account_id)
    roles = validate_referee_roles(referee_roles)
    definition = fixture_definition()
    with transaction.atomic():
        account = validate_fixture_account(account_id)
        DiscordUser.objects.select_for_update().get(pk=account.pk)
        if Match.objects.filter(pk=definition["match_id"]).exists():
            match = validate_fixture(require_access=False)
            marker = read_fixture_marker(match)
            if (marker["guild_id"], marker["account_id"], marker["roles"]) != (guild_id, account_id, roles):
                raise fixture_error()
            match.group.bracket.tournament.guild.referees.add(account)
            validate_fixture()
            return match.pk
        if Tournament.objects.exists() or Match.objects.exists() or ExhibitionMatch.objects.exists():
            raise fixture_error()
        if CHIcon.objects.filter(pk=definition["icon"]).exists():
            raise fixture_error()
        guild, unused_created = Guilds.objects.get_or_create(pk=guild_id)
        guild.deleted = False
        guild.save(update_fields=["deleted"])
        for role in roles:
            stored = Roles.objects.filter(pk=role["id"]).first()
            if stored is not None and stored.guild_id != guild_id:
                raise fixture_error()
            Roles.objects.update_or_create(
                pk=role["id"], defaults={"guild": guild, "name": role["name"], "deleted": False},
            )
        guild.ref_role_id = roles[0]["id"]
        guild.save(update_fields=["ref_role"])
        guild.additional_ref_roles.set([role["id"] for role in roles[1:]])
        guild.referees.add(account)
        tournament = Tournament(name=definition["tournament"], short_name=definition["short_name"], guild=guild)
        tournament.save()
        bracket = Bracket(tournament=tournament, name=definition["bracket"], revealed=True)
        bracket.save()
        rules = bracket.ruleset
        rules.num_players, rules.num_rounds, rules.num_bans = 2, 7, 2
        rules.ban_ruleset, rules.pick_ruleset, rules.tb_ruleset = "bansave", "loserpicks", "corp_cup"
        rules.save()
        group = Group.objects.create(bracket=bracket, name=definition["group"])
        seeds = []
        for number, name in enumerate(definition["players"], start=1):
            player = TournamentPlayer.objects.create(
                tournament=tournament, name=name, is_active=True,
                config=PlayerConfig(names_list=[CH_Name(ch_name=name, is_primary=True)]),
            )
            seeds.append(GroupSeed.objects.create(group=group, player=player, seed=number))
        icon = CHIcon.objects.create(name=definition["icon"])
        charts = []
        for name in definition["charts"]:
            chart = Chart(name=name, icon=icon)
            chart.save()
            chart.brackets.add(bracket)
            charts.append(chart)
        match = Match(id=definition["match_id"], group=group, referee=account)
        match.save()
        match.players.set(seeds)
        tournament.config.rules = json.dumps({
            "kind": "local-viewer-pilot-v1", "guild_id": guild_id, "account_id": account_id,
            "roles": roles, "tournament_id": tournament.pk, "bracket_id": bracket.pk,
            "group_id": group.pk, "player_ids": [seed.player_id for seed in seeds],
            "seed_ids": [seed.pk for seed in seeds], "chart_ids": [chart.pk for chart in charts],
        }, sort_keys=True)
        if len(tournament.config.rules) > 1024:
            raise StagingConfigurationError("Too many DEV referee roles for the local fixture ownership record.")
        tournament.config.save(update_fields=["rules"])
        for number, owner in enumerate((0, 1, 1, 0)):
            match = record_opening_action(
                match.pk, seeds[owner].player_id, charts[number].pk,
                expected_state=get_match_state_token(match),
            )
        validate_fixture()
    return match.pk


def advance_fixture(action, expected_state):
    """
    Applies one token-checked database-only action to the validated synthetic match

    :param str action: pick, win-p1, win-p2, undo or finalize
    :param str expected_state: Fresh match state token from the local snapshot
    :return: Updated fixed match"""
    if action not in {"pick", "win-p1", "win-p2", "undo", "finalize"}:
        raise StagingConfigurationError("This local fixture action is unavailable.")
    if not isinstance(expected_state, str) or not expected_state:
        raise StagingConfigurationError("Inspect the local fixture before choosing an action.")
    with locked_match(fixture_definition()["match_id"], expected_state):
        match = validate_fixture()
        context = load_corp_context(match)
        if action == "pick":
            if context.selection is None or not context.selection.eligible_chart_ids:
                raise MatchActionError("There is no chart available to pick.")
            result = select_chart(
                match.pk, context.selection.eligible_chart_ids[0],
                player_id=context.selection.next_picker_id, expected_state=expected_state,
            )
        elif action in {"win-p1", "win-p2"}:
            result = record_round_winner(
                match.pk, context.players[0 if action == "win-p1" else 1].player_id,
                expected_state=expected_state,
            )
        elif action == "finalize":
            result = finalize_match(match.pk, expected_state=expected_state)
        else:
            if not match.complete and len(context.rounds) == 1 and context.rounds[0].chart_id is None:
                raise MatchActionError("The initial fixture bans cannot be undone.")
            result = undo_match_action(match.pk, expected_state=expected_state)
        validate_fixture()
        return result


def revoke_fixture_access():
    """
    Revokes only the fixture owner's local referee grant under its match lock

    :return: Fixed match with unchanged gameplay and ownership"""
    with locked_match(fixture_definition()["match_id"]):
        match = validate_fixture(require_access=False)
        match.group.bracket.tournament.guild.referees.remove(match.referee_id)
        return match
