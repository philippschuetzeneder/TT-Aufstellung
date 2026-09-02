"""
Validate canvas analysis fixes (F1,F3,F5,F6,F7,F10,F11,F12 + v36 sharpen/H2H blend).

Compares CURRENT production model vs simulated PRE-CANVAS baseline on:
  1. Unit/regression tests (pytest)
  2. Opponent lineup backtest (8381 cases, leakage-safe)
  3. Low-history opponent quartets (0-2 exact-4 games)
  4. Team result backtest sample (Phase C home, outcome/Brier/log-loss)
  5. Own-lineup optimization invariants (optimal >= RC-strength baseline)
  6. Manual fixture cases (Lasberg, Alberndorf spread)

Script-only — does not modify app code.
"""
from __future__ import annotations

import contextlib
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import (
    SCENARIO_SHARPENING_ALPHA,
    _adaptive_strength_position_weights,
    _build_known_four_opponent_scenarios,
    _empty_profile,
    _global_strength_weight_scale,
    _lineup_recency_weight,
    _lookup_matchup_probability,
    _matchup_probability,
    _parse_match_date,
    _sharpen_scenarios,
    _trivial_strength_own_order,
    analyze_lineup,
    clear_analysis_runtime_caches,
)
from app.db import SessionLocal
from backtest_opponent_lineups import (
    _lineup_cohesion_lb,
    _load_latest_rc_map_lb,
    extract_side_order,
    leakage_safe,
)
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual
from backtest_team_result_2526 import (
    actual_probs,
    analyze_case,
    backtest_context,
    brier,
    load_cases,
    log_loss,
    parse_team_result,
    predicted_outcome,
    probs_from_rec,
)

OUT_PATH = SCRIPT_DIR / "output" / "validate_canvas_fixes.json"
BASELINE_OPP_PATH = SCRIPT_DIR / "output" / "opponent_lineup_backtest_2526.json"
TEAM_BASELINE_PATH = SCRIPT_DIR / "output" / "team_result_backtest_2526.json"
TOP_K = [1, 3, 5, 10]


def _day_recency(match_date, ref_date, **kwargs):
    parsed = mod._parse_match_date(match_date)
    if not parsed or not ref_date:
        return 0.15
    age_days = max(0, (ref_date - parsed).days)
    return math.pow(0.5, age_days / 60.0)


def _legacy_strength_weights(player_ids, rc_by_player):
    rc_values = sorted(mod._resolve_rc_rating(pid, rc_by_player, player_ids) for pid in player_ids)
    spread = rc_values[-1] - rc_values[0]
    if spread < mod.STRENGTH_RC_SPREAD_FLAT_THRESHOLD:
        return mod.STRENGTH_POSITION_WEIGHTS_TIGHT
    gaps = [rc_values[i + 1] - rc_values[i] for i in range(3)]
    total_gap = sum(gaps) or 1.0
    normalized = [gap / total_gap for gap in gaps]
    w_min = mod.STRENGTH_POSITION_WEIGHT_MIN
    w_span = mod.STRENGTH_POSITION_WEIGHT_MAX - mod.STRENGTH_POSITION_WEIGHT_MIN
    w_a = w_min + w_span * (0.25 + 0.75 * normalized[2])
    w_d = w_min + w_span * (0.25 + 0.75 * normalized[0])
    w_b = w_min + w_span * (0.25 + 0.75 * (normalized[1] + normalized[2]) / 2.0)
    w_c = w_min + w_span * (0.25 + 0.75 * (normalized[0] + normalized[1]) / 2.0)
    return (w_a, w_b, w_c, w_d)


