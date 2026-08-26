"""Inspect recency weights for Hirschbach quartet lineup permutations."""
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

from app.analysis_service import (
    _position_index,
    _reference_date,
    _lineup_recency_weight,
    _parse_match_date,
    _cutoff,
    STATS_YEARS,
    LINEUP_RECENCY_HALF_LIFE_DAYS,
)
from app.db import SessionLocal

OPP = ["17245", "18958", "19987", "20837"]  # Rauch, Mayer, Penn, Hammer


def label_order(order, names):
    short = {pid: names.get(pid, pid).split()[0] for pid in order}
    return " / ".join(short[pid] for pid in order)


with SessionLocal() as db:
    ref_date = _reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    bind_names = [f"known_id_{i}" for i in range(len(OPP))]
    id_params = {name: value for name, value in zip(bind_names, OPP)}
    placeholders = ",".join(f":{name}" for name in bind_names)
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team,
               mp.side, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') DESC
        """
        ),
        {"cutoff": stats_cutoff, **id_params},
    ).mappings()

    matches = defaultdict(list)
    names = {}
    required = set(OPP)
    for row in rows:
        matches[row["match_id"]].append(row)
        names.setdefault(str(row["player_id"]), row["player_name"])

    observations = []
    for match_id, players in matches.items():
        by_side = defaultdict(dict)
        for row in players:
            by_side[row["side"]][str(row["player_id"])] = row
        for side, side_players in by_side.items():
            if set(side_players) != required:
                continue
            order = [None] * 4
            valid = True
            for pid in OPP:
                idx = _position_index(side_players[pid]["position"])
                if idx is None or order[idx] is not None:
                    valid = False
                    break
                order[idx] = pid
            if not valid or not all(order):
                continue
            row0 = players[0]
            match_date = row0["match_date"]
            parsed = _parse_match_date(match_date)
            weight = _lineup_recency_weight(match_date, ref_date)
            age = (ref_date - parsed).days if parsed else None
            observations.append(
                {
                    "order": tuple(order),
                    "label": label_order(order, names),
                    "match_date": match_date,
                    "parsed": parsed,
                    "age_days": age,
                    "weight": weight,
                    "match_id": match_id,
                    "side": side,
                    "home": row0["home_team"],
                    "away": row0["away_team"],
                }
            )

    observations.sort(key=lambda x: x["parsed"] or datetime.min, reverse=True)

    print(f"Referenzdatum: {ref_date}")
    print(f"Recency Halbwertszeit: {LINEUP_RECENCY_HALF_LIFE_DAYS} Tage")
    print(f"Gesamt Beobachtungen (exakt Quartett): {len(observations)}")
    print()

    # Per-order aggregates
    by_order = defaultdict(lambda: {"count": 0, "weight_sum": 0.0, "dates": []})
    for obs in observations:
        key = obs["order"]
        by_order[key]["count"] += 1
        by_order[key]["weight_sum"] += obs["weight"]
        by_order[key]["dates"].append(obs)

    total_weight = sum(x["weight_sum"] for x in by_order.values())
    print(f"Summe Recency-Gewichte: {total_weight:.4f}")
    print()
    print("Pro Konstellation (sortiert nach Gewicht):")
    ranked = sorted(by_order.items(), key=lambda x: -x[1]["weight_sum"])
    for order, agg in ranked:
        pct = 100 * agg["weight_sum"] / total_weight
        print(f"  {label_order(order, names)}")
        print(f"    Spiele: {agg['count']}  Gewicht-Summe: {agg['weight_sum']:.4f}  => {pct:.2f}% (nur Historie, vor Blend)")
        for obs in sorted(agg["dates"], key=lambda x: x["parsed"], reverse=True):
            print(
                f"      {obs['match_date']}  age={obs['age_days']}d  w={obs['weight']:.4f}  "
                f"{obs['home']} vs {obs['away']}  match={obs['match_id']}"
            )
        print()

    # Highlight row 3
    row3 = tuple(["18958", "20837", "17245", "19987"])  # Mayer Hammer Rauch Penn
    print("=== Zeile 3: Mayer / Hammer / Rauch / Penn ===")
    if row3 in by_order:
        agg = by_order[row3]
        print(f"Spiele: {agg['count']}, Gewicht: {agg['weight_sum']:.4f}")
        for obs in agg["dates"]:
            print(f"  LETZTES? Datum {obs['match_date']}, age {obs['age_days']}d, w={obs['weight']:.4f}")
            print(f"  {obs['home']} vs {obs['away']}")
    else:
        print("Nicht gefunden")

    print()
    print("=== Alle Spiele chronologisch (neueste zuerst) ===")
    for obs in observations[:10]:
        print(f"{obs['match_date']}  {obs['label']}  w={obs['weight']:.4f}  {obs['home']} vs {obs['away']}")
