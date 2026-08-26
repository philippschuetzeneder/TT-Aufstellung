"""Historical match counts per Hirschbach lineup permutation (Trak vs Hirschbach example)."""
from collections import Counter, defaultdict

from sqlalchemy import text

from app.analysis_service import (
    _known_quartet_lineup_scenarios,
    _load_analysis_data,
    _position_index,
    _reference_date,
    _sharpen_scenarios,
    SCENARIO_SHARPENING_ALPHA,
    _cutoff,
    STATS_YEARS,
)
from app.db import SessionLocal

OWN = ["21773", "23754", "24890", "24889"]
OPP_TEAM = "Hirschbach 1"
OPP = ["17245", "18958", "19987", "20837"]


def raw_order_counts(db, player_ids, ref_date):
    """Integer match counts per A/B/C/D order for exact quartet (cross-team)."""
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f"known_id_{i}" for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ",".join(f":{name}" for name in bind_names)
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, mp.side, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
        """
        ),
        {"cutoff": stats_cutoff, **id_params},
    ).mappings()
    matches = defaultdict(list)
    names = {}
    required = set(ids)
    for row in rows:
        matches[row["match_id"]].append(row)
        names.setdefault(str(row["player_id"]), row["player_name"])

    counts = Counter()
    for players in matches.values():
        by_side = defaultdict(dict)
        for row in players:
            by_side[row["side"]][str(row["player_id"])] = row
        for side_players in by_side.values():
            if set(side_players) != required:
                continue
            order = [None] * 4
            valid = True
            for pid in ids:
                idx = _position_index(side_players[pid]["position"])
                if idx is None or order[idx] is not None:
                    valid = False
                    break
                order[idx] = pid
            if valid and all(order):
                counts[tuple(order)] += 1

    return counts, names, sum(counts.values())


with SessionLocal() as db:
    ref_date = _reference_date(db)
    names, profiles, matchups, scenarios, source, ref_date, opponent_pool = _load_analysis_data(
        OWN, OPP_TEAM, OPP, use_spieltyp=False,
    )
    raw_scenarios = list(scenarios)
    model_scenarios = _sharpen_scenarios(list(scenarios))
    model_by_order = {order: prob for prob, order in model_scenarios}
    order_counts, hist_names, total_exact = raw_order_counts(db, OPP, ref_date)
    _, _, joint_weighted_total = _known_quartet_lineup_scenarios(db, OPP, ref_date)

    print(f"Tragwein/Kamig 3 vs {OPP_TEAM}")
    print(f"Bekannte Gegner: {', '.join(hist_names.get(p, p) for p in OPP)}")
    print(f"Quartett gesamt (exakt diese 4): {total_exact} Spiele in Datenbasis")
    print(f"Recency-gewichtete Masse (für P): {joint_weighted_total:.2f}")
    print(f"Sharpening α={SCENARIO_SHARPENING_ALPHA}")
    print()
    print("| # | Spiele | P historisch % | P Modell % | A | B | C | D |")
    print("|---:|:---:|:---:|:---:|:---|:---|:---|:---|")

    ranked = sorted(raw_scenarios, key=lambda x: -x[0])
    for i, (pr, order) in enumerate(ranked, 1):
        ps = model_by_order.get(order, pr)
        n = order_counts.get(order, 0)
        cols = [names.get(pid, pid).split()[0] for pid in order]
        print(
            f"| {i} | {n} | {pr*100:5.2f} | {ps*100:5.2f} | "
            f"{cols[0]} | {cols[1]} | {cols[2]} | {cols[3]} |"
        )

    print(f"\nSumme Spiele in Tabelle: {sum(order_counts.get(o, 0) for _, o in ranked)}")
    print(f"Summe P historisch: {sum(p for p, _ in ranked)*100:.2f} %")