def _legacy_matchup(a, b, profiles, matchups, own_is_home=True, use_spieltyp=False):
    own_profile = profiles.get(a, _empty_profile())
    opp_profile = profiles.get(b, _empty_profile())
    if own_is_home:
        own_strength = mod._combined_strength(own_profile, "home")
        opp_strength = mod._combined_strength(opp_profile, "away")
    else:
        own_strength = mod._combined_strength(own_profile, "away")
        opp_strength = mod._combined_strength(opp_profile, "home")
    base = mod._logistic(own_strength - opp_strength)
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + mod.H2H_LEGACY_PRIOR_WINS) / (games + mod.H2H_LEGACY_PRIOR_GAMES)
    weight = min(
        mod.H2H_LEGACY_WEIGHT_CAP,
        mod.H2H_LEGACY_WEIGHT_BASE + games / mod.H2H_LEGACY_WEIGHT_GAMES_DIV,
    )
    return mod._clamp_probability((1.0 - weight) * base + weight * direct)


def _legacy_effective_rc(rating, deviation=None):
    if rating is None:
        return None
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _legacy_lookup(matchup_p, own_id, opp_id, profiles, matchups, own_is_home=True, use_spieltyp=False):
    return matchup_p.get((own_id, opp_id), 0.5)


@contextlib.contextmanager
def legacy_pre_canvas():
    saved = {
        "_lineup_recency_weight": mod._lineup_recency_weight,
        "_adaptive_strength_position_weights": mod._adaptive_strength_position_weights,
        "_matchup_probability": mod._matchup_probability,
        "_global_strength_weight_scale": mod._global_strength_weight_scale,
        "_effective_rc": mod._effective_rc,
        "_lookup_matchup_probability": mod._lookup_matchup_probability,
        "H2H_SHRINKAGE_BLEND": mod.H2H_SHRINKAGE_BLEND,
        "SCENARIO_SHARPENING_ALPHA": mod.SCENARIO_SHARPENING_ALPHA,
    }
    mod._lineup_recency_weight = _day_recency
    mod._adaptive_strength_position_weights = _legacy_strength_weights
    mod._matchup_probability = _legacy_matchup
    mod._global_strength_weight_scale = lambda db, ref_date=None: 1.0
    mod._effective_rc = _legacy_effective_rc
    mod._lookup_matchup_probability = _legacy_lookup
    mod.H2H_SHRINKAGE_BLEND = 1.0
    mod.SCENARIO_SHARPENING_ALPHA = 2.5
    clear_analysis_runtime_caches()
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(mod, key, value)
        clear_analysis_runtime_caches()


def predict_scenarios(db, opponent_team, opponent_ids, ref_end: date):
    with leakage_safe(ref_end):
        scenarios, _, _ = _build_known_four_opponent_scenarios(
            db, opponent_team, opponent_ids, ref_end,
        )
    if scenarios:
        return list(scenarios)
    from itertools import permutations

    uniform = 1.0 / 24.0
    return [(uniform, tuple(o)) for o in permutations(opponent_ids)]


def opponent_backtest(cases, label: str, *, legacy: bool) -> dict:
    totals = {k: 0 for k in TOP_K}
    cohesion_detail = {
        "0": {k: 0 for k in TOP_K},
        "1-3": {k: 0 for k in TOP_K},
        "4+": {k: 0 for k in TOP_K},
    }
    cohesion_n = {"0": 0, "1-3": 0, "4+": 0}
    scenario_cache: dict = {}
    cohesion_cache: dict = {}
    n = 0

    ctx = legacy_pre_canvas() if legacy else contextlib.nullcontext()
    with ctx:
        db = SessionLocal()
        try:
            for case in cases:
                ref_end = case["match_date"] - timedelta(days=1)
                actual = tuple(case["actual_order"])
                key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
                if key not in scenario_cache:
                    scenario_cache[key] = predict_scenarios(
                        db, case["opponent_team"], case["opponent_ids"], ref_end,
                    )
                model_rank = rank_of_actual(scenario_cache[key], actual)

                if key not in cohesion_cache:
                    with leakage_safe(ref_end):
                        exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
                    cohesion_cache[key] = exact
                exact = cohesion_cache[key]
                bucket = "0" if exact == 0 else ("1-3" if exact <= 3 else "4+")

                n += 1
                cohesion_n[bucket] += 1
                for k in TOP_K:
                    if model_rank <= k:
                        totals[k] += 1
                        cohesion_detail[bucket][k] += 1
        finally:
            db.close()

    return {
        "label": label,
        "legacy": legacy,
        "cases": n,
        "top_k_pct": {str(k): totals[k] / n if n else 0.0 for k in TOP_K},
        "cohesion_buckets": {
            bucket: {
                "n": cohesion_n[bucket],
                "top_k_pct": {
                    str(k): cohesion_detail[bucket][k] / cohesion_n[bucket]
                    if cohesion_n[bucket]
                    else 0.0
                    for k in TOP_K
                },
            }
            for bucket in cohesion_detail
        },
    }


