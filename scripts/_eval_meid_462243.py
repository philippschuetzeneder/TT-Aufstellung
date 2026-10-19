"""Read-only eval for meid 462243 — no import."""
from __future__ import annotations

from app.analysis_service import analyze_lineup, _format_match_score_display

# Actual MEID 462243 (16.10.2026): Allerheiligen 1 (Heim) 4:8 Tragwein/Kamig 2 (Gast)
# TRAK played: A Friedinger, B Hahn, C Reithmayr, D Schmalzer
OWN_PLAYED = ["23784", "23754", "21771", "22183"]
OPP_PLAYED = ["17003", "17595", "24494", "17996"]  # Wurm, Lehner, Karlinger, Wegerer

# User's pre-match assumption (chat): TRAK Heim vs Feilmayr/Steininger quartet
OPP_ASSUMED = ["17003", "24498", "17595", "20639"]
OWN = ["23754", "23784", "21771", "22183"]
TEAM = "Tragwein/Kamig 2"
OPP_TEAM = "Allerheiligen 1"


def show(label: str, **kwargs) -> None:
    r = analyze_lineup(**kwargs)
    rec = r["recommendation"]
    info = r.get("info_summary") or {}
    print(f"\n=== {label} ===")
    print("phase", r.get("phase"), "source", r.get("opponent_set_source")[:80] if r.get("opponent_set_source") else "")
    if not rec:
        print("no rec", r.get("warnings"))
        return
    print("optimal", rec.get("players"))
    print("win%", round(rec["team_win_probability"] * 100, 2))
    print("expected", rec.get("expected_score_display"))
    print("draw/loss", round(rec.get("team_draw_probability", 0) * 100, 2), round(rec.get("team_loss_probability", 0) * 100, 2))
    print("spread_pp", rec.get("lineup_spread_pp"), "adv_strength_pp", rec.get("advantage_vs_strength_lineup_pp"))
    print("top_margin_pp", info.get("top_lineup_margin_pp"))


def main() -> None:
    # User scenario (wrong venue + wrong opp names)
    show(
        "User chat (TRAK Heim, Wurm/Feilmayr/Lehner/Steininger)",
        own_player_ids=OWN,
        opponent_team=OPP_TEAM,
        actual_opponent_ids=OPP_ASSUMED,
        own_is_home=True,
        own_team=TEAM,
        opponent_on_letters=True,
    )
    # Correct match context: TRAK away, actual opponent four
    show(
        "Correct context (TRAK Gast, actual opp four)",
        own_player_ids=OWN,
        opponent_team=OPP_TEAM,
        actual_opponent_ids=OPP_PLAYED,
        own_is_home=False,
        own_team=TEAM,
        opponent_on_letters=True,
    )
    # Fixed: lineup actually played
    r = analyze_lineup(
        own_player_ids=OWN_PLAYED,
        opponent_team=OPP_TEAM,
        actual_opponent_ids=OPP_PLAYED,
        own_is_home=False,
        own_team=TEAM,
        opponent_on_letters=True,
        fixed_order=OWN_PLAYED,
    )
    rec = r["recommendation"]
    opt = r.get("optimal_recommendation") or rec
    print("\n=== Actually played lineup (fixed) vs optimal ===")
    print("played win%", round(rec["team_win_probability"] * 100, 2), "expected", rec.get("expected_score_display"))
    print("optimal", opt.get("players"), "win%", round(opt["team_win_probability"] * 100, 2))
    print("actual result: TRAK won 8:4 (Heim:Gast 4:8)")


if __name__ == "__main__":
    main()
