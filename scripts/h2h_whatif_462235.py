"""H2H / RC / model decomposition for Trak2–Klam upsets (MEID 462235, ref 06.10.2026)."""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))
sys.path.insert(0, str(SCRIPT_DIR))

from sqlalchemy import bindparam, text

from app.analysis_service import (
    H2H_LEGACY_PRIOR_GAMES,
    H2H_LEGACY_PRIOR_WINS,
    H2H_SHRINKAGE_MATCHES,
    _combined_strength,
    _h2h_blended_probability,
    _load_player_profiles,
    _logistic,
    _matchup_probability,
)
from app.db import SessionLocal
from compare_optimal_vs_actual_411 import leakage_safe

REF = date(2026, 10, 6)
TRAK = {
    "17885": "Meisinger Alexander",
    "23784": "Friedinger Niklas",
    "21772": "Undesser Manuel",
    "22183": "Schmalzer Martin",
}
KLAM = {
    "20845": "Fürnhammer Johannes",
    "22776": "Kieslinger Philipp",
    "24477": "Kühberger Stefan",
    "70394": "Kühberger Marlene",
}
ALL = {**TRAK, **KLAM}

# Actual singles from the match (own=Trak home)
ACTUAL = [
    ("23784", "70394", "3:2", True),
    ("21772", "20845", "0:3", False),
    ("22183", "24477", "2:3", False),
    ("17885", "22776", "3:1", True),
    ("23784", "20845", "0:3", False),
    ("21772", "70394", "1:3", False),
    ("17885", "24477", "3:1", True),
    ("22183", "22776", "3:2", True),
    ("17885", "20845", "3:0", True),
    ("23784", "22776", "3:2", True),
    ("21772", "24477", "0:3", False),
    ("22183", "70394", "0:3", False),
]


def _base_prob(a, b, profiles):
    own_strength = _combined_strength(profiles[a], "home")
    opp_strength = _combined_strength(profiles[b], "away")
    return _logistic(own_strength - opp_strength)


def _h2h_games(db, a: str, b: str) -> list[dict]:
    stmt = text(
        """
        WITH base AS (
            SELECT m.match_date, m.home_team, m.away_team, g.result,
                   hp.external_player_id::text AS home_id,
                   ap.external_player_id::text AS away_id,
                   split_part(trim(g.result), ':', 1)::int AS hs,
                   split_part(trim(g.result), ':', 2)::int AS aws
            FROM match_games g
            JOIN match_players hp ON hp.match_id = g.match_id AND hp.side = 'home'
                AND hp.position = g.home_position
            JOIN match_players ap ON ap.match_id = g.match_id AND ap.side = 'away'
                AND ap.position = g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type = 'singles'
              AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') <= :ref
              AND (
                (hp.external_player_id::text = :a AND ap.external_player_id::text = :b)
                OR (hp.external_player_id::text = :b AND ap.external_player_id::text = :a)
              )
        )
        SELECT * FROM base
        ORDER BY to_date(substring(match_date from 1 for 10), 'DD.MM.YYYY'), match_date
        """
    )
    rows = db.execute(stmt, {"a": a, "b": b, "ref": REF}).mappings().all()
    out = []
    for r in rows:
        if r["home_id"] == a:
            ow, og = r["hs"], r["aws"]
            side = "Heim" if r["home_id"] in TRAK else "Heim(Klam)"
        else:
            ow, og = r["aws"], r["hs"]
        trak_won = (r["home_id"] == a and r["hs"] > r["aws"]) or (
            r["away_id"] == a and r["aws"] > r["hs"]
        )
        out.append(
            {
                "date": (r["match_date"] or "")[:10],
                "teams": f"{r['home_team']} – {r['away_team']}",
                "score": r["result"],
                "trak_won": trak_won,
            }
        )
    return out


def _profile_line(pid: str, profiles: dict) -> str:
    p = profiles.get(pid, {})
    rc = p.get("rc_rating")
    return (
        f"RC={rc if rc is not None else '?'}  "
        f"Spiele {p.get('wins', 0)}/{p.get('games', 0)}  "
        f"Heim {p.get('home_wins', 0)}/{p.get('home_games', 0)}  "
        f"Auswärts {p.get('away_wins', 0)}/{p.get('away_games', 0)}  "
        f"Trend={p.get('rc_trend')}"
    )


