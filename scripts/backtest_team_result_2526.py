"""
Backtest team match results — season 2025/2026, home team perspective, Phase C.

Compares:
  1. Full production model (leakage-safe, 3-year history window)
  2. RC-only model (matchups from RC delta only; no H2H, form, venue, spieltyp)
  3. Strength baseline (strongest-on-A RC/strength order) for each model variant

Opponent quartet and order are fixed (Phase C). Only data strictly before match day.
Script-only — does not modify app code.
"""
from __future__ import annotations

import contextlib
import json
import math
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

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
    _parse_match_date,
    _position_index,
    _trivial_strength_own_order,
)
from app.db import SessionLocal  # noqa: E402
from compare_optimal_vs_actual_411 import (  # noqa: E402
    _LEAKAGE_REF,
    _load_analysis_data_leakage_safe,
    _load_player_profiles_leakage_safe,
    leakage_safe,
    parse_team_result,
    predicted_outcome,
)

SEASON = "2025/2026"
HISTORY_YEARS = 3
MAX_RUNTIME_SEC = 1800
OUT_PATH = SCRIPT_DIR / "output" / "team_result_backtest_2526.json"

_FIXED_OPP_ORDER: tuple[str, ...] | None = None
_RC_ONLY = False
_ORIG_COMBINED = svc._combined_strength
_ORIG_MATCHUP = svc._matchup_probability
_ORIG_STATS_YEARS = svc.STATS_YEARS
_ORIG_POOL_YEARS = svc.OPPONENT_POOL_YEARS
_ORIG_LOAD = svc._load_analysis_data


def _rc_only_combined_strength(profile, side="overall"):
    del side
    rc = profile.get("rc_rating")
    if rc is None:
        return 0.0
    return ((float(rc) - RC_BASELINE) / RC_SCALE) * RC_COMPONENT_WEIGHT


def _rc_only_matchup_probability(a, b, profiles, matchups, own_is_home=True, use_spieltyp=False):
    del matchups, own_is_home, use_spieltyp
    own_strength = _rc_only_combined_strength(profiles.get(a, _empty_profile()))
    opp_strength = _rc_only_combined_strength(profiles.get(b, _empty_profile()))
    return _logistic(own_strength - opp_strength)


def _load_rc_only_profiles(db, ids, ref_date):
    names, profiles, _matchups = _load_player_profiles_leakage_safe(db, ids, ref_date)
    empty_matchups = {}
    for pid in ids:
        profiles.setdefault(str(pid), _empty_profile())
        names.setdefault(str(pid), f"Spieler {pid}")
        for field in (
            "wins", "games", "home_wins", "home_games", "away_wins", "away_games",
            "rc_trend", "rc_trend_momentum", "trend_component",
        ):
            profiles[str(pid)][field] = 0 if field != "trend_component" else 0.0
    return names, profiles, empty_matchups


def _load_analysis_data_backtest(own, opponent_team, actual, use_spieltyp=False):
    if _RC_ONLY:
        db = SessionLocal()
        try:
            ref_date = _LEAKAGE_REF or date.today()
            own = [str(x) for x in own]
            actual = None if actual is None else [str(x) for x in actual]
            if _FIXED_OPP_ORDER is None:
                raise ValueError("fixed opponent order required")
            scenarios = [(1.0, _FIXED_OPP_ORDER)]
            relevant = set(own) | set(_FIXED_OPP_ORDER)
            ids = list(relevant)
            names, profiles, matchups = _load_rc_only_profiles(db, ids, ref_date)
            return names, profiles, matchups, scenarios, "phase-c-rc-only", ref_date, set(actual or []), []
        finally:
            db.close()

    names, profiles, matchups, scenarios, source, ref_date, pool = (
        _load_analysis_data_leakage_safe(own, opponent_team, actual, use_spieltyp=use_spieltyp)
    )
    warnings: list[str] = []
    if _FIXED_OPP_ORDER is not None:
        scenarios = [(1.0, _FIXED_OPP_ORDER)]
        source = "phase-c-fixed-order"
    return names, profiles, matchups, scenarios, source, ref_date, pool, warnings


