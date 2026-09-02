"""Compare Tragwein/Kamig 3 vs Lasberg 1: current vs pre-fix model parameters."""
from __future__ import annotations

import math
import sys

sys.path.insert(0, "backend")

import app.analysis_service as mod
from app.analysis_service import analyze_lineup

OWN = ["21773", "23782", "24890", "24889"]
OPP = ["13308", "14374", "18518", "16703"]
DOUBLES = [["23782", "24890"], ["21773", "24889"]]
NAMES = {
    "21773": "Schützeneder",
    "23782": "Prantner",
    "24890": "Dreiling",
    "24889": "Nötstaller",
}


def summarize(result: dict, label: str) -> None:
    print(f"\n=== {label} ===")
    rec = result["recommendation"]
    print(f"model: {result.get('model', {}).get('version')}")
    print(
        f"Sieg: {rec['team_win_probability']*100:.1f}% | "
        f"Remis: {rec.get('team_draw_probability',0)*100:.1f}% | "
        f"{rec.get('expected_score_display')}"
    )
    print(
        "Aufstellung:",
        " ".join(f"{p}={NAMES.get(pid, pid)}" for p, pid in zip("ABCD", rec["own_player_ids"])),
    )
    print(
        f"Vorteil vs Stärke-Aufstellung: {rec.get('advantage_vs_strength_lineup_pp', 0):.2f} PP | "
        f"Spread Top-Bottom: {rec.get('lineup_spread_pp', 0):.2f} PP"
    )
    recs = result.get("recommendations") or []
    top = recs[0]["team_win_probability"] if recs else rec["team_win_probability"]
    for i, item in enumerate(recs[1:5], 2):
        print(
            f"  Alt #{i}: -{(top - item['team_win_probability'])*100:.2f} PP - "
            + "/".join(NAMES.get(p, p) for p in item["own_player_ids"])
        )
    strength = rec.get("strength_lineup_player_ids") or []
    if strength:
        print(
            "Staerke-Aufstellung (A-D):",
            " ".join(f"{p}={NAMES.get(pid, pid)}" for p, pid in zip("ABCD", strength)),
        )


def run(**patches) -> dict:
    saved = {}
    for key, value in patches.items():
        saved[key] = getattr(mod, key)
        setattr(mod, key, value)
    mod.clear_analysis_runtime_caches()
    try:
        return analyze_lineup(
            OWN,
            "Lasberg 1",
            actual_opponent_ids=OPP,
            own_is_home=True,
            opponent_on_letters=False,
            own_team="Tragwein/Kamig 3",
            own_double_pairs=DOUBLES,
            stronger_double_pair=1,
        )
    finally:
        for key, value in saved.items():
            setattr(mod, key, value)
        mod.clear_analysis_runtime_caches()


def main() -> None:
    current = run()
    summarize(current, "AKTUELL")

    def day_recency(match_date, ref_date, **kwargs):
        parsed = mod._parse_match_date(match_date)
        if not parsed or not ref_date:
            return 0.15
        age_days = max(0, (ref_date - parsed).days)
        return math.pow(0.5, age_days / 60.0)

    def old_matchup(a, b, profiles, matchups, own_is_home=True, use_spieltyp=False):
        own_profile = profiles.get(a, mod._empty_profile())
        opp_profile = profiles.get(b, mod._empty_profile())
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
        direct = (wins + 1.5) / (games + 3.0)
        weight = min(0.85, 0.35 + games / 6.0)
        return mod._clamp_probability((1.0 - weight) * base + weight * direct)

    def old_weights(player_ids, rc_by_player):
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

    def old_adaptive_strength_weight(db, player_ids, ref_date=None):
        exact, exact_mass, trio, trio_mass = mod._lineup_cohesion(db, player_ids, ref_date)
        if exact >= mod.COHESIVE_QUARTET_MIN_MATCHES and exact_mass >= mod.COHESIVE_QUARTET_MIN_RECENCY_MASS:
            return 0.15, exact, trio
        if trio >= mod.COHESIVE_TRIO_MIN_MATCHES and trio_mass >= mod.COHESIVE_TRIO_MIN_RECENCY_MASS:
            return 0.30, exact, trio
        if exact >= mod.MEDIUM_GROUP_MIN_MATCHES and exact_mass >= 2.0:
            return 0.40, exact, trio
        if exact == 0 and trio == 0:
            return 0.80, exact, trio
        if exact <= 1 and trio <= 1:
            return 0.80, exact, trio
        return 0.70, exact, trio

    orig_load = mod._load_analysis_data

    def load_sharpened(*args, **kwargs):
        out = list(orig_load(*args, **kwargs))
        if len(out) > 3 and out[3]:
            out[3] = mod._sharpen_scenarios(list(out[3]), alpha=2.5)
        return tuple(out)

    mod._lineup_recency_weight = day_recency
    mod._matchup_probability = old_matchup
    mod._adaptive_strength_position_weights = old_weights
    mod._adaptive_strength_weight = old_adaptive_strength_weight
    mod._global_strength_weight_scale = lambda db, ref_date=None: 1.0
    mod._load_analysis_data = load_sharpened
    mod.clear_analysis_runtime_caches()

    old = analyze_lineup(
        OWN,
        "Lasberg 1",
        actual_opponent_ids=OPP,
        own_is_home=True,
        opponent_on_letters=False,
        own_team="Tragwein/Kamig 3",
        own_double_pairs=DOUBLES,
        stronger_double_pair=1,
    )
    summarize(old, "SIMULIERT VOR-FIX")

    mod._load_analysis_data = orig_load
    mod.clear_analysis_runtime_caches()

    d = (old["recommendation"]["team_win_probability"] - current["recommendation"]["team_win_probability"]) * 100
    spread_old = old["recommendation"].get("lineup_spread_pp", 0)
    spread_new = current["recommendation"].get("lineup_spread_pp", 0)
    print(f"\nDelta Siegchance (simuliert alt - aktuell): {d:+.2f} PP")
    print(f"Spread alt: {spread_old:.2f} PP | Spread neu: {spread_new:.2f} PP")


if __name__ == "__main__":
    main()
