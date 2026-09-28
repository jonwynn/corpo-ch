"""Builds a read-only match presentation from an already scoped snapshot."""

import hashlib
import json


def normalise_identifier(value):
    """
    Converts primitive record identifiers without accepting containers or booleans

    :param object value: Snapshot identifier
    :return: String identifier or None"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    return None


def add_issue(issues, issue):
    """
    Adds a distinct, non-sensitive quality reason

    :param list issues: Ordered quality reasons
    :param str issue: Stable reason code"""
    if issue not in issues:
        issues.append(issue)


def valid_integer(value, minimum=0):
    """
    Checks a non-boolean integer against its lower bound

    :param object value: Candidate number
    :param int minimum: Lowest accepted value
    :return: Whether the number is valid"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def normalise_choice(value, choices, default=None):
    """
    Reads a known string choice without accepting arbitrary source values

    :param object value: Candidate choice
    :param set choices: Accepted strings
    :param object default: Value for a missing or malformed choice
    :return: Accepted choice or default"""
    return value if isinstance(value, str) and value in choices else default


def identity_sort_key(value):
    """
    Sorts numeric database keys numerically and fixture keys deterministically

    :param object value: Record identifier
    :return: Comparable sort key"""
    identifier = normalise_identifier(value) or ""
    if identifier.isdecimal():
        return (0, int(identifier))
    return (1, identifier)


def order_snapshot_records(source, key, identifier_key, issues):
    """
    Validates and orders primitive actions or rounds without changing the input

    :param dict source: Scoped snapshot
    :param str key: Collection name
    :param str identifier_key: Record identifier field
    :param list issues: Quality reasons
    :return: Ordered record copies"""
    records = source.get(key)
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        add_issue(issues, f"invalid_{key}")
        return []
    ordered = []
    identifiers = set()
    for item in records:
        record = dict(item)
        identifier = normalise_identifier(record.get(identifier_key))
        if identifier is None or identifier in identifiers:
            add_issue(issues, f"invalid_{key}_identity")
        identifiers.add(identifier)
        record[identifier_key] = identifier
        if not valid_integer(record.get("num"), 1 if key == "rounds" else 0):
            add_issue(issues, f"invalid_{key}_number")
        for field in ("chart_id", "player_id", "picked_id", "winner_id"):
            if field in record:
                identifier = normalise_identifier(record[field])
                if record[field] is not None and identifier is None:
                    add_issue(issues, f"invalid_{key}_identity")
                record[field] = identifier
        ordered.append(record)
    return sorted(
        ordered,
        key=lambda item: (
            item["num"] if valid_integer(item.get("num")) else -1,
            identity_sort_key(item.get(identifier_key)),
        ),
    )


def build_player_slots(source, pins, issues):
    """
    Validates two participant identities and preserves accepted display pins

    :param dict source: Scoped snapshot
    :param object pins: Ordered slot pairs or pairs with assignment context
    :param list issues: Quality reasons
    :return: Ordered player copies, or None for unavailable slots"""
    players = source.get("players", [])
    if not isinstance(players, list) or len(players) != 2:
        add_issue(issues, "participant_count")
        return None
    if any(not isinstance(player, dict) for player in players):
        add_issue(issues, "missing_player")
        return None
    players = [dict(player) for player in players]
    for player in players:
        player["player_id"] = normalise_identifier(player.get("player_id"))
        player["seed_id"] = normalise_identifier(player.get("seed_id"))
    if any(not player["player_id"] for player in players):
        add_issue(issues, "missing_player")
        return None
    if len({player["player_id"] for player in players}) != 2:
        add_issue(issues, "duplicate_player")
        return None
    if any(not player["seed_id"] for player in players) or len(
        {player["seed_id"] for player in players},
    ) != 2:
        add_issue(issues, "invalid_seed_identity")
        return None
    if any(not valid_integer(player.get("seed"), 1) for player in players):
        add_issue(issues, "invalid_seed")
        return None
    if players[0]["seed"] == players[1]["seed"]:
        add_issue(issues, "tied_seeds")
    players.sort(key=lambda player: (player["seed"], identity_sort_key(player["seed_id"])))
    if source.get("rev_seeds") is True:
        players.reverse()
    if pins is None:
        return players
    if isinstance(pins, dict):
        if (
            normalise_identifier(pins.get("group_id"))
            != normalise_identifier(source.get("group_id"))
            or pins.get("rev_seeds") is not (source.get("rev_seeds") is True)
        ):
            add_issue(issues, "assignment_changed")
            return None
        pins = pins.get("players")
    if not isinstance(pins, list) or len(pins) != 2 or any(
        not isinstance(pin, dict) for pin in pins
    ):
        add_issue(issues, "invalid_pins")
        return None
    pairs = [
        (normalise_identifier(pin.get("seed_id")), normalise_identifier(pin.get("player_id")))
        for pin in pins
    ]
    lookup = {(player["seed_id"], player["player_id"]): player for player in players}
    if len(set(pairs)) != 2 or set(pairs) != set(lookup):
        add_issue(issues, "assignment_changed")
        return None
    return [lookup[pair] for pair in pairs]


