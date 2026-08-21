"""Show placement logic: Philipp vs Hildner for TRAK vs Sandl Phase B."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import permutations

from app.analysis_service import (
    SINGLES_SCHEDULE,
    _build_matchup_table,
    _load_analysis_data,
    _matchup_probability,
    analyze_lineup,
)

OWN = ["21773", "24890", "23782", "24889"]
OPP_KNOWN = ["70433", "70417", "22938", "23882"]
OPP_TEAM = "Sandl 1"
NAMES = {
    "21773": "Schützeneder Philipp",
    "24890": "Dreiling Tobias",
    "23782": "Prantner Bernhard",
    "24889": "Nötstaller Sebastian",
    "70433": "Vater Hanna",
    "70417": "Riepl Melanie",
    "22938": "Hildner Oliver",
    "23882": "Stifter Florian",
}


def short(pid: str) -> str:
    return NAMES.get(pid, pid).split()[-1]


def show_lineup(own_order, opp_order, own_is_home=True):
    schedule = SINGLES_SCHEDULE if own_is_home else tuple((a, h) for h, a in SINGLES_SCHEDULE)
    print(f"\nEigene Reihenfolge A-D: {[short(p) for p in own_order]}")
    print(f"Gegner Reihenfolge 1-4: {[short(p) for p in opp_order]}")
    pair_counts = Counter()
    for own_idx, opp_idx in schedule:
        pair = (own_order[own_idx], opp_order[opp_idx])
        pair_counts[pair] += 1
    print("Singles-Paarungen (Anzahl Spiele):")
    for (a, b), n in sorted(pair_counts.items(), key=lambda x: -x[1]):
        print(f"  {short(a)} vs {short(b)}: {n}x")


def main() -> None:
    r = analyze_lineup(
        OWN,
        OPP_TEAM,
        actual_opponent_ids=OPP_KNOWN,
        own_is_home=True,
        use_spieltyp=True,
    )
    rec = r["recommendation"]
    own_order = rec["own_player_ids"]
    print("Empfohlene Aufstellung:", [short(p) for p in own_order])

    scenarios = r.get("opponent_predictions") or []
    print("\nGegner-Szenarien (Phase B, 24 Permutationen gleich wahrscheinlich):")
    for item in scenarios[:5]:
        print(f"  {item['probability']*100:.1f}%", [short(p) for p in item["player_ids"]])
    print(f"  ... {len(scenarios)} Szenarien total")

    names, profiles, matchups, raw_scenarios, *_ = _load_analysis_data(
        OWN, OPP_TEAM, OPP_KNOWN, use_spieltyp=True
    )
    relevant = set(OWN) | set(OPP_KNOWN)
    matchup_p = _build_matchup_table(relevant, profiles, matchups, True, use_spieltyp=True)

    # Weighted expected opponent at each guest slot (index 0..3)
    slot_weight = [0.0] * 4
    opp_at_slot = [Counter() for _ in range(4)]
    for prob, opp_order in raw_scenarios:
        for idx, pid in enumerate(opp_order):
            slot_weight[idx] += prob
            opp_at_slot[idx][pid] += prob

    print("\nWahrscheinlichster Gegner pro Gast-Platz (1–4), gewichtet über alle Szenarien:")
    for idx in range(4):
        best = opp_at_slot[idx].most_common(3)
        parts = ", ".join(f"{short(pid)} {p*100:.0f}%" for pid, p in best)
        print(f"  Platz {idx + 1}: {parts}")

    # Philipp vs Oliver: which own slot meets Oliver most across schedule?
    philipp_id = "21773"
    oliver_id = "22938"
    philipp_idx = own_order.index(philipp_id)
    print(f"\nPhilipp steht auf Platz {'ABCD'[philipp_idx]} (Index {philipp_idx})")

    meet_prob = 0.0
    meet_games = 0
    for prob, opp_order in raw_scenarios:
        oliver_idx = opp_order.index(oliver_id)
        games = sum(
            1
            for own_i, opp_i in SINGLES_SCHEDULE
            if own_order[own_i] == philipp_id and opp_order[opp_i] == oliver_id
        )
        if games:
            meet_prob += prob
            meet_games = games

    print(f"Philipp trifft Oliver in {meet_games} Singles pro Szenario (wenn Oliver in gegn. Aufstellung)")
    print(f"Anteil der Szenarien mit Philipp–Oliver-Begegnung: {meet_prob*100:.0f}%")

    # Compare all 24 own permutations - is current best partly due to Philipp vs Oliver?
    print("\nTop 5 eigene Aufstellungen nach Siegchance:")
    for item in r["recommendations"][:5]:
        print(f"  {item['team_win_probability']*100:.1f}%", [short(p) for p in item["own_player_ids"]])

    # H2H Philipp vs Oliver
    p_po = matchup_p.get((philipp_id, oliver_id), 0.5)
    wins, games = matchups.get((philipp_id, oliver_id), (0, 0))
    print(f"\nDirektmatch Philipp vs Oliver: {p_po*100:.1f}% Siegchance ({wins}/{games} H2H)")

    # Show recommended vs most likely single opponent permutation
    if raw_scenarios:
        show_lineup(own_order, raw_scenarios[0][1])
        # permutation where Oliver on slot that faces Philipp most
        best_opp = max(
            raw_scenarios[0][1] if len(raw_scenarios) == 1 else opp
            for _, opp in raw_scenarios
            if oliver_id in opp
            for _ in [None]
        )
        # find opp order maximizing philipp-oliver games
        best_meet = 0
        best_order = raw_scenarios[0][1]
        for _, opp_order in raw_scenarios:
            g = sum(
                1
                for own_i, opp_i in SINGLES_SCHEDULE
                if own_order[own_i] == philipp_id and opp_order[opp_i] == oliver_id
            )
            if g > best_meet:
                best_meet = g
                best_order = opp_order
        show_lineup(own_order, best_order)


if __name__ == "__main__":
    main()
