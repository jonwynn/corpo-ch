"""Pure rules for the explicitly selected CORP Cup match profile."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OpeningAction:
    """A recorded opening ban or save, ordered from zero."""

    num: int
    player_id: str | int
    chart_id: str | int
    saved: bool = False


@dataclass(frozen=True)
class CorpCupSelection:
    """The next selection after a complete opening and recorded results."""

    next_picker_id: str | int | None
    eligible_chart_ids: tuple
    forced_chart_id: str | int | None
    is_tiebreaker: bool
    complete: bool


def validate_corp_cup_profile(
    num_players, num_rounds, num_bans, ban_ruleset, pick_ruleset,
    setlist_size, defer=False,
):
    """Rejects configurations outside the confirmed CORP Cup profile.

    :param int num_players: number of match participants
    :param int num_rounds: maximum songs, seven or nine
    :param int num_bans: stored action quota per player, including saves
    :param str ban_ruleset: opening action mode
    :param str pick_ruleset: ordinary chart selection mode
    :param int setlist_size: number of distinct charts in the setlist
    :param bool defer: whether a deferral has been recorded"""
    if num_players != 2 or num_rounds not in (7, 9):
        raise ValueError("CORP Cup requires two players and best of seven or nine.")
    if num_bans != 2 or ban_ruleset != "bansave":
        raise ValueError("CORP Cup requires four opening ban/save actions.")
    if pick_ruleset != "loserpicks":
        raise ValueError("CORP Cup requires the previous song's loser to pick.")
    if setlist_size != num_rounds + 4:
        raise ValueError("CORP Cup requires 11 group-stage or 13 playoff charts.")
    if defer:
        raise ValueError("CORP Cup does not permit deferral.")


def validate_player_ids(player_ids):
    """Requires two distinct participant identities in seed order.

    :param tuple player_ids: higher-seeded and lower-seeded participant IDs"""
    if len(player_ids) != 2 or None in player_ids or len(set(player_ids)) != 2:
        raise ValueError("CORP Cup requires two distinct assigned players.")


def opening_actor(player_ids, actions):
    """Returns the actor for the next opening action, or none after four.

    :param tuple player_ids: higher-seeded and lower-seeded participant IDs
    :param tuple actions: ordered opening action records"""
    validate_player_ids(player_ids)
    if len(actions) > 4:
        raise ValueError("CORP Cup permits only four opening actions.")
    if len(actions) == 4:
        return None
    return player_ids[(0, 1, 1, 0)[len(actions)]]


def validate_opening_actions(player_ids, chart_ids, actions):
    """Rejects invalid actor order, repeated bans and invalid saves.

    :param tuple player_ids: higher-seeded and lower-seeded participant IDs
    :param tuple chart_ids: distinct setlist chart IDs
    :param tuple actions: ordered opening action records, possibly incomplete"""
    validate_player_ids(player_ids)
    if None in chart_ids or len(set(chart_ids)) != len(chart_ids):
        raise ValueError("The setlist must contain distinct assigned charts.")
    if len(actions) > 4:
        raise ValueError("Bans and saves are limited to four opening actions.")
    previously_banned = set()
    for index, action in enumerate(actions):
        expected_actor = player_ids[(0, 1, 1, 0)[index]]
        if action.num != index or action.player_id != expected_actor:
            raise ValueError("Opening actions must follow higher, lower, lower, higher.")
        if action.chart_id not in chart_ids:
            raise ValueError("An opening action references a chart outside the setlist.")
        if type(action.saved) is not bool:
            raise ValueError("Each opening action must explicitly be a ban or save.")
        if action.saved:
            if index not in (1, 3):
                raise ValueError("Only the second and fourth opening actions may save.")
            previous = actions[index - 1]
            if previous.saved or action.chart_id != previous.chart_id:
                raise ValueError("A save must restore the immediately preceding ban.")
            if action.player_id == previous.player_id:
                raise ValueError("A player cannot save their own ban.")
        elif action.chart_id in previously_banned:
            raise ValueError("A chart cannot be banned again, including after a save.")
        else:
            previously_banned.add(action.chart_id)


def opening_choices(player_ids, chart_ids, actions):
    """Returns legal ban choices and the optional immediately preceding save.

    :param tuple player_ids: higher-seeded and lower-seeded participant IDs
    :param tuple chart_ids: distinct setlist chart IDs in presentation order
    :param tuple actions: ordered opening action records"""
    validate_opening_actions(player_ids, chart_ids, actions)
    if len(actions) == 4:
        return (), ()
    used_chart_ids = {action.chart_id for action in actions}
    ban_ids = tuple(chart_id for chart_id in chart_ids if chart_id not in used_chart_ids)
    save_ids = (actions[-1].chart_id,) if len(actions) in (1, 3) else ()
    return ban_ids, save_ids


def build_corp_cup_selection(
    player_ids, chart_ids, actions, played_chart_ids, recorded_winner_ids, num_rounds,
):
    """Derives the next chart choices from complete opening and round records.

    All supplied rounds must have recorded winners. A pending current round is
    excluded by the caller; screenshot scores do not select a winner.

    :param tuple player_ids: higher-seeded and lower-seeded participant IDs
    :param tuple chart_ids: distinct setlist chart IDs in presentation order
    :param tuple actions: all four ordered opening action records
    :param tuple played_chart_ids: completed round chart IDs in round order
    :param tuple recorded_winner_ids: completed round winners in the same order
    :param int num_rounds: maximum songs, seven or nine"""
    validate_corp_cup_profile(2, num_rounds, 2, "bansave", "loserpicks", len(chart_ids))
    validate_opening_actions(player_ids, chart_ids, actions)
    if len(actions) != 4:
        raise ValueError("All four opening actions must finish before chart selection.")
    if len(played_chart_ids) != len(recorded_winner_ids):
        raise ValueError("Each completed chart requires one recorded winner.")
    if len(set(played_chart_ids)) != len(played_chart_ids):
        raise ValueError("A chart cannot be played twice in the same match.")
    banned_ids = {action.chart_id for action in actions if not action.saved}
    saved_ids = {action.chart_id for action in actions if action.saved}
    effective_bans = banned_ids - saved_ids
    if any(
        chart_id not in chart_ids or chart_id in effective_bans
        for chart_id in played_chart_ids
    ):
        raise ValueError("A played chart is unavailable in this match's setlist.")
    target = (num_rounds + 1) // 2
    wins = dict.fromkeys(player_ids, 0)
    completed_chart_ids = set()
    for chart_id, winner_id in zip(played_chart_ids, recorded_winner_ids):
        if winner_id not in player_ids:
            raise ValueError("Every recorded winner must be a match participant.")
        if target in wins.values():
            raise ValueError("No round may follow the match-winning result.")
        is_tiebreaker_round = all(value == target - 1 for value in wins.values())
        remaining_saved_ids = saved_ids - completed_chart_ids
        if (
            is_tiebreaker_round and len(remaining_saved_ids) == 2
            and chart_id in remaining_saved_ids
        ):
            raise ValueError("Two unplayed saved charts are ineligible for the tiebreaker.")
        wins[winner_id] += 1
        completed_chart_ids.add(chart_id)
    if target in wins.values():
        return CorpCupSelection(None, (), None, False, True)
    is_tiebreaker = all(value == target - 1 for value in wins.values())
    remaining_ids = tuple(
        chart_id for chart_id in chart_ids
        if chart_id not in effective_bans and chart_id not in played_chart_ids
    )
    unplayed_saved_ids = saved_ids - set(played_chart_ids)
    if is_tiebreaker and len(unplayed_saved_ids) == 2:
        remaining_ids = tuple(
            chart_id for chart_id in remaining_ids if chart_id not in unplayed_saved_ids
        )
    if not remaining_ids:
        raise ValueError("No eligible chart remains before the match has completed.")
    if is_tiebreaker and len(remaining_ids) == 1:
        return CorpCupSelection(None, remaining_ids, remaining_ids[0], True, False)
    picker_id = player_ids[0]
    if recorded_winner_ids:
        picker_id = next(
            player_id for player_id in player_ids
            if player_id != recorded_winner_ids[-1]
        )
    return CorpCupSelection(picker_id, remaining_ids, None, is_tiebreaker, False)