def build_rule_summary(source, issues):
    """
    Exposes a target only for a valid, explicitly supported profile

    :param dict source: Scoped snapshot and rule validation result
    :param list issues: Quality reasons
    :return: Safe rule values and target"""
    rules = source.get("rules")
    if not isinstance(rules, dict):
        add_issue(issues, "missing_rules")
        return {"supported": False, "target": None}
    number = rules.get("num_rounds")
    pick_ruleset = normalise_choice(rules.get("pick_ruleset"), {"loserpicks", "alternate"})
    ban_ruleset = normalise_choice(rules.get("ban_ruleset"), {"default", "deferban", "deferboth", "bansave"})
    tb_ruleset = normalise_choice(
        rules.get("tb_ruleset"),
        {"single", "csc", "banpick", "refdecide", "bansave", "corp_cup"},
    )
    supported = (
        rules.get("num_players") == 2
        and valid_integer(number, 3)
        and number <= 25
        and number % 2 == 1
        and valid_integer(rules.get("num_bans"), 1)
        and rules["num_bans"] <= 4
        and pick_ruleset is not None
        and ban_ruleset is not None
        and tb_ruleset is not None
        and (rules.get("fixture_only") is True or rules.get("profile_supported") is True)
    )
    if not supported:
        add_issue(issues, "unsupported_rules")
    return {
        "supported": supported,
        "fixture_only": rules.get("fixture_only") is True,
        "num_players": rules.get("num_players") if valid_integer(rules.get("num_players")) else None,
        "num_bans": rules.get("num_bans") if valid_integer(rules.get("num_bans")) else None,
        "num_rounds": number if valid_integer(number) else None,
        "pick_ruleset": pick_ruleset,
        "ban_ruleset": ban_ruleset,
        "tb_ruleset": tb_ruleset,
        "target": (number + 1) // 2 if supported else None,
    }


def build_chart_presentation(record, revealed):
    """
    Removes withheld chart identifiers and titles from every presented record

    :param dict record: Source action or round
    :param bool revealed: Whether the selected bracket releases chart titles
    :return: Allowlisted chart presentation"""
    if not record.get("chart_id"):
        return {"visibility": "unavailable", "label": "Chart unavailable"}
    if not revealed or record.get("chart_visible") is False:
        return {"visibility": "withheld", "label": "Chart withheld"}
    title = record.get("chart_title")
    if not isinstance(title, str) or not title.strip():
        return {"id": record["chart_id"], "visibility": "unavailable", "label": "Chart unavailable"}
    return {"id": record["chart_id"], "title": title, "visibility": "visible", "label": title}


def has_selection(record):
    """
    Checks retained selection evidence without treating a blank round as a pick

    :param dict record: Source round
    :return: Whether selection evidence survives"""
    return bool(
        record.get("chart_id")
        or record.get("winner_id")
        or normalise_choice(record.get("selection_kind"), {"player", "referee", "automatic"})
    )


def build_action_state(actions, slots, wins, target, rules, revealed, issues):
    """
    Separates surviving action history from visible effective chart exclusions

    :param list actions: Ordered action records
    :param list slots: Validated player IDs, or None
    :param list wins: Recorded wins, or None
    :param int target: Validated target, or None
    :param dict rules: Safe rule summary
    :param bool revealed: Selected bracket visibility
    :param list issues: Quality reasons
    :return: Action presentation and hidden chart identities for internal counting"""
    slot_lookup = {player_id: f"p{index + 1}" for index, player_id in enumerate(slots or [])}
    saved_charts = {record["chart_id"] for record in actions if record.get("saved") is True and record.get("chart_id")}
    opening_ids = []
    effective_ids = []
    presented_actions = []
    hidden_charts = set()
    for record in actions:
        phase = normalise_choice(record.get("action_phase"), {"opening", "tiebreaker"}, "unknown")
        if phase == "unknown":
            add_issue(issues, "action_phase_unknown")
        if phase == "opening":
            opening_ids.append(record["action_id"])
        if phase == "tiebreaker" and (
            not wins or target is None or min(wins) < target - 1 or rules.get("tb_ruleset") == "corp_cup"
        ):
            add_issue(issues, "tiebreaker_provenance_inconsistent")
        chart = build_chart_presentation(record, revealed)
        player_slot = slot_lookup.get(record.get("player_id"))
        if slots and player_slot is None:
            add_issue(issues, "action_owner_unavailable")
        if chart["visibility"] == "withheld":
            hidden_charts.add(record["chart_id"])
        elif (
            record.get("chart_id")
            and record["chart_id"] not in saved_charts
            and record["chart_id"] not in effective_ids
        ):
            effective_ids.append(record["chart_id"])
        presented_actions.append({
            "action_id": record["action_id"],
            "number": record.get("num") if valid_integer(record.get("num")) else None,
            "player_slot": player_slot,
            "player_label": player_slot.upper() if player_slot else "Player unavailable",
            "chart": chart,
            "saved": record.get("saved") is True,
            "action_phase": phase,
        })

    return presented_actions, opening_ids, effective_ids, hidden_charts