@contextlib.contextmanager
def backtest_context(ref_end: date, *, rc_only: bool = False, fixed_opp_order: tuple[str, ...]):
    global _FIXED_OPP_ORDER, _RC_ONLY
    old_fixed = _FIXED_OPP_ORDER
    old_rc = _RC_ONLY
    _FIXED_OPP_ORDER = fixed_opp_order
    _RC_ONLY = rc_only

    svc.STATS_YEARS = HISTORY_YEARS
    svc.OPPONENT_POOL_YEARS = HISTORY_YEARS
    svc._load_analysis_data = _load_analysis_data_backtest
    if rc_only:
        svc._combined_strength = _rc_only_combined_strength
        svc._matchup_probability = _rc_only_matchup_probability

    with leakage_safe(ref_end):
        try:
            yield
        finally:
            _FIXED_OPP_ORDER = old_fixed
            _RC_ONLY = old_rc
            svc.STATS_YEARS = _ORIG_STATS_YEARS
            svc.OPPONENT_POOL_YEARS = _ORIG_POOL_YEARS
            svc._load_analysis_data = _ORIG_LOAD
            svc._combined_strength = _ORIG_COMBINED
            svc._matchup_probability = _ORIG_MATCHUP


def extract_side_order(rows: list[dict]) -> list[str] | None:
    order = [None] * 4
    positions = []
    for row in rows:
        idx = _position_index(row["position"])
        if idx is None or order[idx] is not None:
            return None
        order[idx] = str(row["player_id"])
        positions.append(str(row["position"] or "").strip().upper())
    if not all(order):
        return None
    return order


def opponent_on_letters(positions: list[str]) -> bool:
    letters = sum(1 for p in positions if p in {"A", "B", "C", "D"})
    numbers = sum(1 for p in positions if p in {"1", "2", "3", "4"})
    return letters >= numbers


