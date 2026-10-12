"""Evaluate league 421 by distinct matchday among 4v4 games (legacy helper)."""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))
sys.path.insert(0, str(SCRIPT_DIR))

from sqlalchemy import text

from app.analysis_service import _parse_match_date, analyze_lineup
from app.db import SessionLocal
from compare_optimal_vs_actual_411 import (
    leakage_safe,
    load_players,
    outcome_label,
    parse_team_result,
    predicted_outcome,
)

LEAGUE_LIKE = "421%"
SEASON = "2026/2027"


def _match_days(rows) -> list[str]:
    return sorted({(r["match_date"] or "")[:10] for r in rows}, key=lambda d: _parse_match_date(d) or d)


def _fetch_eligible(db) -> list:
    return db.execute(
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, required=True, help="1-based round index by distinct matchday")
    args = parser.parse_args()

    with SessionLocal() as db:
        all_rows = _fetch_eligible(db)

    if not all_rows:
        print(f"Keine abgeschlossenen 4v4-Spiele für {LEAGUE_LIKE} Saison {SEASON}.")
        return 1

    days = _match_days(all_rows)
    round_idx = args.round
    if round_idx < 1 or round_idx > len(days):
        print(f"Runde {round_idx} unbekannt. Spieltage mit 4v4: {len(days)}")
        for i, d in enumerate(days, 1):
            print(f"  Runde {i}: {d}")
        return 1

    day = days[round_idx - 1]
    round_matches = [r for r in all_rows if (r["match_date"] or "")[:10] == day]

    print(f"Liga: {round_matches[0]['league']}")
    print(f"Saison: {SEASON}")
    print(f"Spieltag {day}: {len(round_matches)} Spiele (4:4)\n")

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
            try:
                with leakage_safe(ref):
                    analysis = analyze_lineup(
                        home_ids,
                        m["away_team"],
                        actual_opponent_ids=away_ids,
                        own_is_home=True,
                        own_team=m["home_team"],
                        opponent_on_letters=True,
                        use_spieltyp=False,
                    )
            except Exception as exc:
                errors.append(f"{m['home_team']} vs {m['away_team']}: {exc}")
                continue

            rec = analysis.get("recommendation") or {}
            pred_outcome = predicted_outcome(
                rec.get("team_win_probability", 0.0),
                rec.get("team_draw_probability", 0.0),
            )
            parsed = parse_team_result(m["team_result"])
            if not parsed:
                continue
            home_score, away_score = parsed
            actual_outcome = outcome_label(home_score, away_score)
            results.append(
                {
                    "spiel": f"{m['home_team']} – {m['away_team']}",
                    "expected": rec.get("expected_score_display", ""),
                    "win_pct": rec.get("team_win_probability", 0.0) * 100,
                    "draw_pct": rec.get("team_draw_probability", 0.0) * 100,
                    "prognose_kat": pred_outcome,
                    "tatsaechlich": f"{home_score}:{away_score}",
                    "actual_kat": actual_outcome,
                    "treffer": pred_outcome == actual_outcome,
                }
            )

    print("| Spiel (Heim – Gast) | App: erwartetes Ergebnis | App: Sieg / Remis | App: Kategorie | Tatsächlich (XTTV) | Kategorie OK? |")
    print("|---------------------|--------------------------|-------------------|----------------|---------------------|---------------|")
    for r in results:
        ok = "Ja" if r["treffer"] else "Nein"
        print(
            f"| {r['spiel']} | **{r['expected']}** | {r['win_pct']:.1f}% / {r['draw_pct']:.1f}% | "
            f"{r['prognose_kat']} | **{r['tatsaechlich']}** ({r['actual_kat']} Heim) | {ok} |"
        )

    hits = sum(1 for r in results if r["treffer"])
    print(f"\nTreffer: {hits}/{len(results)}")
    if errors:
        print("\nFehler:")
        for e in errors:
            print(f"  - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