def build_round_state(rounds, slots, revealed, issues):
    """
    Projects history, latest personal picks and evidence from the same rounds

    :param list rounds: Ordered round records
    :param list slots: Validated player IDs, or None
    :param bool revealed: Selected bracket visibility
    :param list issues: Quality reasons
    :return: Round presentation, history, latest picks, evidence and hidden identities"""
    slot_lookup = {player_id: f"p{index + 1}" for index, player_id in enumerate(slots or [])}
    hidden_charts = set()
    latest_picks = [None, None] if slots else None
    history = []
    presented_rounds = []
    evidence = []
    for record in rounds:
        selected = has_selection(record)
        kind = normalise_choice(record.get("selection_kind"), {"player", "referee", "automatic"}, "unknown")
        if kind == "unknown":
            if selected:
                add_issue(issues, "selection_kind_unknown")
        picker_slot = slot_lookup.get(record.get("picked_id")) if kind == "player" else None
        if kind == "player" and picker_slot is None:
            add_issue(issues, "picker_unavailable")
        winner_slot = slot_lookup.get(record.get("winner_id"))
        if record.get("winner_id") and winner_slot is None:
            round_state = "needs_review"
        elif selected and not record.get("chart_id"):
            add_issue(issues, "selected_chart_missing")
            round_state = "result_recorded" if winner_slot else "chart_unavailable"
        elif winner_slot:
            round_state = "result_recorded"
        else:
            round_state = "awaiting_result" if selected else "awaiting_selection"
        if selected and picker_slot and latest_picks is not None:
            latest_picks[slots.index(record["picked_id"])] = record["round_id"]
        chart = (
            build_chart_presentation(record, revealed)
            if selected else {"visibility": "not_selected", "label": "No chart selected"}
        )
        if chart["visibility"] == "withheld":
            hidden_charts.add(record["chart_id"])
        row = {
            "round_id": record["round_id"],
            "number": record.get("num") if valid_integer(record.get("num"), 1) else None,
            "state": round_state,
            "winner_slot": winner_slot,
        }
        history.append(row)
        presented_rounds.append({**row, "chart": chart, "selection_kind": kind, "picker_slot": picker_slot})
        metadata = normalise_choice(
            record.get("metadata_kind"),
            {"absent", "empty", "dummy", "malformed", "invalid", "v6", "v10", "manual", "present"},
            "absent",
        )
        if metadata in {"dummy", "malformed", "invalid"}:
            add_issue(issues, "metadata_unverified")
        evidence.append({
            "round_id": record["round_id"],
            "file_reference_recorded": record.get("screenshot_present") is True,
            "metadata_present": metadata not in {None, "absent", "empty"},
            "verified": False,
        })
    return presented_rounds, history, latest_picks, evidence, hidden_charts