def team_backtest_sample(cases, *, legacy: bool, max_cases: int = 600) -> dict:
    rows = []
    ctx = legacy_pre_canvas() if legacy else contextlib.nullcontext()
    with ctx:
        for case in cases[:max_cases]:
            ref_end = case["match_date"] - timedelta(days=1)
            parsed = parse_team_result(case["team_result"])
            if not parsed:
                continue
            home_score, away_score = parsed
            actual = actual_probs(home_score, away_score)
            result = analyze_case(case, ref_end, rc_only=False)
            if not result:
                continue
            optimal = result["optimal"]
            baseline = result["baseline"]
            rows.append(
                {
                    "optimal_outcome_hit": predicted_outcome(optimal) == predicted_outcome(actual),
                    "baseline_outcome_hit": predicted_outcome(baseline) == predicted_outcome(actual),
                    "optimal_brier": brier(optimal, actual),
                    "baseline_brier": brier(baseline, actual),
                    "optimal_log_loss": log_loss(optimal, actual),
                    "baseline_log_loss": log_loss(baseline, actual),
                    "optimal_win": optimal["win"],
                    "baseline_win": baseline["win"],
                }
            )

    n = len(rows)
    if not n:
        return {"label": "legacy" if legacy else "current", "n": 0}

    def agg(key):
        return sum(r[key] for r in rows) / n

    optimal_ge_baseline = sum(
        1 for r in rows if r["optimal_win"] >= r["baseline_win"] - 1e-9
    )
    return {
        "label": "legacy_pre_canvas" if legacy else "current_v36",
        "legacy": legacy,
        "n": n,
        "optimal_outcome_hit_pct": agg("optimal_outcome_hit"),
        "baseline_outcome_hit_pct": agg("baseline_outcome_hit"),
        "optimal_mean_brier": agg("optimal_brier"),
        "baseline_mean_brier": agg("baseline_brier"),
        "optimal_mean_log_loss": agg("optimal_log_loss"),
        "baseline_mean_log_loss": agg("baseline_log_loss"),
        "optimal_ge_baseline_win_pct": optimal_ge_baseline / n,
    }


