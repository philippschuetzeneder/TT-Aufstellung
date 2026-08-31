"""
Win-probability comparison: optimal lineup vs RC-strength baseline.

Reuses leakage-safe Phase-C backtest infrastructure.
Use --rc-only for singles probabilities based on RC strength only.
"""
from __future__ import annotations

import argparse
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
from app.db import SessionLocal  # noqa: E402
from backtest_team_result_2526 import (  # noqa: E402
    MAX_RUNTIME_SEC,
    _PROFILE_CACHE,
    _load_analysis_data_backtest,
    backtest_context,
    load_cases,
    parse_team_result,
)
from app.analysis_service import _trivial_strength_own_order  # noqa: E402

OUT_PATH_WIN = SCRIPT_DIR / "output" / "win_prob_vs_baseline_2526.json"
OUT_PATH_WIN_RC = SCRIPT_DIR / "output" / "win_prob_vs_baseline_rc_singles_2526.json"


def mean_singles_win_prob(
    own_order: list[str],
    opp_order: tuple[str, ...],
    profiles: dict,
    *,
    opponent_on_letters: bool,
    rc_only: bool,
) -> float:
    """Average RC-based singles win probability over the 12 singles games."""
    own_on_letters = None if opponent_on_letters is None else not opponent_on_letters
    schedule = svc._schedule_for_orientation(
        True if own_on_letters is None else own_on_letters,
    )
    relevant = set(own_order) | set(opp_order)
    matchup_p = svc._build_matchup_table(relevant, profiles, {}, True, use_spieltyp=False)
    singles = [
        matchup_p.get((own_order[own_idx], opp_order[opp_idx]), 0.5)
        for own_idx, opp_idx in schedule
    ]
    del rc_only  # caller ensures backtest_context already patched when rc_only
    return statistics.mean(singles)


def evaluate_case(case: dict, ref_end, *, rc_only: bool) -> dict | None:
    fixed_opp = tuple(case["opp_ids"])
    cache_key = (ref_end, frozenset(case["own_ids"]), frozenset(fixed_opp), rc_only)
    if cache_key in _PROFILE_CACHE:
        names, profiles, matchups, scenarios = _PROFILE_CACHE[cache_key]
    else:
        with backtest_context(ref_end, rc_only=rc_only, fixed_opp_order=fixed_opp):
            names, profiles, matchups, scenarios, _, _, _, _ = _load_analysis_data_backtest(
                case["own_ids"], case["away_team"], list(case["opp_ids"]),
            )
        if not scenarios:
            return None
        _PROFILE_CACHE[cache_key] = (names, profiles, matchups, scenarios)

    import time as _time

    with backtest_context(ref_end, rc_only=rc_only, fixed_opp_order=fixed_opp):
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
    strength_order = _trivial_strength_own_order(case["own_ids"], profiles)
    strength_item = next(
        (item for item in evaluated if item["own_player_ids"] == strength_order),
        evaluated[0],
    )

    with backtest_context(ref_end, rc_only=rc_only, fixed_opp_order=fixed_opp):
        opt_singles = mean_singles_win_prob(
            optimal_order, fixed_opp, profiles,
            opponent_on_letters=case["opponent_on_letters"], rc_only=rc_only,
        )
        base_singles = mean_singles_win_prob(
            strength_order, fixed_opp, profiles,
            opponent_on_letters=case["opponent_on_letters"], rc_only=rc_only,
        )

    return {
        "optimal": {
            "win": float(evaluated[0]["team_win_probability"]),
            "singles_mean": opt_singles,
        },
        "baseline": {
            "win": float(strength_item["team_win_probability"]),
            "singles_mean": base_singles,
        },
    }


def compare_win_probs(
    rows: list[dict],
    delta_key: str,
    optimal_key: str,
    baseline_key: str,
) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}

    deltas = [r[delta_key] for r in rows]
    optimal = [r[optimal_key] for r in rows]
    baseline = [r[baseline_key] for r in rows]
    sorted_deltas = sorted(deltas)

    return {
        "n": n,
        "mean_optimal_pct": round(statistics.mean(optimal) * 100, 3),
        "mean_baseline_pct": round(statistics.mean(baseline) * 100, 3),
        "mean_delta_pp": round(statistics.mean(deltas) * 100, 3),
        "median_delta_pp": round(statistics.median(deltas) * 100, 3),
        "total_delta_pp_sum": round(sum(deltas) * 100, 1),
        "optimal_higher_count": sum(1 for d in deltas if d > 1e-9),
        "optimal_higher_pct": round(sum(1 for d in deltas if d > 1e-9) / n * 100, 2),
        "equal_count": sum(1 for d in deltas if abs(d) <= 1e-9),
        "equal_pct": round(sum(1 for d in deltas if abs(d) <= 1e-9) / n * 100, 2),
        "optimal_lower_count": sum(1 for d in deltas if d < -1e-9),
        "optimal_lower_pct": round(sum(1 for d in deltas if d < -1e-9) / n * 100, 2),
        "p10_delta_pp": round(sorted_deltas[int(0.10 * (n - 1))] * 100, 3),
        "p90_delta_pp": round(sorted_deltas[int(0.90 * (n - 1))] * 100, 3),
        "max_delta_pp": round(max(deltas) * 100, 3),
        "min_delta_pp": round(min(deltas) * 100, 3),
    }


