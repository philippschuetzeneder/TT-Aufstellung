"""Why are top own lineups almost tied? Phase B: 4 own + 4 opp known, opp order uncertain.

Run on prod: docker compose exec -T app python scripts/lineup_margin_sensitivity.py \\
  --own 23784,... --opp ... --own-team 'Tragwein/Kamig 2' --opponent 'Allerheiligen 1'
"""
from __future__ import annotations

import argparse
import math
import sys
from copy import deepcopy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import (
    SCENARIO_SHARPENING_ALPHA,
    _adaptive_strength_weight,
    _lineup_cohesion,
    _load_analysis_data,
    _load_player_profiles,
    _reference_date,
    _resolve_rc_rating,
    _sharpen_scenarios,
    analyze_lineup,
)
from app.db import SessionLocal


def _margin_pp(recs: list[dict], k: int = 5) -> dict:
    if not recs:
        return {"top1_win_pct": None, "margin_1_2_pp": None, f"loss_rank_{k + 1}_pp": None}
    top = recs[0]["team_win_probability"]
    m12 = (top - recs[1]["team_win_probability"]) * 100 if len(recs) > 1 else 0.0
    loss_k = (top - recs[min(k, len(recs) - 1)]["team_win_probability"]) * 100
    return {
        "top1_win_pct": round(top * 100, 3),
        "margin_1_2_pp": round(m12, 4),
        f"loss_rank_{k + 1}_pp": round(loss_k, 4),
        "lineup_spread_pp": recs[0].get("lineup_spread_pp"),
    }


def _scenario_stats(scenarios: list[tuple[float, tuple]]) -> dict:
    if not scenarios:
        return {}
    probs = [p for p, _ in scenarios]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    max_ent = math.log(len(probs)) if probs else 0.0
    return {
        "n_scenarios": len(scenarios),
        "top_scenario_pct": round(max(probs) * 100, 2),
        "entropy": round(entropy, 3),
        "entropy_ratio": round(entropy / max_ent, 3) if max_ent else None,
    }


def _rc_spread(db, ids: list[str]) -> float | None:
    from app.analysis_service import _load_latest_rc_map

    rc = _load_latest_rc_map(db, ids)
    vals = [_resolve_rc_rating(pid, rc, ids) for pid in ids]
    vals = [v for v in vals if v is not None]
    return round(max(vals) - min(vals), 1) if len(vals) >= 2 else None


