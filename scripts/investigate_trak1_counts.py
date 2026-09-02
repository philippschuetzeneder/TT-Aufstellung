"""Investigate Tragwein/Kamig 1 match counts and RC spread distribution."""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import text
from app.db import SessionLocal

SEASON = "2025/2026"
TEAM = "Tragwein/Kamig 1"


def main() -> None:
    db = SessionLocal()

    print(f"=== All matches for '{TEAM}' season {SEASON} ===")
    rows = db.execute(
        text(
            """
        SELECT m.id, m.match_date, m.home_team, m.away_team, m.team_result,
               count(mp.id) FILTER (WHERE mp.side='home') AS home_players,
               count(mp.id) FILTER (WHERE mp.side='away') AS away_players
        FROM xttv_matches m
        LEFT JOIN match_players mp ON mp.match_id = m.id AND mp.external_player_id IS NOT NULL
        WHERE m.season = :season
          AND (m.home_team = :team OR m.away_team = :team)
        GROUP BY m.id, m.match_date, m.home_team, m.away_team, m.team_result
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
        """
        ),
        {"season": SEASON, "team": TEAM},
    ).mappings().all()
    print(f"Total matches in xttv_matches: {len(rows)}")
    with_four = [r for r in rows if (
        (r["home_team"] == TEAM and r["home_players"] == 4)
        or (r["away_team"] == TEAM and r["away_players"] == 4)
    )]
    print(f"Matches where {TEAM} has exactly 4 players: {len(with_four)}")
    without_four = [r for r in rows if r not in with_four]
    if without_four:
        print("Matches WITHOUT 4 players on team side:")
        for r in without_four:
            side = "home" if r["home_team"] == TEAM else "away"
            n = r["home_players"] if side == "home" else r["away_players"]
            print(f"  {r['match_date']} vs {r['away_team'] if side=='home' else r['home_team']}: {n} players")

    print(f"\n=== Backtest case count (opponent_team = '{TEAM}') ===")
    print(f"Backtest opponent cases (4-player side): {len(with_four)}")

    print(f"\n=== Team name variants containing 'Tragwein' or 'Kamig' ===")
    names = db.execute(
        text(
            """
        SELECT DISTINCT team, count(*) AS matches FROM (
          SELECT home_team AS team FROM xttv_matches WHERE season=:season
          UNION ALL
          SELECT away_team FROM xttv_matches WHERE season=:season
        ) t GROUP BY team HAVING team ILIKE '%tragwein%' OR team ILIKE '%kamig%'
        ORDER BY team
        """
        ),
        {"season": SEASON},
    ).mappings().all()
    for n in names:
        print(f"  {n['matches']:3} refs  {n['team']}")

    print(f"\n=== RC spread: per-match quartet vs full team roster ===")
    # All players who appeared for Tragwein/Kamig 1
    roster_rows = db.execute(
        text(
            """
        SELECT DISTINCT mp.external_player_id::text AS pid, mp.name
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE m.season = :season AND mp.external_player_id IS NOT NULL
          AND ((m.home_team = :team AND mp.side='home') OR (m.away_team = :team AND mp.side='away'))
        """
        ),
        {"season": SEASON, "team": TEAM},
    ).mappings().all()
    roster_ids = [r["pid"] for r in roster_rows]
    print(f"Distinct players in roster: {len(roster_ids)}")

    # Latest RC per player
    rc_rows = db.execute(
        text(
            """
        SELECT DISTINCT ON (player_id) player_id::text, rc_rating
        FROM rc_snapshots
        WHERE player_id::text = ANY(:ids)
        ORDER BY player_id, observed_at DESC
        """
        ),
        {"ids": roster_ids},
    ).mappings().all()
    rc_map = {r["player_id"]: float(r["rc_rating"]) for r in rc_rows if r["rc_rating"]}
    rc_vals = sorted(rc_map.values())
    if rc_vals:
        print(f"Roster RC range: {rc_vals[0]:.0f} – {rc_vals[-1]:.0f}, spread = {rc_vals[-1]-rc_vals[0]:.0f} ({len(rc_vals)} with RC)")

    # Per-match quartet RC spread for Tragwein/Kamig 1
    match_quartets = db.execute(
        text(
            """
        SELECT m.id, m.match_date,
               array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE m.season = :season AND mp.external_player_id IS NOT NULL
          AND ((m.home_team = :team AND mp.side='home') OR (m.away_team = :team AND mp.side='away'))
        GROUP BY m.id, m.match_date
        HAVING count(*) = 4
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY')
        """
        ),
        {"season": SEASON, "team": TEAM},
    ).mappings().all()

    spreads = []
    for mq in match_quartets:
        vals = [rc_map.get(pid) for pid in mq["lineup"] if rc_map.get(pid)]
        if len(vals) >= 2:
            spreads.append(max(vals) - min(vals))
    if spreads:
        spreads.sort()
        print(f"Per-match quartet RC spread ({len(spreads)} matches):")
        print(f"  min={spreads[0]:.0f} median={spreads[len(spreads)//2]:.0f} max={spreads[-1]:.0f}")
        print(f"  >=40: {sum(1 for s in spreads if s>=40)}/{len(spreads)}")
        print(f"  <40:  {sum(1 for s in spreads if s<40)}/{len(spreads)}")

    # Season-wide: all 4-player sides RC spread
    print(f"\n=== RC spread ALL teams, all 4-player sides, season {SEASON} ===")
    all_sides = db.execute(
        text(
            """
        SELECT CASE WHEN mp.side='home' THEN m.home_team ELSE m.away_team END AS team,
               array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE m.season = :season AND mp.external_player_id IS NOT NULL
        GROUP BY m.id, team
        HAVING count(*) = 4
        """
        ),
        {"season": SEASON},
    ).mappings().all()

    # Load all RC in one go
    all_ids = list({pid for row in all_sides for pid in row["lineup"]})
    rc_all = db.execute(
        text(
            """
        SELECT DISTINCT ON (player_id) player_id::text, rc_rating
        FROM rc_snapshots WHERE player_id::text = ANY(:ids)
        ORDER BY player_id, observed_at DESC
        """
        ),
        {"ids": all_ids},
    ).mappings().all()
    rc_all_map = {r["player_id"]: float(r["rc_rating"]) for r in rc_all if r["rc_rating"]}

    all_spreads = []
    for row in all_sides:
        vals = [rc_all_map.get(pid) for pid in row["lineup"] if rc_all_map.get(pid)]
        if len(vals) >= 4:
            all_spreads.append(max(vals) - min(vals))
    all_spreads.sort()
    n = len(all_spreads)
    print(f"Total 4-player sides: {n}")
    print(f"Spread min={all_spreads[0]:.0f} p25={all_spreads[n//4]:.0f} median={all_spreads[n//2]:.0f} p75={all_spreads[3*n//4]:.0f} max={all_spreads[-1]:.0f}")
    for threshold in (40, 100, 150, 200):
        cnt = sum(1 for s in all_spreads if s >= threshold)
        print(f"  spread >= {threshold}: {cnt}/{n} ({cnt/n*100:.1f}%)")

    db.close()


if __name__ == "__main__":
    main()
