"""Round-1 evaluation for league 421: app prediction (Phase C, 8 known players) vs XTTV result."""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))
sys.path.insert(0, str(SCRIPT_DIR))

from sqlalchemy import text

from app.analysis_service import _parse_match_date
from app.db import SessionLocal
from app.analysis_service import analyze_lineup
from compare_optimal_vs_actual_411 import (
    leakage_safe,
    load_players,
    outcome_label,
    parse_team_result,
    predicted_outcome,
)

LEAGUE_LIKE = "421%"
SEASON = "2026/2027"


def main() -> int:
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
            SELECT m.id, m.match_date, m.home_team, m.away_team, m.team_result, m.league
            FROM xttv_matches m
            WHERE m.league LIKE :league_like
              AND m.season = :season
              AND m.team_result IS NOT NULL
              AND m.team_result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND (
                SELECT COUNT(*) FROM match_players mp
                WHERE mp.match_id = m.id AND mp.side = 'home' AND mp.external_player_id IS NOT NULL
              ) = 4
              AND (
                SELECT COUNT(*) FROM match_players mp
                WHERE mp.match_id = m.id AND mp.side = 'away' AND mp.external_player_id IS NOT NULL
              ) = 4
            ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
            """
            ),
            {"league_like": LEAGUE_LIKE, "season": SEASON},
        ).mappings().all()

    if not rows:
        print(f"Keine abgeschlossenen 4v4-Spiele für {LEAGUE_LIKE} Saison {SEASON}.")
        return 1

    league_name = rows[0]["league"]
    round_matches = rows
    days = sorted({(r["match_date"] or "")[:10] for r in rows}, key=lambda d: _parse_match_date(d) or d)

    print(f"Liga: {league_name}")
    print(f"Saison: {SEASON}")
    print(f"Erste Spielrunde: {len(round_matches)} abgeschlossene Spiele (4:4 Aufstellungen)")
    print(f"Spieltage: {', '.join(days)}\n")

    results = []
    errors = []

    with SessionLocal() as db:
        for m in round_matches:
            match_day = _parse_match_date(m["match_date"])
            if not match_day:
                errors.append(f"{m['id']}: Datum unparsebar")
                continue
            ref = match_day - timedelta(days=1)
            home_ids, away_ids = load_players(db, m["id"])
            home_names = db.execute(
                text(
                    """
                SELECT mp.position, mp.name
                FROM match_players mp
                WHERE mp.match_id = :mid AND mp.side = 'home'
                ORDER BY mp.position
                """
                ),
                {"mid": m["id"]},
            ).mappings().all()
            away_names = db.execute(
                text(
                    """
                SELECT mp.position, mp.name
                FROM match_players mp
                WHERE mp.match_id = :mid AND mp.side = 'away'
                ORDER BY mp.position
                """
                ),
                {"mid": m["id"]},
            ).mappings().all()

            own_ids, opp_ids, opponent_team = home_ids, away_ids, m["away_team"]
            own_lineup = ", ".join(r["name"] or "?" for r in home_names)
            opp_lineup = ", ".join(r["name"] or "?" for r in away_names)
            try:
                with leakage_safe(ref):
                    analysis = analyze_lineup(
                        own_ids,
                        opponent_team,
                        actual_opponent_ids=opp_ids,
                        own_is_home=True,
                        own_team=m["home_team"],
                        opponent_on_letters=True,
                        use_spieltyp=False,
                    )
            except Exception as exc:
                errors.append(f"{m['home_team']} vs {m['away_team']}: {exc}")
                continue

            rec = analysis.get("recommendation") or {}
            pred = {
                "win_prob": rec.get("team_win_probability", 0.0),
                "draw_prob": rec.get("team_draw_probability", 0.0),
                "expected_score": rec.get("expected_score_display", ""),
                "phase": analysis.get("phase"),
            }

            parsed = parse_team_result(m["team_result"])
            if not parsed:
                continue
            home_score, away_score = parsed
            actual_outcome = outcome_label(home_score, away_score)
            pred_outcome = predicted_outcome(pred["win_prob"], pred["draw_prob"])
            match_ok = pred_outcome == actual_outcome

            results.append(
                {
                    "spiel": f"{m['home_team']} – {m['away_team']}",
                    "eigene_4": own_lineup,
                    "gegner_4": opp_lineup,
                    "prognose": f"Sieg {pred['win_prob']*100:.1f}% · Remis {pred['draw_prob']*100:.1f}% · Erwartet {pred['expected_score']}",
                    "prognose_ergebnis": pred_outcome,
                    "tatsaechlich": f"{home_score}:{away_score} ({actual_outcome} Heim)",
                    "treffer": match_ok,
                    "phase": pred.get("phase"),
                }
            )

    for r in results:
        print("—" * 60)
        print(r["spiel"])
        print(f"  Eigene 4 (Heim): {r['eigene_4']}")
        print(f"  Gegner 4: {r['gegner_4']}")
        print(f"  App (Phase {r['phase']}): {r['prognose']}")
        print(f"  Prognostiziertes Ergebnis (Kategorie): {r['prognose_ergebnis']}")
        print(f"  Tatsächlich XTTV: {r['tatsaechlich']}")
        print(f"  Sieg/Niederlage/7:7 getroffen? {'Ja' if r['treffer'] else 'Nein'}")

    hits = sum(1 for r in results if r["treffer"])
    print("\n" + "=" * 60)
    print(f"Zusammenfassung: {hits}/{len(results)} Ergebniskategorien (Sieg/Niederlage/7:7) getroffen")
    if errors:
        print("\nFehler:")
        for e in errors:
            print(f"  - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
