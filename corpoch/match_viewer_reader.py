"""Scoped, read-only database snapshots for the staff match viewer."""

from contextlib import contextmanager
import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import connections, transaction
from django.db.models import BooleanField, Case, Q, TextField, Value, When
from django.db.models.functions import Cast

from corpoch.match_rules import (
    OpeningAction, validate_corp_cup_history, validate_corp_cup_profile,
)
from corpoch.models import Chart, DiscordUser, GroupSeed, Match


class ViewerReadError(Exception):
    """A controlled, non-sensitive viewer response."""

    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


@contextmanager
def match_read_transaction(using="default"):
    """
    Materializes one consistent snapshot without changing global isolation

    :param str using: Database alias
    """
    connection = connections[using]
    if connection.vendor == "sqlite":
        with transaction.atomic(using=using):
            yield
        return
    if connection.vendor != "mysql":
        raise ViewerReadError("This database has not been verified for the match viewer.")
    if getattr(settings, "MATCH_VIEWER_MYSQL_VERIFIED", False) is not True:
        raise ViewerReadError("Match viewing is awaiting database verification.")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise ViewerReadError("A consistent match snapshot is unavailable in this request.")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with transaction.atomic(using=using):
            yield
    finally:
        # Clear the connection even when entering the transaction failed. A
        # pending next-transaction isolation setting must not reach another read.
        connection.close()


def read_active_account(user_id, using):
    """
    Reloads account status rather than trusting a previously loaded session user

    :param object user_id: Session account identifier
    :param str using: Database alias
    :return: Active account"""
    if user_id is None:
        raise ViewerReadError("Sign in with a tournament staff account.", 401)
    try:
        account = DiscordUser.objects.using(using).only(
            "id", "is_active", "is_superuser",
        ).filter(pk=user_id).first()
    except (ValueError, TypeError, OverflowError) as error:
        raise ViewerReadError("Sign in with a tournament staff account.", 401) from error
    if account is None:
        raise ViewerReadError("Sign in with a tournament staff account.", 401)
    if not account.is_active:
        raise ViewerReadError("Your account does not have access to this match.", 403)
    return account


def read_staff_role(account, guild_id, using):
    """
    Requires stored membership in the selected tournament's guild

    :param DiscordUser account: Fresh active account
    :param object guild_id: Selected tournament guild
    :param str using: Database alias
    :return: Stored role, or None for superuser access"""
    if account.is_superuser:
        return None
    if guild_id is not None:
        if account.guilds_admin.using(using).filter(pk=guild_id).exists():
            return "admin"
        if account.guilds_referee.using(using).filter(pk=guild_id).exists():
            return "referee"
    raise ViewerReadError("Your account does not have access to this match.", 403)


def read_alias(config_text):
    """
    Reads only the primary or first valid configured Clone Hero name

    :param str config_text: Raw JSON cast without schema conversion
    :return: Full alias or None"""
    try:
        config = json.loads(config_text) if isinstance(config_text, str) else config_text
    except (TypeError, ValueError):
        return None
    names = config.get("names_list") if isinstance(config, dict) else None
    if not isinstance(names, list):
        return None
    valid_names = [
        item for item in names
        if isinstance(item, dict) and isinstance(item.get("ch_name"), str) and item["ch_name"].strip()
    ]
    return next(
        (item["ch_name"] for item in valid_names if item.get("is_primary") is True),
        valid_names[0]["ch_name"] if valid_names else None,
    )


def read_seed_rows(match, using):
    """
    Loads scoped identities without exposing account IDs or raw configuration

    :param Match match: Selected match with loaded group and tournament
    :param str using: Database alias
    :return: Primitive players and a setup-validity flag"""
    rows = list(match.players.using(using).order_by("seed", "pk").annotate(
        alias_text=Cast("player__config", output_field=TextField()),
    ).values("id", "seed", "group_id", "player_id", "player__tournament_id", "alias_text")[:5])
    players = []
    valid_scope = len(rows) == 2
    for row in rows:
        same_scope = (
            row["group_id"] == match.group_id
            and row["player__tournament_id"] == match.group.bracket.tournament_id
        )
        valid_scope = valid_scope and same_scope and row["player_id"] is not None
        players.append({
            "seed_id": str(row["id"]),
            "player_id": str(row["player_id"]) if row["player_id"] is not None and same_scope else None,
            "seed": row["seed"],
            "name": read_alias(row["alias_text"]) if same_scope else None,
        })
    return players, valid_scope


