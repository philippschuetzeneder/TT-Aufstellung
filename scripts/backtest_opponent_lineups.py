"""
Backtest opponent lineup predictions (known 4 vs known 4).

Compares sharpening alpha values on leakage-safe scenario forecasts.
Does not modify app code — script-only experiment.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import permutations
from pathlib import Path

from sqlalchemy import text

from app.analysis_service import (
    STATS_YEARS,
    _build_known_four_opponent_scenarios,
    _build_combined_prior_scenarios,
    _cutoff,
    _lineup_cohesion,
    _lineup_cohesion_cache,
    _load_known_quartet_joint_scenarios,
    _load_player_position_priors,
    _load_latest_rc_map,
    _known_quartet_lineup_scenarios,
    _parse_match_date,
    _position_index,
    _reference_date,
    _sharpen_scenarios,
)
from app.db import SessionLocal

ALPHAS = [1.0, 2.0, 2.5]
_EPS = 1e-12

_LEAKAGE_REF: date | None = None
_ORIG_REFERENCE_DATE = _reference_date


def _patched_reference_date(db):
    if _LEAKAGE_REF is not None:
        return _LEAKAGE_REF
    return _ORIG_REFERENCE_DATE(db)


def _date_upper_sql() -> str:
    if _LEAKAGE_REF is None:
        return ""
    return " AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') <= :ref_end"


def _known_quartet_lineup_scenarios_lb(db, player_ids, ref_date=None, team=None):
    """Leakage-safe variant of _known_quartet_lineup_scenarios."""
    ref_date = ref_date or _patched_reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f"known_id_{i}" for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ",".join(f":{name}" for name in bind_names)
    team_clause = ""
    params = {"cutoff": stats_cutoff, "ref_end": _LEAKAGE_REF, **id_params}
    if team:
        team_clause = (
            "AND ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))"
        )
        params["team"] = team
    upper = _date_upper_sql()
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, mp.side, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          {team_clause}
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
        """
        ),
        params,
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
                from app.analysis_service import _lineup_recency_weight

                counts[tuple(order)] += _lineup_recency_weight(
                    players[0].get("match_date"), ref_date,
                )

    total = sum(counts.values())
    if not total:
        return [], names, 0
    return [(count / total, order) for order, count in counts.most_common(24)], names, total