def own_lineup_invariants(cases, *, legacy: bool, max_cases: int = 400) -> dict:
    violations = 0
    checked = 0
    spreads = []
    ctx = legacy_pre_canvas() if legacy else contextlib.nullcontext()
    with ctx:
        for case in cases[:max_cases]:
            ref_end = case["match_date"] - timedelta(days=1)
            try:
                with backtest_context(ref_end, rc_only=False, fixed_opp_order=tuple(case["opp_ids"])):
                    result = analyze_lineup(
                        case["own_ids"],
                        case["away_team"],
                        actual_opponent_ids=list(case["opp_ids"]),
                        own_is_home=True,
                        opponent_on_letters=case["opponent_on_letters"],
                    )
            except Exception:
                continue
            if not result.get("ok") or not result.get("recommendation"):
                continue
            rec = result["recommendation"]
            adv = rec.get("advantage_vs_strength_lineup_pp")
            spread = rec.get("lineup_spread_pp")
            if adv is not None and adv < -0.01:
                violations += 1
            if spread is not None:
                spreads.append(spread)
            checked += 1
    return {
        "label": "legacy_pre_canvas" if legacy else "current_v36",
        "checked": checked,
        "optimal_lt_strength_violations": violations,
        "mean_lineup_spread_pp": sum(spreads) / len(spreads) if spreads else None,
        "median_lineup_spread_pp": sorted(spreads)[len(spreads) // 2] if spreads else None,
    }


def manual_fixtures() -> list[dict]:
    fixtures = [
        {
            "name": "Lasberg 1 (Tragwein/Kamig 3, Heim, 4/4 Gegner)",
            "own": ["21773", "23782", "24890", "24889"],
            "opp": ["13308", "14374", "18518", "16703"],
            "team": "Tragwein/Kamig 3",
            "opponent": "Lasberg 1",
            "pairs": [["23782", "24890"], ["21773", "24889"]],
            "own_is_home": True,
            "opponent_on_letters": False,
        },
        {
            "name": "Alberndorf 2 (Phase C, Spread-Check)",
            "own": ["21773", "23754", "24890", "24889"],
            "opp": ["22472", "22223", "21339", "21970"],
            "team": "Tragwein/Kamig 3",
            "opponent": "Alberndorf 2",
            "pairs": [["21773", "24889"], ["24890", "23754"]],
            "own_is_home": True,
            "opponent_on_letters": True,
        },
    ]
    results = []
    for fix in fixtures:
        for legacy in (False, True):
            ctx = legacy_pre_canvas() if legacy else contextlib.nullcontext()
            with ctx:
                result = analyze_lineup(
                    fix["own"],
                    fix["opponent"],
                    actual_opponent_ids=fix["opp"],
                    own_is_home=fix["own_is_home"],
                    opponent_on_letters=fix["opponent_on_letters"],
                    own_team=fix["team"],
                    own_double_pairs=fix["pairs"],
                    stronger_double_pair=1,
                )
            rec = result.get("recommendation") or {}
            recs = result.get("recommendations") or []
            results.append(
                {
                    "fixture": fix["name"],
                    "legacy": legacy,
                    "model_version": result.get("model", {}).get("version"),
                    "win_pct": round((rec.get("team_win_probability") or 0) * 100, 2),
                    "lineup_spread_pp": rec.get("lineup_spread_pp"),
                    "advantage_vs_strength_pp": rec.get("advantage_vs_strength_lineup_pp"),
                    "alt2_loss_pp": recs[1].get("loss_pp_vs_optimal") if len(recs) > 1 else None,
                    "alt5_loss_pp": recs[4].get("loss_pp_vs_optimal") if len(recs) > 4 else None,
                }
            )
    return results


def run_pytest() -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "backend/test_analysis_model_fixes.py",
            "backend/test_alt_lineup_loss.py",
            "backend/test_global_strength_prior.py",
            "backend/test_rc_and_position_priors.py",
            "-q",
        ],
        cwd=str(SCRIPT_DIR.parent),
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.strip()[-500:],
        "stderr_tail": proc.stderr.strip()[-500:],
    }


def delta_pp(current: float, baseline: float) -> float:
    return (current - baseline) * 100


