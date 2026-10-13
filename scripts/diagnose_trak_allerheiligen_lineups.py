"""Diagnose: Trak vs Allerheiligen — how flat are the top lineups?"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from sqlalchemy import text

from app.analysis_service import (
    _combined_strength,
    _load_player_profiles,
    _reference_date,
    analyze_lineup,
)
from app.db import SessionLocal

HOME = "Tragwein/Kamig 2"
AWAY = "Allerheiligen 1"
# Fallback if names differ in DB
HOME_ALIASES = ("Tragwein/Kamig 2", "Tragwein/Kamig 2 ")


def resolve_match(db):
    for ht in HOME_ALIASES:
        row = db.execute(
            text(
                """
                SELECT id, match_date, home_team, away_team, season
                FROM xttv_matches
                WHERE home_team = :home AND away_team ILIKE :away
                ORDER BY match_date DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"home": ht, "away": "Allerheiligen%"},
        ).mappings().first()
        if row:
            return row
    return None


def load_squad_by_name(db, team: str, name_hints: tuple[str, ...]) -> dict[str, str]:
    """Map first-name hint -> external_player_id from recent match_players."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT mp.external_player_id::text AS pid, mp.name
            FROM match_players mp
            JOIN xttv_matches m ON m.id = mp.match_id
            WHERE (m.home_team = :team OR m.away_team = :team)
              AND mp.external_player_id IS NOT NULL
            """
        ),
        {"team": team},
    ).mappings().all()
    out = {}
    for hint in name_hints:
        hint_l = hint.lower()
        for r in rows:
            name = (r["name"] or "").lower()
            if hint_l in name.split() or name.startswith(hint_l):
                out[hint] = r["pid"]
                break
    return out


def main() -> None:
    own_hints = ("Niklas", "Julian", "Hannes", "Martin")
    opp_hints = ("Franz", "Martin", "Tobias", "Thomas")

    with SessionLocal() as db:
        m = resolve_match(db)
        ref = _reference_date(db)
        home_map = load_squad_by_name(db, HOME, own_hints)
        away_map = load_squad_by_name(db, AWAY, opp_hints)

    print(f"Referenzdatum Modell: {ref}")
    if m:
        print(f"Spiel in DB: {m['home_team']} vs {m['away_team']}  ({m['match_date']})  id={m['id']}")
    else:
        print(f"Kein Spiel {HOME} vs {AWAY} in DB — Analyse Phase B/C ohne fixe Gegner-Reihenfolge.")

    print("\nHeim-Kader (PassNr):")
    for h in own_hints:
        print(f"  {h}: {home_map.get(h, '???')}")

    print("\nGast-Kader (PassNr):")
    for h in opp_hints:
        print(f"  {h}: {away_map.get(h, '???')}")

    own_ids = [home_map[h] for h in own_hints if h in home_map]
    opp_ids = [away_map[h] for h in opp_hints if h in away_map]
    if len(own_ids) != 4:
        print("\nFEHLER: nicht alle 4 Heim-Spieler gefunden.")
        return
    if len(opp_ids) != 4:
        print("\nWARNUNG: nicht alle 4 Gegner gefunden — Phase B ohne actual_opponent_ids.")
        opp_ids = None

    with SessionLocal() as db:
        ids = list(set(own_ids) | set(opp_ids or []))
        names, profiles, _matchups = _load_player_profiles(db, ids, ref)

    print("\nStärke (Modell):")
    for label, pids in (("Heim", own_ids), ("Gast", opp_ids or [])):
        for pid in pids:
            p = profiles.get(pid, {})
            print(
                f"  [{label}] {names.get(pid, pid):28} RC={p.get('rc_rating')} "
                f"Stärke={_combined_strength(p):.4f}"
            )

    kwargs = dict(
        own_player_ids=own_ids,
        opponent_team=AWAY,
        own_is_home=True,
        own_team=HOME,
        opponent_on_letters=True,
    )
    if opp_ids and len(opp_ids) == 4:
        kwargs["actual_opponent_ids"] = opp_ids

    result = analyze_lineup(**kwargs)
    if not result.get("ok"):
        print(result)
        return

    rec = result["recommendation"]
    info = result.get("info_summary") or {}
    print(f"\nPhase: {result.get('phase')}")
    print(f"Optimale Aufstellung (A-D): {rec.get('players')}")
    print(
        f"Siegchance (Anzeige): {rec.get('team_win_probability', 0)*100:.3f}% "
        f"· Erwartet {rec.get('expected_score_display')}"
    )
    print(f"lineup_spread_pp (alle 24 Permutationen): {rec.get('lineup_spread_pp')}")
    print(f"top_lineup_margin_pp (1. vs 2.): {info.get('top_lineup_margin_pp')}")
    print(f"Vorteil vs Stärke-Aufstellung: {rec.get('advantage_vs_strength_lineup_pp')} pp")

    print("\nTop 10 Aufstellungen (Ranking-Siegchance, loss_pp_vs_optimal):")
    for item in (result.get("recommendations") or [])[:10]:
        abcd = "".join(
            "ABCD"[own_ids.index(pid)] if pid in own_ids else "?"
            for pid in item["own_player_ids"]
        )
        print(
            f"  #{item['rank']:2d} {abcd}  "
            f"p_win={item['team_win_probability']*100:.4f}%  "
            f"loss_pp={item.get('loss_pp_vs_optimal')}  "
            f"{', '.join(item.get('players') or [])}"
        )

    recs = result.get("recommendations") or []
    if len(recs) >= 2:
        spread_rank = (recs[0]["team_win_probability"] - recs[-1]["team_win_probability"]) * 100
        print(f"\nSpread Rang 1 vs Rang 24: {spread_rank:.4f} Prozentpunkte")


if __name__ == "__main__":
    main()
