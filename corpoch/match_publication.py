"""Publishes delayed match evidence without overwriting sporting records."""

from corpoch.match_actions import MatchActionError, locked_match
from corpoch.models import Match, MatchRound


def has_recorded_evidence(round_record, expected_players=None):
    """
    Checks recorded evidence references without reading a file or decoding an image

    :param MatchRound round_record: Fresh round record
    :param int expected_players: Required metadata player count when checking completion
    :return: Whether a file reference and player metadata are recorded"""
    metadata = round_record.steg
    players = metadata.get("players") if isinstance(metadata, dict) else getattr(metadata, "players", None)
    return bool(
        round_record.screenshot and isinstance(players, list) and players
        and (expected_players is None or len(players) == expected_players)
    )


def publish_round_evidence(
    match_id, round_id, chart_id, screenshot_name, steg, *,
    expected_screenshot=None, expected_state=None, using="default",
):
    """
    Publishes already stored evidence after rechecking its match and chart binding

    Storage and decoding must finish before this function; no file is opened here.
    A rejected upload may leave an unreferenced stored file for operator cleanup.

    :param str match_id: Official match identifier
    :param int round_id: Round selected before decoding
    :param int chart_id: Chart selected before decoding
    :param str screenshot_name: Stored file name from save=False
    :param object steg: Decoded metadata
    :param str expected_screenshot: Previously recorded file name, or None for empty
    :param str expected_state: Sporting snapshot token captured before decoding
    :param str using: Database alias
    :return: Fresh match record"""
    if not isinstance(screenshot_name, str) or not screenshot_name:
        raise MatchActionError("The screenshot has no stored file reference.")
    with locked_match(match_id, expected_state=expected_state, using=using) as match:
        if not match.complete:
            raise MatchActionError("Finalize the match before publishing screenshots.")
        try:
            round_record = MatchRound.objects.using(using).get(pk=round_id, match=match)
        except MatchRound.DoesNotExist as error:
            raise MatchActionError("The selected round no longer exists.") from error
        if round_record.chart_id != chart_id or chart_id is None:
            raise MatchActionError("The round's chart changed. Upload its current result.")
        if str(round_record.screenshot or "") != str(expected_screenshot or ""):
            raise MatchActionError("The round's screenshot changed. Reload before replacing it.")
        if steg is None:
            raise MatchActionError("The screenshot has no decoded metadata.")
        MatchRound.objects.using(using).filter(pk=round_record.pk).update(
            screenshot=screenshot_name, steg=steg,
        )
        Match.objects.using(using).filter(pk=match.pk).update(finished=False)
    return Match.objects.using(using).get(pk=match_id)


def finish_match_evidence(match_id, *, using="default"):
    """
    Marks evidence complete only when fresh finalized rounds all have recorded data

    :param str match_id: Official match identifier
    :param str using: Database alias
    :return: Fresh match record"""
    with locked_match(match_id, using=using) as match:
        rounds = list(MatchRound.objects.using(using).filter(match=match))
        if not match.complete or not rounds or not all(
            has_recorded_evidence(row, match.ruleset.num_players) for row in rounds
        ):
            raise MatchActionError("The finalized match still needs screenshot evidence.")
        Match.objects.using(using).filter(pk=match.pk).update(finished=True)
    return Match.objects.using(using).get(pk=match_id)


def publish_export_status(match_id, *, submitted=True, expected_state=None, using="default"):
    """
    Changes only the local export flag after an external export or explicit reset

    :param str match_id: Official match identifier
    :param bool submitted: Whether an export was recorded
    :param str expected_state: Sporting snapshot token captured before export
    :param str using: Database alias"""
    with locked_match(match_id, expected_state=expected_state, using=using) as match:
        if submitted and not match.finished:
            raise MatchActionError("The match changed before export publication.")
        Match.objects.using(using).filter(pk=match.pk).update(submitted=submitted)
