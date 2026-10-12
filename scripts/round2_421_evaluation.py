"""Round 2 (XTTV Runde 2) evaluation for league 421 season 2026/2027."""
from __future__ import annotations

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

# XTTV schedule R2/DG1 (421 RK Linz Umg. / MV Ost 2026/2027)
ROUND2_MEIDS = (462235, 462237, 462238)


def main() -> int:
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
            SELECT m.id, m.match_date, m.home_team, m.away_team, m.team_result, m.league,
                   m.external_id,
                   (SELECT COUNT(*) FROM match_players mp
                    WHERE mp.match_id = m.id AND mp.side = 'home'
                      AND mp.external_player_id IS NOT NULL) AS hc,
                   (SELECT COUNT(*) FROM match_players mp
                    WHERE mp.match_id = m.id AND mp.side = 'away'
                      AND mp.external_player_id IS NOT NULL) AS ac
            FROM xttv_matches m
            WHERE m.external_id::bigint = ANY(:meids)
            ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
            """
            ),
            {"meids": list(ROUND2_MEIDS)},
        ).mappings().all()

    missing = set(ROUND2_MEIDS) - {int(r["external_id"]) for r in rows}
    if missing:
        print("FEHLT in DB (bitte importieren):", sorted(missing))
        return 1

    league_name = rows[0]["league"] if rows else "421"
    print(f"Liga: {league_name}")
    print(f"Saison: 2026/2027")
    print(f"Spielrunde 2 (XTTV): {len(ROUND2_MEIDS)} Spiele\n")

    results = []
    errors = []
    skipped = []

    with SessionLocal() as db:
        for m in rows:
            if m["hc"] != 4 or m["ac"] != 4:
                skipped.append(
                    f"{m['home_team']} – {m['away_team']} ({m['team_result']}, {m['hc']}/{m['ac']})"
                )
                continue
            if not m["team_result"]:
                skipped.append(f"{m['home_team']} – {m['away_team']} (kein Ergebnis)")
                continue

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
            win_prob = rec.get("team_win_probability", 0.0)
            draw_prob = rec.get("team_draw_probability", 0.0)
            expected = rec.get("expected_score_display", "")
            parsed = parse_team_result(m["team_result"])
            if not parsed:
                continue
            home_score, away_score = parsed
            actual_outcome = outcome_label(home_score, away_score)
            pred_outcome = predicted_outcome(win_prob, draw_prob)
            results.append(
                {
                    "spiel": f"{m['home_team']} – {m['away_team']}",
                    "expected": expected,
                    "win_pct": win_prob * 100,
                    "draw_pct": draw_prob * 100,
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
    print(f"\nTreffer Kategorie Sieg/Niederlage/7:7: {hits}/{len(results)}")
    if skipped:
        print("\nAusgeschlossen:")
        for s in skipped:
            print(f"  - {s}")
    if errors:
        print("\nFehler:")
        for e in errors:
            print(f"  - {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