def build_lifecycle_state(source, slots, wins, target, issues):
    """
    Keeps official finalization distinct from evidence and export flags

    :param dict source: Scoped snapshot
    :param list slots: Validated player IDs, or None
    :param list wins: Recorded wins, or None
    :param int target: Validated target, or None
    :param list issues: Quality reasons
    :return: Safe lifecycle values"""
    lifecycle = source.get("lifecycle") if isinstance(source.get("lifecycle"), dict) else {}
    complete = lifecycle.get("complete") is True
    finished = lifecycle.get("finished") is True
    submitted = lifecycle.get("submitted") is True
    official_winner = normalise_identifier(lifecycle.get("winner_id"))
    target_reached = wins is not None and target is not None and any(value >= target for value in wins)
    if complete and wins is not None and target is not None:
        winners = [slots[index] for index, value in enumerate(wins) if value >= target]
        if len(winners) != 1 or official_winner != winners[0]:
            add_issue(issues, "final_winner_mismatch")
    if (finished and not complete) or (submitted and not finished):
        add_issue(issues, "lifecycle_inconsistent")
    if wins is not None and target is not None and (sum(value >= target for value in wins) > 1 or max(wins) > target):
        add_issue(issues, "score_exceeds_target")
    slot_lookup = {player_id: f"p{index + 1}" for index, player_id in enumerate(slots or [])}
    return {
        "target_reached": target_reached,
        "result_finalized": complete,
        "evidence_finished": finished,
        "export_recorded": submitted,
        "winner_slot": slot_lookup.get(official_winner),
    }


def resolve_presentation_state(slots, target, history, status, issues):
    """
    Selects one match label without conflating setup, rules and completion

    :param list slots: Validated player IDs, or None
    :param int target: Validated target, or None
    :param list history: Presented round history
    :param dict status: Lifecycle flags
    :param list issues: Quality reasons
    :return: Stable match state code"""
    review_reasons = {
        "duplicate_round_number", "outsider_winner", "invalid_rounds", "invalid_rounds_identity",
        "invalid_rounds_number", "invalid_actions", "invalid_actions_identity", "invalid_actions_number",
        "selected_chart_missing", "final_winner_mismatch", "lifecycle_inconsistent",
        "score_exceeds_target", "tiebreaker_provenance_inconsistent", "picker_unavailable", "tied_seeds",
        "noncontiguous_round_numbers", "nonfinal_pending_round", "round_after_decisive_result",
        "corp_history_invalid",
    }
    if slots is None:
        state = (
            "setup_changed"
            if any(issue in issues for issue in ("assignment_changed", "invalid_pins"))
            else "setup_incomplete"
        )
    elif any(issue in review_reasons for issue in issues):
        state = "needs_review"
    elif target is None:
        state = "unsupported_rules"
    elif status["result_finalized"]:
        state = "complete"
    elif status["target_reached"]:
        state = "awaiting_finalization"
    elif not history:
        state = "opening_bans"
    elif history[-1]["state"] == "result_recorded":
        state = "awaiting_next_round"
    else:
        state = history[-1]["state"]

    return state


def build_presented_players(players, wins, remaining, latest_picks):
    """
    Allowlists display identity and values while retaining complete player names

    :param list players: Ordered valid source players, or None
    :param list wins: Recorded wins, or None
    :param list remaining: Remaining wins, or None
    :param list latest_picks: Verified latest round IDs, or None
    :return: Safe player records"""
    presented_players = []
    for index, player in enumerate(players or []):
        name = player.get("name")
        if not isinstance(name, str) or not name.strip() or name == "</Null>":
            name = "Name unavailable"
        presented_players.append({
            "slot": f"p{index + 1}", "seed_id": player["seed_id"], "player_id": player["player_id"],
            "name": name, "overline": None, "seed": player["seed"],
            "wins": wins[index] if wins is not None else None,
            "remaining_wins": remaining[index] if remaining is not None else None,
            "latest_pick_round_id": latest_picks[index],
        })
    return presented_players