def load_cases(db):
    rows = db.execute(
        text(
            """
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team, m.team_result,
               mp.side, mp.external_player_id AS player_id, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND m.season = :season
          AND m.match_date IS NOT NULL
          AND m.team_result IS NOT NULL
          AND m.team_result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
        """
        ),
        {"season": SEASON},
    ).mappings().all()

    by_match = defaultdict(lambda: defaultdict(list))
    meta = {}
    for row in rows:
        mid = row["match_id"]
        meta[mid] = {
            "match_date": row["match_date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "team_result": row["team_result"],
        }
        by_match[mid][row["side"]].append(row)

    cases = []
    for mid, sides in by_match.items():
        if len(sides.get("home", [])) != 4 or len(sides.get("away", [])) != 4:
            continue
        home_order = extract_side_order(sides["home"])
        away_order = extract_side_order(sides["away"])
        if not home_order or not away_order:
            continue
        away_positions = [str(r["position"] or "").strip().upper() for r in sides["away"]]
        parsed = _parse_match_date(meta[mid]["match_date"])
        if not parsed:
            continue
        cases.append(
            {
                "match_id": mid,
                "match_date": parsed,
                "home_team": meta[mid]["home_team"],
                "away_team": meta[mid]["away_team"],
                "team_result": meta[mid]["team_result"],
                "own_ids": home_order,
                "opp_ids": tuple(away_order),
                "opponent_on_letters": opponent_on_letters(away_positions),
            }
        )
    return cases


def probs_from_rec(rec: dict) -> dict[str, float]:
    return {
        "win": float(rec.get("team_win_probability") or 0.0),
        "draw": float(rec.get("team_draw_probability") or 0.0),
        "loss": float(rec.get("team_loss_probability") or 0.0),
    }


def actual_probs(home_score: int, away_score: int) -> dict[str, float]:
    if home_score > away_score:
        return {"win": 1.0, "draw": 0.0, "loss": 0.0}
    if home_score < away_score:
        return {"win": 0.0, "draw": 0.0, "loss": 1.0}
    return {"win": 0.0, "draw": 1.0, "loss": 0.0}


def brier(pred: dict[str, float], actual: dict[str, float]) -> float:
    return sum((pred[k] - actual[k]) ** 2 for k in ("win", "draw", "loss"))


def log_loss(pred: dict[str, float], actual: dict[str, float], eps: float = 1e-15) -> float:
    if actual["win"]:
        p = pred["win"]
    elif actual["draw"]:
        p = pred["draw"]
    else:
        p = pred["loss"]
    return -math.log(max(p, eps))


_PROFILE_CACHE: dict[tuple, tuple] = {}


def analyze_case(case: dict, ref_end: date, *, rc_only: bool) -> dict | None:
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
    optimal = probs_from_rec(evaluated[0])
    strength_order = _trivial_strength_own_order(case["own_ids"], profiles)
    strength_item = next(
        (item for item in evaluated if item["own_player_ids"] == strength_order),
        evaluated[0],
    )
    baseline = probs_from_rec(strength_item)
    return {"optimal": optimal, "baseline": baseline}


def aggregate(rows: list[dict], key: str) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    outcome_hits = sum(1 for r in rows if r[f"{key}_outcome_hit"])
    brier_sum = sum(r[f"{key}_brier"] for r in rows)
    log_sum = sum(r[f"{key}_log_loss"] for r in rows)
    return {
        "n": n,
        "outcome_hit_pct": outcome_hits / n,
        "mean_brier": brier_sum / n,
        "mean_log_loss": log_sum / n,
    }


def run(cases: list[dict], started: float) -> dict:
    rows = []
    errors = []
    skipped = 0

    for i, case in enumerate(cases, 1):
        if time.monotonic() - started > MAX_RUNTIME_SEC:
            break
        ref_end = case["match_date"] - timedelta(days=1)
        parsed = parse_team_result(case["team_result"])
        if not parsed:
            skipped += 1
            continue
        home_score, away_score = parsed
        actual = actual_probs(home_score, away_score)
        actual_label = (
            "Sieg" if actual["win"] else ("7:7" if actual["draw"] else "Niederlage")
        )

        try:
            full = analyze_case(case, ref_end, rc_only=False)
            rc = analyze_case(case, ref_end, rc_only=True)
        except Exception as exc:
            errors.append({"match_id": case["match_id"], "error": str(exc)})
            continue
        if not full or not rc:
            skipped += 1
            continue

        row = {
            "match_id": case["match_id"],
            "match_date": case["match_date"].isoformat(),
            "home_team": case["home_team"],
            "away_team": case["away_team"],
            "actual_score": f"{home_score}:{away_score}",
            "actual_outcome": actual_label,
        }
        for prefix, data in (("full_model", full), ("rc_only", rc)):
            for slot in ("optimal", "baseline"):
                pred = data[slot]
                label = predicted_outcome(pred["win"], pred["draw"])
                hit = label == actual_label
                row[f"{prefix}_{slot}_outcome_hit"] = hit
                row[f"{prefix}_{slot}_brier"] = brier(pred, actual)
                row[f"{prefix}_{slot}_log_loss"] = log_loss(pred, actual)
                row[f"{prefix}_{slot}_win_prob"] = round(pred["win"], 4)
        rows.append(row)

        if i % 25 == 0:
            elapsed = time.monotonic() - started
            print(f"  ... {i}/{len(cases)} ({elapsed:.0f}s)", flush=True)

    processed = len(rows)
    result = {
        "season": SEASON,
        "perspective": "home_only",
        "phase": "C (known opponent quartet + fixed order + direction)",
        "history_years": HISTORY_YEARS,
        "leakage": "RC/stats only before match day (ref_end = match_date - 1)",
        "cases_total": len(cases),
        "cases_processed": processed,
        "cases_skipped": skipped,
        "runtime_sec": round(time.monotonic() - started, 1),
        "timed_out": processed < len(cases) and (time.monotonic() - started) >= MAX_RUNTIME_SEC,
        "full_model_optimal": aggregate(rows, "full_model_optimal"),
        "full_model_baseline": aggregate(rows, "full_model_baseline"),
        "rc_only_optimal": aggregate(rows, "rc_only_optimal"),
        "rc_only_baseline": aggregate(rows, "rc_only_baseline"),
        "delta_full_optimal_minus_baseline_pp": {},
        "delta_rc_optimal_minus_baseline_pp": {},
        "errors": errors[:50],
    }
    fm_o = result["full_model_optimal"]
    fm_b = result["full_model_baseline"]
    rc_o = result["rc_only_optimal"]
    rc_b = result["rc_only_baseline"]
    if fm_o.get("n") and fm_b.get("n"):
        result["delta_full_optimal_minus_baseline_pp"] = {
            "outcome_hit_pp": (fm_o["outcome_hit_pct"] - fm_b["outcome_hit_pct"]) * 100,
            "brier": fm_o["mean_brier"] - fm_b["mean_brier"],
            "log_loss": fm_o["mean_log_loss"] - fm_b["mean_log_loss"],
        }
    if rc_o.get("n") and rc_b.get("n"):
        result["delta_rc_optimal_minus_baseline_pp"] = {
            "outcome_hit_pp": (rc_o["outcome_hit_pct"] - rc_b["outcome_hit_pct"]) * 100,
            "brier": rc_o["mean_brier"] - rc_b["mean_brier"],
            "log_loss": rc_o["mean_log_loss"] - rc_b["mean_log_loss"],
        }
    return result


def print_summary(result: dict):
    n = result["cases_processed"]
    print(f"\n=== Mannschaftsergebnis-Backtest · {result['season']} · Heim · Phase C ===")
    print(f"Fälle gesamt: {result['cases_total']} · verarbeitet: {n} · übersprungen: {result['cases_skipped']}")
    print(f"Laufzeit: {result['runtime_sec']}s · Timeout: {'ja' if result['timed_out'] else 'nein'}")
    print(f"Historie: {result['history_years']} Jahre · {result['leakage']}")
    print()
    headers = [
        ("Vollmodell optimal", "full_model_optimal"),
        ("Vollmodell Stärke-Baseline", "full_model_baseline"),
        ("RC-only optimal", "rc_only_optimal"),
        ("RC-only Stärke-Baseline", "rc_only_baseline"),
    ]
    print("| Variante | Outcome-Treffer | Brier (lower) | Log-Loss (lower) |")
    print("|:---|:---:|:---:|:---:|")
    for label, key in headers:
        agg = result.get(key) or {}
        if not agg.get("n"):
            continue
        print(
            f"| {label} | {agg['outcome_hit_pct']*100:.1f}% | "
            f"{agg['mean_brier']:.4f} | {agg['mean_log_loss']:.4f} |"
        )
    df = result.get("delta_full_optimal_minus_baseline_pp") or {}
    dr = result.get("delta_rc_optimal_minus_baseline_pp") or {}
    if df:
        print(
            f"\nVollmodell optimal vs Baseline: "
            f"{df.get('outcome_hit_pp', 0):+.1f} PP Outcome, "
            f"Brier {df.get('brier', 0):+.4f}, Log-Loss {df.get('log_loss', 0):+.4f}"
        )
    if dr:
        print(
            f"RC-only optimal vs Baseline: "
            f"{dr.get('outcome_hit_pp', 0):+.1f} PP Outcome, "
            f"Brier {dr.get('brier', 0):+.4f}, Log-Loss {dr.get('log_loss', 0):+.4f}"
        )


def main() -> int:
    started = time.monotonic()
    print(f"Loading Phase-C home cases for {SEASON}...", flush=True)
    db = SessionLocal()
    try:
        cases = load_cases(db)
    finally:
        db.close()
    print(f"Cases: {len(cases)}")
    if not cases:
        return 1

    result = run(cases, started)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print_summary(result)
    print(f"\nJSON: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