def main() -> None:
    ids = list(ALL.keys())
    with SessionLocal() as db:
        with leakage_safe(REF):
            names, profiles, matchups = _load_player_profiles(db, ids, REF)

    print(f"Stichtag: {REF} (Tag vor Trak2–Klam 07.10.2026)\n")
    print("Spielerprofile (Modell-Inputs):")
    for pid, label in ALL.items():
        print(f"  {label}: {_profile_line(pid, profiles)}")

    upsets = [x for x in ACTUAL if not x[3]]
    print("\n" + "=" * 72)
    print("UPSET-EINZEL (Trak verlor, Modell favorisierte Trak oder klarer Gegner hielt)")
    print("=" * 72)

    with SessionLocal() as db:
        for own, opp, result, trak_won in ACTUAL:
            p_full = _matchup_probability(own, opp, profiles, matchups, own_is_home=True)
            p_base = _base_prob(own, opp, profiles)
            w, g = matchups.get((own, opp), (0, 0))
            h2h_list = _h2h_games(db, own, opp)
            label_own = ALL[own]
            label_opp = ALL[opp]
            fav = "Trak" if p_full >= 0.5 else "Klam"
            upset = (p_full >= 0.55 and not trak_won) or (p_full <= 0.45 and trak_won)
            if not upset and trak_won:
                continue  # focus detailed block on losses and key wins vs Fürnhammer
            if trak_won and p_full < 0.55:
                continue

            print(f"\n{label_own} vs {label_opp}  (Spieltag: {result}, Sieger: {'Trak' if trak_won else 'Klam'})")
            print(f"  Modell P(Trak): {p_full*100:.1f}%  (nur RC/Stärke: {p_base*100:.1f}%)")
            print(f"  H2H in DB bis Stichtag: {w}:{g - w} aus {g} Duellen")
            if g:
                p_shrink = (w + H2H_SHRINKAGE_MATCHES * p_base) / (g + H2H_SHRINKAGE_MATCHES)
                direct = (w + H2H_LEGACY_PRIOR_WINS) / (g + H2H_LEGACY_PRIOR_GAMES)
                print(f"  H2H-Shrinkage allein: {p_shrink*100:.1f}%  Legacy-Direktquote: {direct*100:.1f}%")
            if h2h_list:
                print("  Direktspiele (chronologisch):")
                for h in h2h_list:
                    mark = "Trak+" if h["trak_won"] else "Klam+"
                    print(f"    {h['date']} {h['teams']}  {h['score']}  [{mark}]")
            else:
                print("  → Kein Direktspiel in der DB: Prognose = reine RC/Stärke-Schätzung")

            ow_s = _combined_strength(profiles[own], "home")
            op_s = _combined_strength(profiles[opp], "away")
            print(f"  Stärke-Score Heim(Trak): {ow_s:.3f}  Gast(Klam): {op_s:.3f}  Δ={ow_s - op_s:.3f}")

    print("\n" + "=" * 72)
    print("ALLE 12 EINZEL – Kurzüberblick")
    print("=" * 72)
    exp_trak = 0.0
    for own, opp, result, trak_won in ACTUAL:
        p = _matchup_probability(own, opp, profiles, matchups, own_is_home=True)
        exp_trak += p
        w, g = matchups.get((own, opp), (0, 0))
        flag = ""
        if p >= 0.55 and not trak_won:
            flag = " UPSET"
        elif p <= 0.45 and trak_won:
            flag = " UPSET"
        print(
            f"  {ALL[own][:12]:12} vs {ALL[opp][:12]:12}  P(Trak)={p*100:5.1f}%  H2H {w}/{g}  "
            f"ist {result}{flag}"
        )
    print(f"\nSumme erwartete Trak-Einzelpunkte (Modell): {exp_trak:.2f} von 12")
    print("(Teamprognose 8:4 enthält zusätzlich Doppel + Aufstellungsoptimierung; hier fixe Kreuz-A–D.)")


if __name__ == "__main__":
    main()