def run(cases: list[dict], started: float, *, rc_only: bool) -> dict:
    rows = []
    errors = []

    for i, case in enumerate(cases, 1):
        if time.monotonic() - started > MAX_RUNTIME_SEC:
            break
        ref_end = case["match_date"] - timedelta(days=1)
        parsed = parse_team_result(case["team_result"])
        if not parsed:
            continue
        try:
            result = evaluate_case(case, ref_end, rc_only=rc_only)
        except Exception as exc:
            errors.append({"match_id": case["match_id"], "error": str(exc)})
            continue
        if not result:
            continue

        opt_w = float(result["optimal"]["win"])
        base_w = float(result["baseline"]["win"])
        opt_s = float(result["optimal"]["singles_mean"])
        base_s = float(result["baseline"]["singles_mean"])
        rows.append(
            {
                "match_id": case["match_id"],
                "match_date": case["match_date"].isoformat(),
                "home_team": case["home_team"],
                "away_team": case["away_team"],
                "optimal_win_prob": opt_w,
                "baseline_win_prob": base_w,
                "delta_win_prob": opt_w - base_w,
                "optimal_singles_mean": opt_s,
                "baseline_singles_mean": base_s,
                "delta_singles_mean": opt_s - base_s,
            }
        )
        if i % 50 == 0:
            print(f"  ... {i}/{len(cases)} ({time.monotonic() - started:.0f}s)", flush=True)

    team_cmp = compare_win_probs(rows, "delta_win_prob", "optimal_win_prob", "baseline_win_prob")
    singles_cmp = compare_win_probs(
        rows, "delta_singles_mean", "optimal_singles_mean", "baseline_singles_mean",
    )
    model_label = (
        "RC-only Einzelduelle (logistic aus RC-Differenz, kein H2H/Form/Heim)"
        if rc_only else "Vollmodell Einzelduelle"
    )
    return {
        "description": (
            "Siegewahrscheinlichkeit optimal vs. RC-Stärke-Baseline; "
            f"Einzel-Wahrscheinlichkeiten: {model_label}"
        ),
        "singles_probability_model": model_label,
        "rc_only_singles": rc_only,
        "season": "2025/2026",
        "perspective": "home_only",
        "phase": "C",
        "cases_total": len(cases),
        "cases_processed": len(rows),
        "runtime_sec": round(time.monotonic() - started, 1),
        "team_win_probability_comparison": team_cmp,
        "singles_mean_win_probability_comparison": singles_cmp,
        "errors": errors[:20],
    }


def print_comparison(title: str, cmp_: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"Fälle: {cmp_['n']}")
    print(f"Mittel optimal:  {cmp_['mean_optimal_pct']:.2f}%")
    print(f"Mittel Baseline: {cmp_['mean_baseline_pct']:.2f}%")
    print(f"Mittlerer Vorteil optimal: +{cmp_['mean_delta_pp']:.2f} PP")
    print(f"Median Vorteil optimal:      +{cmp_['median_delta_pp']:.2f} PP")
    print(
        f"In {cmp_['optimal_higher_pct']:.1f}% der Spiele optimal > Baseline "
        f"({cmp_['optimal_higher_count']} von {cmp_['n']})"
    )
    print(f"Gleich: {cmp_['equal_pct']:.1f}% · Optimal niedriger: {cmp_['optimal_lower_pct']:.1f}%")
    print(f"Spannweite Delta: {cmp_['min_delta_pp']:+.2f} bis {cmp_['max_delta_pp']:+.2f} PP")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rc-only",
        action="store_true",
        help="Einzelduell-Wahrscheinlichkeiten nur aus RC-Stärke (kein H2H/Form/Heim)",
    )
    args = parser.parse_args()

    started = time.monotonic()
    print("Loading cases...", flush=True)
    db = SessionLocal()
    try:
        cases = load_cases(db)
    finally:
        db.close()
    print(f"Cases: {len(cases)}", flush=True)

    result = run(cases, started, rc_only=args.rc_only)
    print_comparison("Mannschafts-Siegchance", result["team_win_probability_comparison"])
    print_comparison("Einzelduelle (mittl. Spielgewinn-Wahrscheinlichkeit)", result["singles_mean_win_probability_comparison"])

    out_path = OUT_PATH_WIN_RC if args.rc_only else OUT_PATH_WIN
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