def build_match_presentation(source, pins=None):
    """
    Builds a privacy-filtered, read-only match presentation

    The caller supplies an already scoped primitive snapshot. This function does
    not authenticate a request or replace database visibility checks. Returned
    strings remain untrusted text for an escaping template; no HTML is marked safe.

    :param dict source: Primitive snapshot matching the viewer fixture source
    :param object pins: Optional validated-shape slot pairs and assignment context
    :return: Primitive presentation with no raw source objects or provider data"""
    if not isinstance(source, dict):
        raise TypeError("Match snapshot must be a dictionary.")
    access = source.get("access")
    if not isinstance(access, dict):
        access = {}
    allowed = (
        access.get("authenticated") is True
        and access.get("active") is True
        and (
            access.get("superuser") is True
            or normalise_choice(access.get("same_guild_role"), {"admin", "referee"}) is not None
        )
    )
    if not allowed:
        return {
            "contract_version": "1.0.0",
            "access": "denied",
            "state": "access_denied" if access.get("authenticated") is True else "authentication_required",
            "quality": [],
        }

    issues = []
    if source.get("history_valid") is False:
        add_issue(issues, "corp_history_invalid")
    players = build_player_slots(source, pins, issues)
    slots = [player["player_id"] for player in players] if players else None
    slot_lookup = {player_id: f"p{index + 1}" for index, player_id in enumerate(slots or [])}
    rules = build_rule_summary(source, issues)
    target = rules["target"]
    actions = order_snapshot_records(source, "actions", "action_id", issues)
    rounds = order_snapshot_records(source, "rounds", "round_id", issues)
    withheld_chart_ids = {
        record["chart_id"]
        for record in actions + rounds
        if record.get("chart_id") and record.get("chart_visible") is False
    }
    for record in actions + rounds:
        if record.get("chart_id") in withheld_chart_ids:
            record["chart_visible"] = False
    numbers = [record["num"] for record in rounds if valid_integer(record.get("num"), 1)]
    if len(numbers) != len(set(numbers)):
        add_issue(issues, "duplicate_round_number")
    elif len(numbers) == len(rounds) and numbers != list(range(1, len(rounds) + 1)):
        add_issue(issues, "noncontiguous_round_numbers")
    if any(record.get("winner_id") is None for record in rounds[:-1]):
        add_issue(issues, "nonfinal_pending_round")
    if slots and target is not None:
        prefix_wins = dict.fromkeys(slots, 0)
        for record in rounds:
            if any(value >= target for value in prefix_wins.values()):
                add_issue(issues, "round_after_decisive_result")
            if record.get("winner_id") in prefix_wins:
                prefix_wins[record["winner_id"]] += 1
    if any(record.get("winner_id") and record["winner_id"] not in slot_lookup for record in rounds) and slots:
        add_issue(issues, "outsider_winner")
    invalid_score = any(
        issue in issues
        for issue in (
            "duplicate_round_number", "outsider_winner", "invalid_rounds",
            "invalid_rounds_identity", "invalid_rounds_number", "noncontiguous_round_numbers",
        )
    )
    wins = None if slots is None or invalid_score else [
        sum(record.get("winner_id") == player_id for record in rounds)
        for player_id in slots
    ]
    remaining = [max(target - value, 0) for value in wins] if wins is not None and target is not None else None
    revealed = source.get("bracket_revealed") is True
    presented_actions, opening_ids, effective_ids, hidden_actions = build_action_state(
        actions, slots, wins, target, rules, revealed, issues,
    )
    presented_rounds, history, latest_picks, evidence, hidden_rounds = build_round_state(
        rounds, slots, revealed, issues,
    )
    hidden_charts = hidden_actions | hidden_rounds
    if hidden_charts:
        add_issue(issues, "titles_withheld")

    status = build_lifecycle_state(source, slots, wins, target, issues)
    panel_mode = "latest_picks" if any(has_selection(record) for record in rounds) else (
        "recorded_actions" if "action_phase_unknown" in issues else "initial_actions"
    )
    state = resolve_presentation_state(slots, target, history, status, issues)
    presented_players = build_presented_players(players, wins, remaining, latest_picks)
    presented_round_lookup = {record["round_id"]: record for record in presented_rounds}
    for player in presented_players:
        player_actions = [record for record in presented_actions if record["player_slot"] == player["slot"]]
        player["opening_actions"] = [record for record in player_actions if record["action_phase"] == "opening"]
        player["recorded_actions"] = [record for record in player_actions if record["action_phase"] != "tiebreaker"]
        player["latest_pick"] = presented_round_lookup.get(player["latest_pick_round_id"])
    context = source.get("context") if isinstance(source.get("context"), dict) else {}
    presentation = {
        "contract_version": "1.0.0",
        "match_id": normalise_identifier(source.get("match_id")),
        "access": "allowed", "state": state, "panel_mode": panel_mode,
        "slots": slots, "wins": wins, "target": target, "remaining": remaining,
        "current_round": history[-1]["round_id"] if history else None,
        "current_selection": presented_rounds[-1] if presented_rounds else None,
        "round_history": history, "opening_action_ids": opening_ids,
        "effective_ban_chart_ids": effective_ids, "latest_picks": latest_picks,
        "quality": issues, "hidden_chart_count": len(hidden_charts),
        "players": presented_players, "rules": rules,
        "actions": presented_actions, "rounds": presented_rounds, "evidence": evidence,
        "context": {
            key: context[key]
            for key in ("tournament", "bracket", "group")
            if isinstance(context.get(key), str)
        },
        "status": status,
        "assignment": {
            "group_id": normalise_identifier(source.get("group_id")),
            "rev_seeds": source.get("rev_seeds") is True,
            "players": [{"seed_id": player["seed_id"], "player_id": player["player_id"]} for player in players or []],
        },
    }
    presentation["digest"] = hashlib.sha256(
        json.dumps(presentation, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    return presentation
