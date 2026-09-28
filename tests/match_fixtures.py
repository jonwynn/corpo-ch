"""Creates isolated official matches using the real model schema."""

from corpoch.models import (
    Bracket, Chart, CHIcon, Group, GroupSeed, Match, Tournament, TournamentPlayer,
)
from corpoch.types import CH_Name, PlayerConfig


def create_corp_match(num_rounds=7, match_id="fixture-corp-match"):
    """Creates a complete CORP configuration without external services.

    :param int num_rounds: Seven for groups or nine for playoffs
    :param str match_id: Unique fixture match identifier"""
    tournament = Tournament(name="Fixture Cup", short_name="FIX")
    tournament.save()
    bracket = Bracket(tournament=tournament, name="Fixture Bracket", revealed=True)
    bracket.save()
    rules = bracket.ruleset
    rules.num_players = 2
    rules.num_rounds = num_rounds
    rules.num_bans = 2
    rules.ban_ruleset = "bansave"
    rules.pick_ruleset = "loserpicks"
    rules.tb_ruleset = "corp_cup"
    rules.save()
    group = Group.objects.create(bracket=bracket, name="A")
    players = []
    for seed, name in ((1, "Higher Seed"), (2, "Lower Seed")):
        player = TournamentPlayer.objects.create(
            tournament=tournament, name=name, is_active=True,
            config=PlayerConfig(names_list=[CH_Name(ch_name=name, is_primary=True)]),
        )
        players.append(GroupSeed.objects.create(group=group, player=player, seed=seed))
    icon, created = CHIcon.objects.get_or_create(name="ch_default_icon")
    charts = []
    for number in range(num_rounds + 4):
        chart = Chart(name=f"Fixture Song {number + 1}", icon=icon)
        chart.save()
        chart.brackets.add(bracket)
        charts.append(chart)
    match = Match(id=match_id, group=group)
    match.save()
    match.players.set(players)
    return match, players, charts