def main() -> int:
    started = time.monotonic()
    report: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    print("=== 1/6 Pytest regression ===")
    report["pytest"] = run_pytest()
    print(report["pytest"]["stdout_tail"])

    print("\n=== 2/6 Opponent lineup backtest (full season) ===")
    db = SessionLocal()
    try:
        opp_cases = load_cases_2526(db)
        team_cases = load_cases(db)
    finally:
        db.close()
    print(f"Loaded {len(opp_cases)} opponent cases, {len(team_cases)} team cases")

    current_opp = opponent_backtest(opp_cases, "current_v36", legacy=False)
    legacy_opp = opponent_backtest(opp_cases, "legacy_pre_canvas", legacy=True)
    report["opponent_lineup"] = {
        "current": current_opp,
        "legacy": legacy_opp,
        "delta_current_minus_legacy_pp": {
            str(k): delta_pp(
                current_opp["top_k_pct"][str(k)],
                legacy_opp["top_k_pct"][str(k)],
            )
            for k in TOP_K
        },
    }
    if BASELINE_OPP_PATH.exists():
        stored = json.loads(BASELINE_OPP_PATH.read_text(encoding="utf-8"))
        report["opponent_lineup"]["stored_baseline_top1_pct"] = stored["model"]["top_k_pct"]["1"]
        report["opponent_lineup"]["delta_current_minus_stored_pp"] = delta_pp(
            current_opp["top_k_pct"]["1"],
            stored["model"]["top_k_pct"]["1"],
        )

    for label, data in [("CURRENT", current_opp), ("LEGACY", legacy_opp)]:
        print(f"\n{label} Top-k:", {k: f"{data['top_k_pct'][str(k)]*100:.1f}%" for k in TOP_K})

    low_history_cases = []
    db = SessionLocal()
    try:
        cohesion_cache = {}
        for case in opp_cases:
            ref_end = case["match_date"] - timedelta(days=1)
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
            if key not in cohesion_cache:
                with leakage_safe(ref_end):
                    exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
                cohesion_cache[key] = exact
            if cohesion_cache[key] <= 2:
                low_history_cases.append(case)
    finally:
        db.close()

    print(f"\n=== 3/6 Low-history quartets (0-2 exact-4 games): {len(low_history_cases)} cases ===")
    current_low = opponent_backtest(low_history_cases, "current_low_history", legacy=False)
    legacy_low = opponent_backtest(low_history_cases, "legacy_low_history", legacy=True)
    report["low_history_opponent"] = {
        "cases": len(low_history_cases),
        "current": current_low,
        "legacy": legacy_low,
        "delta_current_minus_legacy_pp": {
            str(k): delta_pp(
                current_low["top_k_pct"][str(k)],
                legacy_low["top_k_pct"][str(k)],
            )
            for k in TOP_K
        },
    }
    print(
        "Top-1 current vs legacy:",
        f"{current_low['top_k_pct']['1']*100:.1f}% vs {legacy_low['top_k_pct']['1']*100:.1f}%",
    )

    print("\n=== 4/6 Team result sample (600 home Phase-C cases) ===")
    current_team = team_backtest_sample(team_cases, legacy=False, max_cases=600)
    legacy_team = team_backtest_sample(team_cases, legacy=True, max_cases=600)
    report["team_result_sample"] = {
        "max_cases": 600,
        "current": current_team,
        "legacy": legacy_team,
    }
    if TEAM_BASELINE_PATH.exists():
        stored_team = json.loads(TEAM_BASELINE_PATH.read_text(encoding="utf-8"))
        report["team_result_sample"]["stored_full_run"] = {
            "optimal_outcome_hit_pct": stored_team["full_model_optimal"]["outcome_hit_pct"],
            "optimal_mean_brier": stored_team["full_model_optimal"]["mean_brier"],
        }
    print(
        "Outcome hit optimal:",
        f"{current_team['optimal_outcome_hit_pct']*100:.1f}% (current) vs "
        f"{legacy_team['optimal_outcome_hit_pct']*100:.1f}% (legacy)",
    )

    print("\n=== 5/6 Own-lineup invariants (400 cases) ===")
    report["own_lineup_invariants"] = {
        "current": own_lineup_invariants(team_cases, legacy=False),
        "legacy": own_lineup_invariants(team_cases, legacy=True),
    }
    for key in ("current", "legacy"):
        inv = report["own_lineup_invariants"][key]
        print(
            inv["label"],
            f"checked={inv['checked']}",
            f"violations={inv['optimal_lt_strength_violations']}",
            f"mean_spread={inv['mean_lineup_spread_pp']}",
        )

    print("\n=== 6/6 Manual fixtures ===")
    report["manual_fixtures"] = manual_fixtures()
    for row in report["manual_fixtures"]:
        tag = "legacy" if row["legacy"] else "current"
        print(
            f"{row['fixture']} [{tag}]: win={row['win_pct']}% spread={row['lineup_spread_pp']} "
            f"adv={row['advantage_vs_strength_pp']} alt2={row['alt2_loss_pp']}"
        )

    report["runtime_sec"] = round(time.monotonic() - started, 1)
    report["model_version"] = mod.MODEL_VERSION
    report["scenario_sharpening_alpha"] = SCENARIO_SHARPENING_ALPHA

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {OUT_PATH} ({report['runtime_sec']}s)")
    return 0 if report["pytest"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
