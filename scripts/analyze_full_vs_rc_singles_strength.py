"""
Compare team win probability: full singles strength vs RC-only singles strength.

Same optimal lineup (from full model), same opponent (Phase C).
Only the Einzelduell-Wahrscheinlichkeiten change:
  - full: RC + Trend + Heim/Gast + H2H (Produktionsmodell)
  - rc_singles: nur RC-Logistic, kein H2H/Trend/Heim
Doubles bleiben im Produktionsmodell.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as svc  # noqa: E402
from app.analysis_service import (  # noqa: E402
    RC_BASELINE,
    RC_COMPONENT_WEIGHT,
    RC_SCALE,
    _empty_profile,
    _logistic,
)
from app.db import SessionLocal  # noqa: E402
from backtest_team_result_2526 import (  # noqa: E402
    MAX_RUNTIME_SEC,
    _PROFILE_CACHE,
    _load_analysis_data_backtest,
    backtest_context,
    load_cases,
    parse_team_result,
)

OUT_PATH = SCRIPT_DIR / "output" / "full_vs_rc_singles_strength_2526.json"


def _rc_singles_matchup(a: str, b: str, profiles: dict) -> float:
    own = profiles.get(a, _empty_profile())
    opp = profiles.get(b, _empty_profile())
    own_rc = own.get("rc_rating")
    opp_rc = opp.get("rc_rating")
    if own_rc is None or opp_rc is None:
        return 0.5
    own_strength = ((float(own_rc) - RC_BASELINE) / RC_SCALE) * RC_COMPONENT_WEIGHT
    opp_strength = ((float(opp_rc) - RC_BASELINE) / RC_SCALE) * RC_COMPONENT_WEIGHT
    return _logistic(own_strength - opp_strength)


def _build_matchup_tables(relevant: set[str], profiles: dict, matchups: dict, own_is_home: bool):
    full = svc._build_matchup_table(relevant, profiles, matchups, own_is_home, use_spieltyp=False)
    rc_only = {
        (a, b): _rc_singles_matchup(a, b, profiles)
        for a in relevant for b in relevant if a != b
    }
    return full, rc_only


def _team_win_for_order(
    own_order: list[str],
    scenarios,
    profiles: dict,
    matchups: dict,
    matchup_p: dict,
    *,
    opponent_on_letters: bool,
) -> float:
    own_on_letters = None if opponent_on_letters is None else not opponent_on_letters
    schedule = svc._schedule_for_orientation(
        True if own_on_letters is None else own_on_letters,
    )
    pair_a, pair_b = svc._default_double_pairs(own_order, profiles)
    scenario_cache = svc._build_scenario_doubles_cache(
        scenarios, pair_a, pair_b, profiles, {}, stronger_double_pair=1,
    )
    _, agg, _, _, _, _ = svc._evaluate_lineup_for_perm(
        own_order, scenario_cache, matchup_p, schedule,
    )
    return float(agg["win"])


def _mean_singles_win(own_order: list[str], opp_order: tuple[str, ...], matchup_p: dict, *, opponent_on_letters: bool) -> float:
    own_on_letters = None if opponent_on_letters is None else not opponent_on_letters
    schedule = svc._schedule_for_orientation(
        True if own_on_letters is None else own_on_letters,
    )
    singles = [
        matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5)
        for own_idx, opp_idx in schedule
    ]
    return statistics.mean(singles)


def evaluate_case(case: dict, ref_end) -> dict | None:
    fixed_opp = tuple(case["opp_ids"])
    cache_key = (ref_end, frozenset(case["own_ids"]), frozenset(fixed_opp), False)
    if cache_key in _PROFILE_CACHE:
        names, profiles, matchups, scenarios = _PROFILE_CACHE[cache_key]
    else:
        with backtest_context(ref_end, rc_only=False, fixed_opp_order=fixed_opp):
            names, profiles, matchups, scenarios, _, _, _, _ = _load_analysis_data_backtest(
                case["own_ids"], case["away_team"], list(case["opp_ids"]),
            )
        if not scenarios:
            return None
        _PROFILE_CACHE[cache_key] = (names, profiles, matchups, scenarios)

    import time as _time

    relevant = set(case["own_ids"]) | set(fixed_opp)
    full_p, rc_p = _build_matchup_tables(relevant, profiles, matchups, True)

    with backtest_context(ref_end, rc_only=False, fixed_opp_order=fixed_opp):
        started = _time.monotonic()
        own_on_letters = None if case["opponent_on_letters"] is None else not case["opponent_on_letters"]
        evaluated, _ = svc._evaluate_lineups(
            case["own_ids"],
            scenarios,
            profiles,
            matchups,
            names,
            True,
            started,
            use_spieltyp=False,
            doubles_stats={},
            own_on_letters=own_on_letters,
        )
    if not evaluated:
        return None

    evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
    optimal_order = evaluated[0]["own_player_ids"]

    win_full = _team_win_for_order(
        optimal_order, scenarios, profiles, matchups, full_p,
        opponent_on_letters=case["opponent_on_letters"],
    )
    win_rc = _team_win_for_order(
        optimal_order, scenarios, profiles, matchups, rc_p,
        opponent_on_letters=case["opponent_on_letters"],
    )
    singles_full = _mean_singles_win(
        optimal_order, fixed_opp, full_p, opponent_on_letters=case["opponent_on_letters"],
    )
    singles_rc = _mean_singles_win(
        optimal_order, fixed_opp, rc_p, opponent_on_letters=case["opponent_on_letters"],
    )

    return {
        "team_win_full": win_full,
        "team_win_rc_singles": win_rc,
        "delta_team_win": win_full - win_rc,
        "singles_mean_full": singles_full,
        "singles_mean_rc": singles_rc,
        "delta_singles_mean": singles_full - singles_rc,
    }


def aggregate(rows: list[dict], kind: str) -> dict:
    if kind == "team_win":
        full_key, rc_key, delta_key = "team_win_full", "team_win_rc_singles", "delta_team_win"
    else:
        full_key, rc_key, delta_key = "singles_mean_full", "singles_mean_rc", "delta_singles_mean"
    n = len(rows)
    deltas = [r[delta_key] for r in rows]
    full = [r[full_key] for r in rows]
    rc = [r[rc_key] for r in rows]
    sd = sorted(deltas)
    return {
        "n": n,
        "mean_full_pct": round(statistics.mean(full) * 100, 3),
        "mean_rc_only_pct": round(statistics.mean(rc) * 100, 3),
        "mean_delta_pp": round(statistics.mean(deltas) * 100, 3),
        "median_delta_pp": round(statistics.median(deltas) * 100, 3),
        "full_higher_pct": round(sum(1 for d in deltas if d > 1e-9) / n * 100, 2),
        "equal_pct": round(sum(1 for d in deltas if abs(d) <= 1e-9) / n * 100, 2),
        "full_lower_pct": round(sum(1 for d in deltas if d < -1e-9) / n * 100, 2),
        "min_delta_pp": round(min(deltas) * 100, 3),
        "max_delta_pp": round(max(deltas) * 100, 3),
        "p10_delta_pp": round(sd[int(0.10 * (n - 1))] * 100, 3),
        "p90_delta_pp": round(sd[int(0.90 * (n - 1))] * 100, 3),
    }


def run(cases: list[dict], started: float) -> dict:
    rows = []
    errors = []
    for i, case in enumerate(cases, 1):
        if time.monotonic() - started > MAX_RUNTIME_SEC:
            break
        ref_end = case["match_date"] - timedelta(days=1)
        if not parse_team_result(case["team_result"]):
            continue
        try:
            result = evaluate_case(case, ref_end)
        except Exception as exc:
            errors.append({"match_id": case["match_id"], "error": str(exc)})
            continue
        if not result:
            continue
        rows.append({"match_id": case["match_id"], **result})
        if i % 50 == 0:
            print(f"  ... {i}/{len(cases)} ({time.monotonic() - started:.0f}s)", flush=True)

    return {
        "description": (
            "Gleiche optimale Aufstellung (Vollmodell). "
            "Einzel-Wahrscheinlichkeiten: Produktions-Spielstärke vs. nur RC. "
            "Doppel unverändert Produktionsmodell."
        ),
        "season": "2025/2026",
        "perspective": "home_only",
        "phase": "C",
        "cases_total": len(cases),
        "cases_processed": len(rows),
        "runtime_sec": round(time.monotonic() - started, 1),
        "team_win_comparison": aggregate(rows, "team_win"),
        "singles_mean_comparison": aggregate(rows, "singles"),
        "errors": errors[:20],
    }


def main() -> int:
    started = time.monotonic()
    print("Loading cases...", flush=True)
    db = SessionLocal()
    try:
        cases = load_cases(db)
    finally:
        db.close()
    print(f"Cases: {len(cases)}", flush=True)

    result = run(cases, started)
    tw = result["team_win_comparison"]
    sg = result["singles_mean_comparison"]

    print("\n=== Vollmodell-Spielstärke vs. RC-only (Einzel) — gleiche Aufstellung ===")
    print(f"Fälle: {tw['n']}")
    print("\nMannschafts-Siegchance:")
    print(f"  Mittel Vollmodell-Einzel: {tw['mean_full_pct']:.2f}%")
    print(f"  Mittel RC-only Einzel:    {tw['mean_rc_only_pct']:.2f}%")
    print(f"  Mehrwert Vollmodell:      +{tw['mean_delta_pp']:.2f} PP (Median +{tw['median_delta_pp']:.2f} PP)")
    print(f"  Vollmodell höher in {tw['full_higher_pct']:.1f}% der Spiele")
    print(f"  Spannweite: {tw['min_delta_pp']:+.2f} bis {tw['max_delta_pp']:+.2f} PP")

    print("\nEinzelduelle (mittl. Spielgewinn-Wahrscheinlichkeit):")
    print(f"  Mittel Vollmodell: {sg['mean_full_pct']:.2f}%")
    print(f"  Mittel RC-only:    {sg['mean_rc_only_pct']:.2f}%")
    print(f"  Mehrwert:          +{sg['mean_delta_pp']:.2f} PP (Median +{sg['median_delta_pp']:.2f} PP)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