def build_rule_source(match, players, valid_scope, using):
    """
    Validates the explicit CORP profile using bounded setlist metadata

    :param Match match: Selected match with related context
    :param list players: Primitive players
    :param bool valid_scope: Whether assignments belong to this context
    :param str using: Database alias
    :return: Primitive rules and private setlist identities"""
    try:
        rules = match.group.bracket.ruleset
    except ObjectDoesNotExist:
        return None, ()
    result = {
        key: getattr(rules, key)
        for key in ("num_players", "num_rounds", "num_bans", "ban_ruleset", "pick_ruleset", "tb_ruleset")
    }
    result["profile_supported"] = False
    if rules.tb_ruleset != "corp_cup":
        return result, ()
    charts = list(match.group.bracket.setlist.using(using).non_polymorphic().order_by("pk").values(
        "id", "boss", "tiebreaker",
    )[:14])
    try:
        validate_corp_cup_profile(
            rules.num_players, rules.num_rounds, rules.num_bans, rules.ban_ruleset,
            rules.pick_ruleset, len(charts), match.defer,
        )
        config = match.group.bracket.tournament.config
        if (
            not valid_scope or match.rev_seeds or rules.seed_inversions or config.byos
            or len({player["player_id"] for player in players}) != 2
            or len({player["seed"] for player in players}) != 2
            or any(player["seed"] < 1 for player in players)
            or any(chart["tiebreaker"] for chart in charts)
            or (any(chart["boss"] for chart in charts) and not (rules.boss_active and rules.boss_bannable))
        ):
            return result, ()
    except (ValueError, ObjectDoesNotExist):
        return result, ()
    result["profile_supported"] = True
    return result, tuple(chart["id"] for chart in charts)


def read_match_snapshot(user_id, match_id, using="default"):
    """
    Materializes the selected match, scope and display fields in one transaction

    :param object user_id: Session account identifier
    :param str match_id: Stored match identifier
    :param str using: Database alias
    :return: Primitive presentation source, with no model instances"""
    with match_read_transaction(using):
        account = read_active_account(user_id, using)
        if not isinstance(match_id, str) or not match_id or len(match_id) > 40:
            raise ViewerReadError("Match not found or unavailable.", 404)
        match = Match.objects.using(using).select_related(
            "group__bracket__tournament__config", "group__bracket__ruleset",
        ).filter(pk=match_id).first()
        if match is None:
            raise ViewerReadError("Match not found or unavailable.", 404)
        tournament = match.group.bracket.tournament
        role = read_staff_role(account, tournament.guild_id, using)
        players, valid_scope = read_seed_rows(match, using)
        rules, setlist_ids = build_rule_source(match, players, valid_scope, using)
        actions = list(match.match_bans.using(using).order_by("num", "pk").values(
            "id", "num", "player_id", "chart_id", "saved", "action_phase",
        )[:65])
        rounds = list(match.match_rounds.using(using).order_by("num", "pk").annotate(
            screenshot_present=Case(
                When(Q(screenshot__isnull=True) | Q(screenshot=""), then=Value(False)),
                default=Value(True), output_field=BooleanField(),
            ),
            metadata_present=Case(
                When(steg__isnull=True, then=Value(False)),
                default=Value(True), output_field=BooleanField(),
            ),
        ).values(
            "id", "num", "chart_id", "picked_id", "winner_id", "loser_id",
            "selection_kind", "screenshot_present", "metadata_present",
        )[:101])
        if len(actions) > 64 or len(rounds) > 100:
            raise ViewerReadError("This match has too many records and needs staff review.")
        history_valid = None
        if rules and rules["profile_supported"]:
            try:
                validate_corp_cup_history(
                    tuple(int(player["player_id"]) for player in players), setlist_ids,
                    tuple(
                        OpeningAction(action["num"], action["player_id"], action["chart_id"], action["saved"])
                        for action in actions
                    ),
                    tuple(action["action_phase"] for action in actions), rounds, rules["num_rounds"],
                )
                history_valid = True
            except ValueError:
                history_valid = False
        chart_ids = {record["chart_id"] for record in actions + rounds if record["chart_id"] is not None}
        chart_titles = {}
        if match.group.bracket.revealed:
            for chart in Chart.objects.using(using).non_polymorphic().filter(
                pk__in=chart_ids, brackets=match.group.bracket_id,
            ).only("id", "name", "speed", "modifiers"):
                try:
                    chart_titles[chart.pk] = chart.tournament_name
                except (IndexError, TypeError, AttributeError):
                    chart_titles[chart.pk] = None
        for action in actions:
            action["action_id"] = str(action.pop("id"))
            action["chart_title"] = chart_titles.get(action["chart_id"])
            action["chart_visible"] = action["chart_id"] in chart_titles
        for record in rounds:
            record["round_id"] = str(record.pop("id"))
            record["chart_title"] = chart_titles.get(record["chart_id"])
            record["chart_visible"] = record["chart_id"] in chart_titles
            record["metadata_kind"] = "present" if record.pop("metadata_present") else "absent"
            record.pop("loser_id")
        return {
            "match_id": str(match.pk), "group_id": str(match.group_id),
            "players": players, "rules": rules, "rev_seeds": match.rev_seeds, "defer": match.defer,
            "actions": actions, "rounds": rounds, "bracket_revealed": match.group.bracket.revealed,
            "history_valid": history_valid,
            "context": {
                "tournament": tournament.name, "bracket": match.group.bracket.name, "group": match.group.name,
            },
            "lifecycle": {
                "complete": match.complete, "finished": match.finished,
                "submitted": match.submitted, "winner_id": match.winner_id,
            },
            "access": {
                "authenticated": True, "active": True, "superuser": account.is_superuser, "same_guild_role": role,
            },
        }


