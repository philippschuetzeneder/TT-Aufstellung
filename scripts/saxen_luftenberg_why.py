"""Diagnose: Saxen 2 vs Luftenberg 1 prediction vs 8:4 result."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.analysis_service import (
    _combined_strength,
    _load_player_profiles,
    _parse_match_date,
    _reference_date,
    analyze_lineup,
)
from app.db import SessionLocal
from compare_optimal_vs_actual_411 import leakage_safe, load_players

HOME = "Saxen 2"
AWAY = "Luftenberg 1"
SEASON = "2026/2027"


def main() -> None:
    with SessionLocal() as db:
        m = db.execute(
            text(
                """
            SELECT id, match_date, team_result
            FROM xttv_matches
            WHERE home_team = :home AND away_team = :away AND season = :season
            """
            ),
            {"home": HOME, "away": AWAY, "season": SEASON},
        ).mappings().first()
        if not m:
            print("Spiel nicht gefunden")
            return
        ref = _parse_match_date(m["match_date"]) - timedelta(days=1)
        home_ids, away_ids = load_players(db, m["id"])
        names_rows = db.execute(
            text(
                """
            SELECT mp.side, mp.position, mp.name, mp.external_player_id::text AS pid
            FROM match_players mp WHERE mp.match_id = :mid
            ORDER BY mp.side, mp.position
            """
            ),
            {"mid": m["id"]},
        ).mappings().all()

    print(f"Spiel: {HOME} vs {AWAY}  Ergebnis XTTV: {m['team_result']}")
    print(f"Referenzdatum Modell (Vortag): {ref}\n")

    with SessionLocal() as db:
        with leakage_safe(ref):
            ref_date = ref
            ids = list(set(home_ids) | set(away_ids))
            names, profiles, matchups = _load_player_profiles(db, ids, ref_date)

    print("Stärke (Modell, vor Spiel):")
    for side, ids in (("Heim Saxen", home_ids), ("Gast Luftenberg", away_ids)):
        print(f"  {side}:")
        for pid in ids:
            p = profiles.get(pid, {})
            rc = p.get("rc_rating")
            games = p.get("games") or 0
            wins = p.get("wins") or 0
            wr = f"{100*wins/games:.0f}%" if games else "—"
            print(
                f"    {names.get(pid, pid):30} RC={rc if rc is not None else '—':>6} "
                f"Einzel {wins}/{games} ({wr}) Stärke={_combined_strength(p):.3f}"
            )

    with leakage_safe(ref):
        result = analyze_lineup(
            home_ids,
            AWAY,
            actual_opponent_ids=away_ids,
            own_is_home=True,
            own_team=HOME,
            opponent_on_letters=True,
        )
    rec = result.get("recommendation") or {}
    print(f"\nOptimale Heim-Aufstellung: {rec.get('players')}")
    print(f"Sieg {rec.get('team_win_probability', 0)*100:.1f}% · Erwartet {rec.get('expected_score_display')}")
    opp_pred = result.get("most_likely_opponent") or {}
    if opp_pred.get("players"):
        print(f"Wahrscheinlichste Gegner-Reihenfolge (Modell): {opp_pred.get('players')} ({opp_pred.get('probability', 0)*100:.1f}%)")

    with SessionLocal() as db:
        singles = db.execute(
            text(
                """
            SELECT g.sequence, g.home_player, g.away_player, g.result
            FROM match_games g
            WHERE g.match_id = :mid AND g.game_type = 'singles'
            ORDER BY g.sequence
            """
            ),
            {"mid": m["id"]},
        ).mappings().all()
    print("\nTatsächliche Einzel (Auszug):")
    for row in singles[:12]:
        print(f"  #{row['sequence']} {row['home_player']} vs {row['away_player']} → {row['result']}")


if __name__ == "__main__":
    main()
