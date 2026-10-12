"""Why did Tragwein/Kamig 2 vs Klam 1 (462235) end 7:7 vs predicted ~8:4?"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))
sys.path.insert(0, str(SCRIPT_DIR))

from sqlalchemy import text

from app.analysis_service import (
    _load_player_profiles,
    _matchup_probability,
    _parse_match_date,
    _position_index,
    analyze_lineup,
)
from app.db import SessionLocal
from compare_optimal_vs_actual_411 import leakage_safe, load_players

MEID = 462235


def main() -> None:
    with SessionLocal() as db:
        m = db.execute(
            text(
                "SELECT id, match_date, home_team, away_team, team_result, home_scheme, away_scheme "
                "FROM xttv_matches WHERE external_id = :e"
            ),
            {"e": str(MEID)},
        ).mappings().first()
        if not m:
            print("Match not found")
            return

        players = db.execute(
            text(
                """
                SELECT side, position, name, external_player_id::text AS pid
                FROM match_players WHERE match_id = :mid ORDER BY side, position
                """
            ),
            {"mid": m["id"]},
        ).mappings().all()

        games = db.execute(
            text(
                """
                SELECT sequence, game_type, home_position, away_position,
                       home_player, away_player, result, sets
                FROM match_games WHERE match_id = :mid ORDER BY sequence
                """
            ),
            {"mid": m["id"]},
        ).mappings().all()

    match_day = _parse_match_date(m["match_date"])
    ref = match_day - timedelta(days=1) if match_day else date.today()

    print(f"{m['home_team']} vs {m['away_team']}  Ergebnis {m['team_result']}")
    print(f"Datum {m['match_date']}  Heim-System {m['home_scheme']}  Gast {m['away_scheme']}\n")

    by_side_pos: dict[tuple[str, str], dict] = {}
    for p in players:
        by_side_pos[(p["side"], p["position"] or "")] = p

    print("Aufstellungen:")
    for side, label in (("home", "Heim Trak2"), ("away", "Gast Klam")):
        row = [x for x in players if x["side"] == side]
        row.sort(key=lambda x: _position_index(x["position"]))
        print(f"  {label}: " + ", ".join(f"{x['position']}={x['name']} ({x['pid']})" for x in row))

    def parse_set_score(res: str) -> tuple[int, int] | None:
        if not res or ":" not in res:
            return None
        left = res.split(":", 1)[0].strip()
        right = res.split(":", 1)[1].strip().split()[0]
        try:
            return int(left), int(right)
        except ValueError:
            return None

    home_pts = away_pts = 0
    print("\nSpielverlauf:")
    for g in games:
        sc = parse_set_score(g["result"] or "")
        if sc:
            hs, aws = sc
            if hs > aws:
                home_pts += 1
                winner = "Heim"
            elif aws > hs:
                away_pts += 1
                winner = "Gast"
            else:
                winner = "?"
        else:
            winner = "?"
        print(
            f"  #{g['sequence']} {g['game_type']:8} {g['home_position']}/{g['away_position']} "
            f"{g['home_player']} vs {g['away_player']}  {g['result']}  -> {winner}  sets={g['sets'] or '-'}"
        )
    print(f"\nEinzel/Doppel-Punkte (Siege): Heim {home_pts} : Gast {away_pts}")

    with SessionLocal() as db:
        home_ids, away_ids = load_players(db, m["id"])
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

    rec = analysis.get("recommendation") or {}
    print("\nApp-Empfehlung (Phase", analysis.get("phase"), "):")
    print(f"  Erwartet {rec.get('expected_score_display')}  Sieg {rec.get('team_win_probability',0)*100:.1f}%  Remis {rec.get('team_draw_probability',0)*100:.1f}%")
    opt = rec.get("own_lineup") or rec.get("lineup") or {}
    if isinstance(opt, dict):
        print("  Empfohlene Heim-A/B/C/D:", opt)

    # Actual home positions from games (singles)
    actual_home_order = {}
    for g in games:
        if g["game_type"] == "singles" and g["home_position"]:
            actual_home_order[g["away_position"]] = g["home_position"]

    print("\n  Tatsächliche Heim-Zuordnung (Gegner-Slot -> Heim-Slot):", dict(sorted(actual_home_order.items())))

    # Model win prob for each ACTUAL singles pairing
    print("\nModell P(Sieg Heim) für die tatsächlich gespielten Einzel (Stichtag vor Spiel):")
    with SessionLocal() as db:
        with leakage_safe(ref):
            ids = [p["pid"] for p in players if p["pid"]]
            _names, profiles, matchups = _load_player_profiles(db, ids, ref)
            for g in games:
                if g["game_type"] != "singles":
                    continue
                hp = by_side_pos.get(("home", g["home_position"]))
                ap = by_side_pos.get(("away", g["away_position"]))
                if not hp or not ap:
                    continue
                p = _matchup_probability(
                    hp["pid"], ap["pid"], profiles, matchups, own_is_home=True, use_spieltyp=False
                )
                sc = parse_set_score(g["result"] or "")
                won = sc[0] > sc[1] if sc else None
                surprise = ""
                if won is not None:
                    if p >= 0.55 and not won:
                        surprise = "  *** Upset (Favorit verlor)"
                    elif p <= 0.45 and won:
                        surprise = "  *** Upset (Underdog gewann)"
                print(
                    f"  {g['home_position']} {hp['name']} vs {g['away_position']} {ap['name']}: "
                    f"P(Heim)={p*100:.1f}%  Ergebnis {g['result']}{surprise}"
                )

            print("\nDoppel:")
            for g in games:
                if g["game_type"] != "doubles":
                    continue
                print(f"  {g['home_player']} vs {g['away_player']}  {g['result']}")

    # Compare optimal vs actual lineup if analysis exposes spread
    spread = analysis.get("spread") or analysis.get("scenarios")
    if rec.get("why"):
        print("\nKurz 'why' aus Analyse:", str(rec.get("why"))[:500])


if __name__ == "__main__":
    main()