def discover_matches(user_id, page_number=1, using="default"):
    """
    Lists at most 25 authorized matches without loading rounds or chart titles

    :param object user_id: Session account identifier
    :param object page_number: Requested pagination page
    :param str using: Database alias
    :return: Primitive match summaries and pagination"""
    with match_read_transaction(using):
        account = read_active_account(user_id, using)
        matches = Match.objects.using(using).order_by("-started_on", "id")
        if not account.is_superuser:
            matches = matches.filter(
                Q(group__bracket__tournament__guild__admins=account.pk)
                | Q(group__bracket__tournament__guild__referees=account.pk),
            ).distinct()
        values = matches.values(
            "id", "complete", "group_id", "group__name", "group__bracket__name",
            "group__bracket__tournament_id", "group__bracket__tournament__name",
        )
        page = Paginator(values, 25).get_page(page_number)
        rows = list(page.object_list)
        participants = list(GroupSeed.objects.using(using).filter(
            match_players__pk__in=[row["id"] for row in rows],
        ).order_by("seed", "pk").annotate(
            alias_text=Cast("player__config", output_field=TextField()),
        ).values("match_players__pk", "group_id", "player__tournament_id", "alias_text")[:126])
        if len(participants) > 125:
            raise ViewerReadError("Match assignments need staff review before listing.")
        names = {row["id"]: [] for row in rows}
        contexts = {row["id"]: row for row in rows}
        for participant in participants:
            row = contexts[participant["match_players__pk"]]
            if (
                participant["group_id"] == row["group_id"]
                and participant["player__tournament_id"] == row["group__bracket__tournament_id"]
            ):
                names[row["id"]].append(read_alias(participant["alias_text"]) or "Player name unavailable")
        summaries = [{
            "match_id": str(row["id"]), "complete": row["complete"],
            "tournament": row["group__bracket__tournament__name"],
            "bracket": row["group__bracket__name"], "group": row["group__name"],
            "players_label": " vs ".join(names[row["id"]]) or "Players not assigned",
        } for row in rows]
        return {
            "matches": summaries, "page": page.number, "pages": page.paginator.num_pages,
            "previous_page": page.previous_page_number() if page.has_previous() else None,
            "next_page": page.next_page_number() if page.has_next() else None,
        }