def run_once(own, opp, own_team, opponent, *, sharpen_alpha: float | None = None, scenarios_override=None):
    saved_alpha = mod.SCENARIO_SHARPENING_ALPHA
    if sharpen_alpha is not None:
        mod.SCENARIO_SHARPENING_ALPHA = sharpen_alpha
    mod.clear_analysis_runtime_caches()
    try:
        if scenarios_override is not None:
            names, profiles, matchups, _sc, source, ref_date, pool, _w = _load_analysis_data(
                own, opponent, opp,
            )
            scenarios = _sharpen_scenarios(scenarios_override)
            with SessionLocal() as db:
                from app.analysis_service import _load_doubles_stats, _evaluate_lineups, _merge_orientations

                doubles_stats = _load_doubles_stats(db, own, set(opp), own_team, opponent, ref_date)
            started = __import__("time").monotonic()
            home_eval, _ = _evaluate_lineups(
                own, scenarios, profiles, matchups, names, True, started,
                doubles_stats=doubles_stats, own_on_letters=False,
            )
            away_eval, _ = _evaluate_lineups(
                own, scenarios, profiles, matchups, names, False, started,
                doubles_stats=doubles_stats, own_on_letters=False,
            )
            evaluated = _merge_orientations(home_eval, away_eval)
            evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
            for i, item in enumerate(evaluated, 1):
                item["rank"] = i
            opt = evaluated[0]["team_win_probability"]
            for item in evaluated:
                item["loss_pp_vs_optimal"] = round(max(0, (opt - item["team_win_probability"]) * 100), 2)
            rec = evaluated[0]
            rec["lineup_spread_pp"] = mod._compute_lineup_configuration_spread_pp(
                own, scenarios, profiles, matchups, names,
                doubles_stats=doubles_stats, own_on_letters=False, own_is_home=True, started=started,
            )
            return {
                "recommendation": rec,
                "recommendations": evaluated,
                "opponent_set_source": source + "+override",
            }
        return analyze_lineup(
            own,
            opponent,
            actual_opponent_ids=opp,
            own_is_home=True,
            own_team=own_team,
            opponent_on_letters=True,
        )
    finally:
        mod.SCENARIO_SHARPENING_ALPHA = saved_alpha
        mod.clear_analysis_runtime_caches()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--own", required=True, help="comma-separated PassNr")
    p.add_argument("--opp", required=True, help="comma-separated PassNr")
    p.add_argument("--own-team", default="Tragwein/Kamig 2")
    p.add_argument("--opponent", default="Allerheiligen 1")
    args = p.parse_args()
    own = [x.strip() for x in args.own.split(",") if x.strip()]
    opp = [x.strip() for x in args.opp.split(",") if x.strip()]
    if len(own) != 4 or len(opp) != 4:
        raise SystemExit("need exactly 4 own and 4 opp ids")

    with SessionLocal() as db:
        ref = _reference_date(db)
        sw, exact, trio = _adaptive_strength_weight(db, opp, ref)
        ex_n, ex_m, tr_n, tr_m = _lineup_cohesion(db, opp, ref)
        names, profiles, _m = _load_player_profiles(db, own + opp, ref)
        rc_spread_own = _rc_spread(db, own)
        rc_spread_opp = _rc_spread(db, opp)
        _names2, _p2, _m2, scenarios, source, _rd, _pool, _w = _load_analysis_data(
            own, args.opponent, opp,
        )

    print(f"Referenzdatum: {ref}")
    print(f"Gegner-Szenarien-Quelle: {source}")
    print(f"RC-Spread Heim: {rc_spread_own}  Gast-Quartett: {rc_spread_opp}")
    print(
        f"Gast-Quartett Kohäsion: exact4={exact} (mass={ex_m:.2f}), trio={trio} (mass={tr_m:.2f}) "
        f"=> adaptive strength_weight≈{sw:.2f}"
    )
    print(f"Szenario-Statistik (vor Schärfung in analyze): {_scenario_stats(scenarios)}")
    sharpened = _sharpen_scenarios(scenarios)
    print(f"Nach SCENARIO_SHARPENING_ALPHA={SCENARIO_SHARPENING_ALPHA}: {_scenario_stats(sharpened)}")

    print("\nStärke / RC:")
    for pid in own + opp:
        side = "heim" if pid in own else "gast"
        pr = profiles.get(pid, {})
        print(
            f"  [{side}] {names.get(pid, pid):28} RC={pr.get('rc_rating')} "
            f"games={pr.get('games', 0)}"
        )

    baseline = run_once(own, opp, args.own_team, args.opponent)
    recs = baseline.get("recommendations") or []
    print("\n=== Baseline (Prod-Modell) ===")
    print(_margin_pp(recs, k=5))
    print(f"  optimal: {baseline.get('recommendation', {}).get('players')}")
    if len(recs) > 1:
        print(f"  #2 loss_pp={recs[1].get('loss_pp_vs_optimal')}")

    from itertools import permutations

    uniform = [(1 / 24.0, o) for o in permutations(opp)]
    top_only = [max(scenarios, key=lambda x: x[0])] if scenarios else uniform[:1]

    counterfactuals = [
        ("alpha=1.0 (keine Schärfung)", {"sharpen_alpha": 1.0}),
        ("alpha=5.0 (stärkere Schärfung)", {"sharpen_alpha": 5.0}),
        ("Gegner-Szenarien uniform 1/24", {"scenarios_override": uniform}),
        ("nur wahrscheinlichstes Gegner-Szenario", {"scenarios_override": top_only}),
    ]
    print("\n=== Was würde den Abstand vergrößern? (Gegenfakten) ===")
    base_loss6 = _margin_pp(recs, k=5).get("loss_rank_6_pp")
    for label, kw in counterfactuals:
        r = run_once(own, opp, args.own_team, args.opponent, **kw)
        ev = r.get("recommendations") or []
        m = _margin_pp(ev, k=5)
        delta = None
        if base_loss6 is not None and m.get("loss_rank_6_pp") is not None:
            delta = round(m["loss_rank_6_pp"] - base_loss6, 4)
        print(f"  {label}: margin_1_2={m.get('margin_1_2_pp')} pp, rank6-loss={m.get('loss_rank_6_pp')} pp (Δ rank6 {delta})")


if __name__ == "__main__":
    main()
