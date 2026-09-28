"""Validated database-only transitions for explicitly configured CORP Cup matches."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json

from django.db import models, transaction
from django.utils import timezone

from corpoch.match_rules import (
    OpeningAction, opening_actor, opening_choices, validate_corp_cup_history,
    validate_corp_cup_profile, validate_opening_actions,
)
from corpoch.models.match import Match, MatchBan, MatchRound


class MatchActionError(ValueError):
    """A match action is incompatible with the current stored state."""


class StaleMatchAction(MatchActionError):
    """The state displayed to the caller has changed."""


@dataclass(frozen=True)
class CorpCupContext:
    """Loaded, validated records for one official CORP Cup match."""

    match: Match
    players: tuple
    charts: tuple
    actions: tuple
    rounds: tuple
    selection: object


def get_match_state_token(match):
    """Captures gameplay and setup state for a rendered interaction.

    :param Match match: Fresh match instance used to render the controls"""
    rules = match.group.bracket.ruleset
    snapshot = {
        "match": [
            str(match.pk), match.group_id, match.rev_seeds, match.defer,
            match.complete, match.action_revision,
            match.winner_id, match.loser_id, match.ended_on,
        ],
        "rules": {
            field.name: getattr(rules, field.name)
            for field in rules._meta.concrete_fields if not field.is_relation
        },
        "bracket": [
            match.group.bracket_id, match.group.bracket.tournament_id,
            match.group.bracket.revealed, match.group.bracket.tournament.config.byos,
            match.group.bracket.tournament.config.version,
        ],
        "players": list(match.players.order_by("pk").values_list(
            "pk", "seed", "group_id", "player_id", "player__tournament_id", "player__config",
        )),
        "charts": list(match.group.bracket.setlist.order_by("pk").values_list(
            "pk", "tiebreaker", "boss", "speed", "md5", "modifiers",
        )),
        "actions": list(match.match_bans.order_by("num", "pk").values_list(
            "pk", "num", "player_id", "chart_id", "saved", "action_phase",
        )),
        "rounds": list(match.match_rounds.order_by("num", "pk").values_list(
            "pk", "num", "chart_id", "picked_id", "winner_id", "loser_id",
            "selection_kind",
        )),
    }
    serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@contextmanager
def locked_match(match_id, expected_state=None, using="default"):
    """Locks one match before validating and publishing database changes.

    No file storage, decoding, network request or provider call belongs inside
    this context. A missing token is reserved for fresh server-side operations.

    :param str match_id: Selected official match identifier
    :param str expected_state: Token captured before a delayed interaction
    :param str using: Database alias"""
    with transaction.atomic(using=using):
        try:
            match = Match.objects.using(using).select_for_update().get(pk=match_id)
        except Match.DoesNotExist as error:
            raise StaleMatchAction("The selected match is no longer available.") from error
        if expected_state is not None and get_match_state_token(match) != expected_state:
            raise StaleMatchAction("The match changed. Refresh the controls and try again.")
        yield match


def advance_match_revision(match, using="default"):
    """Invalidates earlier sporting controls even when values return to the same state.

    :param Match match: Match already held by the selected-match write lock
    :param str using: Database alias"""
    Match.objects.using(using).filter(pk=match.pk).update(
        action_revision=models.F("action_revision") + 1,
    )
    match.refresh_from_db(fields=["action_revision"], using=using)


def load_corp_context(match, *, validate_history=True):
    """Loads and validates configuration, provenance and surviving round history.

    :param Match match: Official match loaded in the caller's read or write scope
    :param bool validate_history: False only for explicit discard-based recovery"""
    if not isinstance(match, Match) or match.ruleset.tb_ruleset != "corp_cup":
        raise MatchActionError("This action requires the explicit CORP Cup profile.")
    rules = match.ruleset
    charts = tuple(match.group.bracket.setlist.order_by("pk"))
    players = tuple(match.players.select_related("player").order_by("seed", "pk"))
    try:
        validate_corp_cup_profile(
            rules.num_players, rules.num_rounds, rules.num_bans, rules.ban_ruleset,
            rules.pick_ruleset, len(charts), match.defer,
        )
        if match.rev_seeds or rules.seed_inversions:
            raise ValueError("CORP Cup does not permit inverted seeds.")
        if match.group.bracket.tournament.config.byos:
            raise ValueError("CORP Cup requires its fixed tournament setlist.")
        if any(chart.tiebreaker for chart in charts):
            raise ValueError("CORP Cup has no separately reserved tiebreaker charts.")
        if any(chart.boss for chart in charts) and not (rules.boss_active and rules.boss_bannable):
            raise ValueError("Every CORP Cup setlist chart must be available for opening bans and play.")
        if len(players) != 2 or any(
            player.player_id is None or player.group_id != match.group_id
            or player.player.tournament_id != match.group.bracket.tournament_id
            for player in players
        ):
            raise ValueError("Assign two players from this tournament and group.")
        if any(player.seed < 1 for player in players) or players[0].seed == players[1].seed:
            raise ValueError("CORP Cup requires distinct player seeds.")
        player_ids = tuple(player.player_id for player in players)
        chart_ids = tuple(chart.pk for chart in charts)
        validate_opening_actions(player_ids, chart_ids, ())
        ban_records = tuple(match.match_bans.order_by("num", "pk"))
        actions = tuple(
            OpeningAction(record.num, record.player_id, record.chart_id, record.saved)
            for record in ban_records
        )
        rounds = tuple(match.match_rounds.order_by("num", "pk"))
        if not validate_history:
            return CorpCupContext(match, players, charts, actions, rounds, None)
        selection = validate_corp_cup_history(
            player_ids, chart_ids, actions,
            tuple(record.action_phase for record in ban_records),
            tuple({
                "num": record.num, "chart_id": record.chart_id, "picked_id": record.picked_id,
                "winner_id": record.winner_id, "loser_id": record.loser_id,
                "selection_kind": record.selection_kind,
            } for record in rounds),
            rules.num_rounds,
        )
        return CorpCupContext(match, players, charts, actions, rounds, selection)
    except ValueError as error:
        raise MatchActionError(str(error)) from error


def corp_picker_id(match):
    """Returns the same logical chooser used by the validated action writer.

    :param Match match: Selected official match"""
    context = load_corp_context(match)
    if len(context.actions) < 4:
        return opening_actor(tuple(player.player_id for player in context.players), context.actions)
    return context.selection.next_picker_id


def corp_chart_choices(match):
    """Returns legal chart IDs for the current ban or selection control.

    :param Match match: Selected official match"""
    context = load_corp_context(match)
    if len(context.actions) < 4:
        bans, saves = opening_choices(
            tuple(player.player_id for player in context.players),
            tuple(chart.pk for chart in context.charts), context.actions,
        )
        return bans
    return context.selection.eligible_chart_ids


def validate_action_token(expected_state):
    """Requires delayed sporting controls to supply their rendered token.

    :param str expected_state: Token captured with the displayed controls"""
    if not isinstance(expected_state, str) or not expected_state:
        raise StaleMatchAction("Refresh the match before choosing an action.")


def validate_active_match(match):
    """Rejects gameplay writes after recorded finalization.

    :param Match match: Locked match"""
    if match.complete or match.finished:
        raise MatchActionError("Reopen the recorded result before changing gameplay.")


def resolve_identifier(value, allowed):
    """Resolves a posted ID against already scoped records.

    :param object value: Posted primitive identifier
    :param tuple allowed: Authoritative identifiers"""
    if isinstance(value, bool):
        raise MatchActionError("The selected record is not part of this match.")
    matches = [identifier for identifier in allowed if str(identifier) == str(value)]
    if len(matches) != 1:
        raise MatchActionError("The selected record is not part of this match.")
    return matches[0]


def create_pending_round(context, using):
    """Creates a blank or forced automatic round after a validated transition.

    :param CorpCupContext context: Validated state with completed prior rounds
    :param str using: Database alias"""
    selection = context.selection
    if selection is None or selection.complete:
        return
    if context.rounds and context.rounds[-1].winner_id is None:
        return
    return MatchRound.objects.using(using).create(
        match=context.match, num=len(context.rounds) + 1,
        picked_id=selection.next_picker_id,
        chart_id=selection.forced_chart_id,
        selection_kind="automatic" if selection.forced_chart_id else "unknown",
    )


def record_opening_action(
    match_id, player_id, chart_id, *, saved=False, expected_state, using="default",
):
    """Records a ban or save together with its provenance and any first round.

    :param str match_id: Match identifier
    :param int player_id: Logical acting participant
    :param int chart_id: Selected chart
    :param bool saved: Whether this action saves the preceding opponent ban
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        context = load_corp_context(match)
        players = tuple(player.player_id for player in context.players)
        actor = resolve_identifier(player_id, players)
        chart = resolve_identifier(chart_id, tuple(item.pk for item in context.charts))
        if opening_actor(players, context.actions) != actor:
            raise MatchActionError("It is not this player's opening turn.")
        actions = (*context.actions, OpeningAction(len(context.actions), actor, chart, saved))
        try:
            validate_opening_actions(players, tuple(item.pk for item in context.charts), actions)
        except ValueError as error:
            raise MatchActionError(str(error)) from error
        MatchBan.objects.using(using).create(
            match=match, num=len(context.actions), player_id=actor, chart_id=chart,
            saved=saved, action_phase="opening",
        )
        create_pending_round(load_corp_context(match), using)
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def select_chart(match_id, chart_id, *, player_id, expected_state, using="default"):
    """Stores the selected chart, logical picker and provenance atomically.

    :param str match_id: Match identifier
    :param int chart_id: Selected eligible chart
    :param int player_id: Logical chooser, including staff acting on their behalf
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        context = load_corp_context(match)
        if not context.rounds or context.rounds[-1].chart_id is not None:
            raise MatchActionError("There is no blank current round to select.")
        selection = context.selection
        actor = resolve_identifier(player_id, tuple(player.player_id for player in context.players))
        chart = resolve_identifier(chart_id, selection.eligible_chart_ids)
        if selection.forced_chart_id is not None or actor != selection.next_picker_id:
            raise MatchActionError("This player is not the current chart chooser.")
        MatchRound.objects.using(using).filter(pk=context.rounds[-1].pk).update(
            chart_id=chart, picked_id=actor, selection_kind="player",
        )
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def record_round_winner(match_id, winner_id, *, expected_state, using="default"):
    """Records a referee result and creates the next eligible round in one write.

    :param str match_id: Match identifier
    :param int winner_id: Referee-recorded winning participant
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        context = load_corp_context(match)
        if (
            not context.rounds or context.rounds[-1].chart_id is None
            or context.rounds[-1].winner_id is not None
        ):
            raise MatchActionError("Select a chart before recording one result for the current round.")
        players = tuple(player.player_id for player in context.players)
        winner = resolve_identifier(winner_id, players)
        loser = next(player for player in players if player != winner)
        MatchRound.objects.using(using).filter(pk=context.rounds[-1].pk).update(
            winner_id=winner, loser_id=loser,
        )
        create_pending_round(load_corp_context(match), using)
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def finalize_match(match_id, *, expected_state, using="default"):
    """Finalizes a decisive recorded score without marking evidence complete.

    :param str match_id: Match identifier
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        context = load_corp_context(match)
        if context.selection is None or not context.selection.complete:
            raise MatchActionError("The match has not reached its winning target.")
        winner = context.rounds[-1].winner_id
        loser = next(player.player_id for player in context.players if player.player_id != winner)
        Match.objects.using(using).filter(pk=match.pk).update(
            complete=True, winner_id=winner, loser_id=loser, ended_on=timezone.now(),
            finished=False,
        )
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def undo_match_action(match_id, *, expected_state, using="default"):
    """Undoes the most recent surviving gameplay or finalization step.

    Result-only corrections retain evidence. Clearing a chart detaches its file
    reference and metadata; physical files and the historical export flag remain.

    :param str match_id: Match identifier
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        recovering = False
        try:
            context = load_corp_context(match)
        except MatchActionError:
            context = load_corp_context(match, validate_history=False)
            recovering = True
            if not match.complete:
                if context.rounds:
                    context.rounds[-1].delete(using=using)
                elif context.actions:
                    match.match_bans.order_by("num", "pk").last().delete(using=using)
                else:
                    raise
                try:
                    surviving = load_corp_context(match)
                except MatchActionError:
                    surviving = None
                if surviving is not None:
                    create_pending_round(surviving, using)
        if not recovering and not match.complete:
            current = context.rounds[-1] if context.rounds else None
            if current and current.winner_id is not None:
                MatchRound.objects.using(using).filter(pk=current.pk).update(
                    winner=None, loser=None,
                )
            elif current and current.chart_id is not None and current.selection_kind != "automatic":
                MatchRound.objects.using(using).filter(pk=current.pk).update(
                    chart=None, picked=None, selection_kind="unknown", screenshot="", steg=None,
                )
            elif current:
                current.delete(using=using)
                if len(context.rounds) > 1:
                    MatchRound.objects.using(using).filter(pk=context.rounds[-2].pk).update(
                        winner=None, loser=None,
                    )
                else:
                    match.match_bans.order_by("num", "pk").last().delete(using=using)
            elif context.actions:
                match.match_bans.order_by("num", "pk").last().delete(using=using)
            else:
                match.players.clear()
        Match.objects.using(using).filter(pk=match.pk).update(
            complete=False, finished=False,
            winner=None, loser=None, ended_on=None,
        )
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def assign_match_players(match_id, seed_ids, *, expected_state, using="default"):
    """Assigns two scoped CORP players before any sporting action occurs.

    :param str match_id: Match identifier
    :param list seed_ids: Two distinct GroupSeed identifiers
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        if match.match_bans.exists() or match.match_rounds.exists():
            raise MatchActionError("Player assignments cannot change after opening actions begin.")
        available = tuple(match.group.seeding.values_list("pk", flat=True))
        if len(seed_ids) != 2:
            raise MatchActionError("Assign exactly two players.")
        selected = [resolve_identifier(identifier, available) for identifier in seed_ids]
        if len(set(selected)) != 2:
            raise MatchActionError("Assign two distinct players.")
        match.players.set(selected)
        load_corp_context(match)
        advance_match_revision(match, using)
    return Match.objects.using(using).get(pk=match_id)


def cancel_match(match_id, *, expected_state, using="default"):
    """Deletes a confirmed, unchanged unfinished official match.

    :param str match_id: Match identifier
    :param str expected_state: Rendered state token
    :param str using: Database alias"""
    validate_action_token(expected_state)
    with locked_match(match_id, expected_state, using) as match:
        validate_active_match(match)
        match.delete(using=using)