def _lineup_cohesion_lb(db, player_ids, ref_date=None):
    """Leakage-safe _lineup_cohesion."""
    actual = {str(x) for x in player_ids}
    if len(actual) != 4:
        return 0, 0.0, 0, 0.0
    ref_date = ref_date or _patched_reference_date(db)
    cache_key = (str(_cutoff(ref_date, STATS_YEARS)), str(_LEAKAGE_REF), ",".join(sorted(actual)))
    cached = _lineup_cohesion_cache.get(cache_key)
    if cached is not None:
        return cached
    upper = _date_upper_sql()
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, mp.side, mp.external_player_id::text AS player_id
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
        """
        ),
        {"cutoff": _cutoff(ref_date, STATS_YEARS), "ref_end": _LEAKAGE_REF},
    ).mappings()
    from app.analysis_service import _lineup_recency_weight
    from itertools import combinations

    exact = 0
    exact_mass = 0.0
    trio_counts = Counter()
    trio_masses = Counter()
    by_lineup = {}
    for row in rows:
        key = (row["match_id"], row["side"])
        entry = by_lineup.setdefault(key, {"players": set(), "match_date": row["match_date"]})
        entry["players"].add(str(row["player_id"]))
    for entry in by_lineup.values():
        lineup = entry["players"]
        overlap = lineup & actual
        weight = _lineup_recency_weight(entry["match_date"], ref_date)
        if len(overlap) == 4:
            exact += 1
            exact_mass += weight
        if len(overlap) >= 3:
            for trio in combinations(sorted(overlap), 3):
                trio_counts[trio] += 1
                trio_masses[trio] += weight
    best_trio = max(trio_counts, key=trio_counts.get) if trio_counts else None
    result = (
        exact,
        exact_mass,
        trio_counts.get(best_trio, 0),
        trio_masses.get(best_trio, 0.0),
    )
    _lineup_cohesion_cache[cache_key] = result
    return result


def _load_player_position_priors_lb(db, player_ids, ref_date=None, team=None):
    ref_date = ref_date or _patched_reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f"pid_{i}" for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ",".join(f":{name}" for name in bind_names)
    team_clause = ""
    params = {"cutoff": stats_cutoff, "ref_end": _LEAKAGE_REF, **id_params}
    if team:
        team_clause = (
            "AND ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))"
        )
        params["team"] = team
    upper = _date_upper_sql()
    from app.analysis_service import POSITION_PRIOR_SMOOTHING

    rows = db.execute(
        text(
            f"""
        SELECT mp.external_player_id::text AS player_id, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          {team_clause}
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
        """
        ),
        params,
    ).mappings()
    counts = {pid: [0.0, 0.0, 0.0, 0.0] for pid in ids}
    for row in rows:
        pid = str(row["player_id"])
        idx = _position_index(row["position"])
        if pid in counts and idx is not None:
            counts[pid][idx] += 1.0
    alpha = POSITION_PRIOR_SMOOTHING
    priors = {}
    for pid in ids:
        c = counts[pid]
        denom = sum(c) + 4 * alpha
        priors[pid] = [(x + alpha) / denom for x in c]
    return priors


def _load_latest_rc_map_lb(db, ids):
    ids = [str(x) for x in ids]
    if not ids or _LEAKAGE_REF is None:
        return _load_latest_rc_map(db, ids)
    from sqlalchemy import bindparam

    stmt = text(
        """
        SELECT xp.external_player_id::text AS player_id, snap.rc_rating
        FROM xttv_players xp
        JOIN LATERAL (
            SELECT rc_rating
            FROM player_rating_snapshots
            WHERE player_id = xp.id AND source = 'ratingscentral'
              AND observed_at <= :ref_end
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    out = {}
    for row in db.execute(stmt, {"ids": ids, "ref_end": _LEAKAGE_REF}).mappings():
        if row["rc_rating"] is not None:
            out[str(row["player_id"])] = float(row["rc_rating"])
    return out


def _load_known_quartet_joint_scenarios_lb(db, player_ids, ref_date):
    actual = [str(x) for x in player_ids]
    scenarios, names, joint_n = _known_quartet_lineup_scenarios_lb(db, actual, ref_date)
    return scenarios, joint_n, names, "raw-cross-team"


def _adaptive_strength_weight_lb(db, player_ids, ref_date=None):
    from app.analysis_service import (
        COHESIVE_QUARTET_MIN_MATCHES,
        COHESIVE_QUARTET_MIN_RECENCY_MASS,
        COHESIVE_TRIO_MIN_MATCHES,
        COHESIVE_TRIO_MIN_RECENCY_MASS,
        MEDIUM_GROUP_MIN_MATCHES,
    )

    exact, exact_mass, trio, trio_mass = _lineup_cohesion_lb(db, player_ids, ref_date)
    if exact >= COHESIVE_QUARTET_MIN_MATCHES and exact_mass >= COHESIVE_QUARTET_MIN_RECENCY_MASS:
        return 0.15, exact, trio
    if trio >= COHESIVE_TRIO_MIN_MATCHES and trio_mass >= COHESIVE_TRIO_MIN_RECENCY_MASS:
        return 0.30, exact, trio
    if exact >= MEDIUM_GROUP_MIN_MATCHES and exact_mass >= 2.0:
        return 0.40, exact, trio
    if exact == 0 and trio == 0:
        return 0.80, exact, trio
    if exact <= 1 and trio <= 1:
        return 0.80, exact, trio
    return 0.70, exact, trio


def _build_combined_prior_scenarios_lb(db, player_ids, ref_date, strength_weight=None):
    from app.analysis_service import (
        _normalize_scenario_map,
        _scenarios_from_position_priors,
        _scenarios_from_strength_prior,
        _blend_scenario_maps,
    )

    actual = [str(x) for x in player_ids]
    position_priors = _load_player_position_priors_lb(db, actual, ref_date)
    position_scenarios = _scenarios_from_position_priors(actual, position_priors)
    if strength_weight is None:
        strength_weight, exact_n, trio_n = _adaptive_strength_weight_lb(db, actual, ref_date)
    else:
        exact_n, _, trio_n, _ = _lineup_cohesion_lb(db, actual, ref_date)
    if strength_weight <= 0:
        return position_scenarios, "known-opponent-position-prior-cross-team"
    rc_map = _load_latest_rc_map_lb(db, actual)
    strength_scenarios = _scenarios_from_strength_prior(actual, rc_map)
    prior_map = {order: prob for prob, order in position_scenarios}
    strength_map = {order: prob for prob, order in strength_scenarios}
    blended = _blend_scenario_maps(prior_map, strength_map, 1.0 - strength_weight)
    scenarios = sorted(
        [(prob, order) for order, prob in blended.items()],
        key=lambda item: (-item[0], item[1]),
    )
    return scenarios, (
        f"known-opponent-position-strength-prior-cross-team"
        f"(strength-w={strength_weight:.2f},exact4={exact_n},best3={trio_n})"
    )


def _build_known_four_opponent_scenarios_lb(db, opponent_team, player_ids, ref_date):
    from app.analysis_service import _normalize_scenario_map, _blend_scenario_maps

    actual = [str(x) for x in player_ids]
    joint_scenarios, joint_n, fallback_names, joint_source = _load_known_quartet_joint_scenarios_lb(
        db, actual, ref_date,
    )
    strength_weight, exact_n, trio_n = _adaptive_strength_weight_lb(db, actual, ref_date)
    prior_scenarios, prior_source = _build_combined_prior_scenarios_lb(
        db, actual, ref_date, strength_weight=strength_weight,
    )
    if joint_n <= 0:
        return prior_scenarios, fallback_names, prior_source
    joint_weight = 1.0 - strength_weight
    joint_map = _normalize_scenario_map({order: prob for prob, order in joint_scenarios})
    prior_map = {order: prob for prob, order in prior_scenarios}
    scenarios = sorted(
        [(prob, order) for order, prob in _blend_scenario_maps(
            joint_map, prior_map, joint_weight,
        ).items()],
        key=lambda item: (-item[0], item[1]),
    )
    return scenarios, fallback_names, (
        f"known-opponent-blended-{joint_source}"
        f"(history-w={joint_weight:.2f},exact4={exact_n},best3={trio_n})"
    )


@contextlib.contextmanager
def leakage_safe(ref_end: date):
    global _LEAKAGE_REF
    import app.analysis_service as svc

    old_ref = _LEAKAGE_REF
    old_fns = {
        "_reference_date": svc._reference_date,
        "_known_quartet_lineup_scenarios": svc._known_quartet_lineup_scenarios,
        "_lineup_cohesion": svc._lineup_cohesion,
        "_load_player_position_priors": svc._load_player_position_priors,
        "_load_latest_rc_map": svc._load_latest_rc_map,
        "_load_known_quartet_joint_scenarios": svc._load_known_quartet_joint_scenarios,
        "_adaptive_strength_weight": svc._adaptive_strength_weight,
        "_build_combined_prior_scenarios": svc._build_combined_prior_scenarios,
        "_build_known_four_opponent_scenarios": svc._build_known_four_opponent_scenarios,
    }
    _LEAKAGE_REF = ref_end
    svc._reference_date = _patched_reference_date
    svc._known_quartet_lineup_scenarios = _known_quartet_lineup_scenarios_lb
    svc._lineup_cohesion = _lineup_cohesion_lb
    svc._load_player_position_priors = _load_player_position_priors_lb
    svc._load_latest_rc_map = _load_latest_rc_map_lb
    svc._load_known_quartet_joint_scenarios = _load_known_quartet_joint_scenarios_lb
    svc._adaptive_strength_weight = _adaptive_strength_weight_lb
    svc._build_combined_prior_scenarios = _build_combined_prior_scenarios_lb
    svc._build_known_four_opponent_scenarios = _build_known_four_opponent_scenarios_lb
    try:
        yield
    finally:
        _LEAKAGE_REF = old_ref
        for name, fn in old_fns.items():
            setattr(svc, name, fn)
        _lineup_cohesion_cache.clear()


def extract_side_order(players: list[dict]) -> tuple[str, ...] | None:
    order = [None] * 4
    ids = []
    for row in players:
        pid = str(row["player_id"])
        idx = _position_index(row["position"])
        if idx is None or order[idx] is not None:
            return None
        order[idx] = pid
        ids.append(pid)
    if not all(order) or len(set(ids)) != 4:
        return None
    return tuple(order)


def load_test_cases(db, min_test_date: date | None, limit: int | None):
    params = {}
    date_clause = ""
    if min_test_date:
        date_clause = (
            " AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :min_test_date"
        )
        params["min_test_date"] = min_test_date
    limit_clause = ""
    if limit:
        limit_clause = " LIMIT :limit"
        params["limit"] = limit
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team,
               mp.side, mp.external_player_id AS player_id, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND m.match_date IS NOT NULL
          {date_clause}
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
        {limit_clause}
        """
        ),
        params,
    ).mappings().all()

    by_match = defaultdict(lambda: defaultdict(list))
    meta = {}
    for row in rows:
        mid = row["match_id"]
        meta[mid] = {
            "match_date": row["match_date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
        }
        by_match[mid][row["side"]].append(row)

    cases = []
    for mid, sides in by_match.items():
        info = meta[mid]
        parsed = _parse_match_date(info["match_date"])
        if not parsed:
            continue
        for side, team_key in [("home", "home_team"), ("away", "away_team")]:
            if len(sides.get(side, [])) != 4:
                continue
            order = extract_side_order(sides[side])
            if not order:
                continue
            cases.append(
                {
                    "match_id": mid,
                    "match_date": parsed,
                    "opponent_team": info[team_key],
                    "opponent_ids": list(order),
                    "actual_order": order,
                }
            )
    return cases


def predict_scenarios(db, opponent_team: str, opponent_ids: list[str], ref_end: date):
    with leakage_safe(ref_end):
        scenarios, _, _ = _build_known_four_opponent_scenarios(
            db, opponent_team, opponent_ids, ref_end,
        )
    if not scenarios:
        uniform = 1.0 / 24.0
        scenarios = [(uniform, tuple(o)) for o in permutations(opponent_ids)]
    return list(scenarios)


def prob_map(scenarios):
    return {tuple(order): prob for prob, order in scenarios}


def run_backtest(cases, alphas, progress_every=500):
    totals = {
        alpha: {"top1": 0, "top3": 0, "nll": 0.0, "p_actual": 0.0, "n": 0}
        for alpha in alphas
    }
    totals["uniform"] = {"top1": 0, "top3": 0, "nll": 0.0, "p_actual": 0.0, "n": 0}
    cohesion_buckets = {
        "0_exact": {a: {"top1": 0, "n": 0} for a in alphas},
        "1-3_exact": {a: {"top1": 0, "n": 0} for a in alphas},
        "4+_exact": {a: {"top1": 0, "n": 0} for a in alphas},
    }
    scenario_cache: dict[tuple, list] = {}
    cohesion_cache: dict[tuple, int] = {}

    db = SessionLocal()
    try:
        for i, case in enumerate(cases, 1):
            ref_end = case["match_date"] - timedelta(days=1)
            cache_key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
            if cache_key not in scenario_cache:
                scenario_cache[cache_key] = predict_scenarios(
                    db, case["opponent_team"], case["opponent_ids"], ref_end,
                )
            base_scenarios = scenario_cache[cache_key]

            if cache_key not in cohesion_cache:
                with leakage_safe(ref_end):
                    exact_before, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
                cohesion_cache[cache_key] = exact_before
            exact_before = cohesion_cache[cache_key]
            if exact_before == 0:
                bucket = "0_exact"
            elif exact_before <= 3:
                bucket = "1-3_exact"
            else:
                bucket = "4+_exact"

            actual = tuple(case["actual_order"])
            p_uni = 1.0 / 24.0
            uniform_scenarios = [(p_uni, tuple(o)) for o in permutations(case["opponent_ids"])]
            ranked_uni = sorted(uniform_scenarios, key=lambda x: (-x[0], x[1]))
            totals["uniform"]["n"] += 1
            totals["uniform"]["top1"] += int(ranked_uni[0][1] == actual)
            totals["uniform"]["top3"] += int(actual in {o for _, o in ranked_uni[:3]})
            totals["uniform"]["p_actual"] += p_uni
            totals["uniform"]["nll"] += -math.log(p_uni)

            for alpha in alphas:
                scenarios = _sharpen_scenarios(list(base_scenarios), alpha=alpha)
                pmap = prob_map(scenarios)
                p_actual = pmap.get(actual, 0.0)
                ranked = sorted(scenarios, key=lambda x: (-x[0], x[1]))
                top1_hit = ranked[0][1] == actual
                top3_hit = actual in {o for _, o in ranked[:3]}

                totals[alpha]["n"] += 1
                totals[alpha]["top1"] += int(top1_hit)
                totals[alpha]["top3"] += int(top3_hit)
                totals[alpha]["p_actual"] += p_actual
                totals[alpha]["nll"] += -math.log(max(p_actual, _EPS))
                cohesion_buckets[bucket][alpha]["n"] += 1
                cohesion_buckets[bucket][alpha]["top1"] += int(top1_hit)

            if progress_every and i % progress_every == 0:
                print(f"  ... {i}/{len(cases)} cases", flush=True)
    finally:
        db.close()

    return totals, cohesion_buckets


def fmt_pct(hit, n):
    return f"{100 * hit / n:.2f}%" if n else "—"


def main():
    parser = argparse.ArgumentParser(description="Backtest opponent lineup predictions")
    parser.add_argument(
        "--min-test-date",
        default="2024-01-01",
        help="Only evaluate matches on/after this date (YYYY-MM-DD)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit raw SQL rows (debug)")
    parser.add_argument(
        "--max-cases",
        type=int,
        default=4000,
        help="Random sample size after loading (0 = all)",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for sampling")
    parser.add_argument("--output", default=None, help="Write JSON summary to path")
    args = parser.parse_args()

    min_test_date = date.fromisoformat(args.min_test_date)
    print("=== Gegner-Aufstellungs-Backtest (bekanntes Quartett) ===")
    print(f"Testfenster: ab {min_test_date}")
    print(f"Sharpening-Vergleich: α ∈ {ALPHAS}")
    print(f"Leakage: nur Daten bis Tag vor Spiel")
    if args.max_cases:
        print(f"Stichprobe: max {args.max_cases} Fälle (seed={args.seed})")
    print()

    db = SessionLocal()
    try:
        cases = load_test_cases(db, min_test_date, args.limit)
    finally:
        db.close()

    if not cases:
        print("Keine Testfälle gefunden.")
        return 1

    if args.max_cases and len(cases) > args.max_cases:
        rng = random.Random(args.seed)
        cases = rng.sample(cases, args.max_cases)

    print(f"Testfälle ausgewertet: {len(cases)}")

    totals, cohesion_buckets = run_backtest(cases, ALPHAS)

    n = totals[ALPHAS[0]]["n"]
    print()
    print("| Modell | Top-1 exakt | Top-3 Treffer | Ø P(tatsächlich) | Ø NLL |")
    print("|:---|:---:|:---:|:---:|:---:|")
    for label, key in [
        ("Uniform 1/24", "uniform"),
        (f"α=1 (kein Sharpening)", 1.0),
        ("α=2", 2.0),
        ("α=2,5 (Prod)", 2.5),
    ]:
        t = totals[key]
        print(
            f"| {label} | {fmt_pct(t['top1'], t['n'])} | {fmt_pct(t['top3'], t['n'])} | "
            f"{t['p_actual'] / t['n']:.4f} | {t['nll'] / t['n']:.4f} |"
        )

    print()
    print("Top-1 nach Quartett-Historie vor dem Spiel (exakte 4er-Spiele):")
    print("| Historie | n | α=1 | α=2 | α=2,5 |")
    print("|:---|:---:|:---:|:---:|:---:|")
    for bucket, label in [
        ("0_exact", "0 Spiele"),
        ("1-3_exact", "1–3 Spiele"),
        ("4+_exact", "≥4 Spiele"),
    ]:
        b = cohesion_buckets[bucket]
        row_n = b[1.0]["n"]
        cols = [fmt_pct(b[a]["top1"], b[a]["n"]) for a in ALPHAS]
        print(f"| {label} | {row_n} | {cols[0]} | {cols[1]} | {cols[2]} |")

    summary = {
        "min_test_date": str(min_test_date),
        "cases": n,
        "alphas": ALPHAS,
        "metrics": {
            str(k): {
                "top1_pct": totals[k]["top1"] / totals[k]["n"],
                "top3_pct": totals[k]["top3"] / totals[k]["n"],
                "mean_p_actual": totals[k]["p_actual"] / totals[k]["n"],
                "mean_nll": totals[k]["nll"] / totals[k]["n"],
            }
            for k in ["uniform", *ALPHAS]
        },
        "cohesion_buckets": {
            bucket: {
                str(alpha): {
                    "n": cohesion_buckets[bucket][alpha]["n"],
                    "top1_pct": (
                        cohesion_buckets[bucket][alpha]["top1"] / cohesion_buckets[bucket][alpha]["n"]
                        if cohesion_buckets[bucket][alpha]["n"]
                        else None
                    ),
                }
                for alpha in ALPHAS
            }
            for bucket in cohesion_buckets
        },
    }

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nJSON: {args.output}")

    best_alpha = min(ALPHAS, key=lambda a: totals[a]["nll"] / totals[a]["n"])
    print(f"\nBestes α (niedrigster Ø NLL): **{best_alpha}**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
