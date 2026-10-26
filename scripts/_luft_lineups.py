"""Luft lineup history vs displayed most-likely quartet."""
from collections import Counter

from sqlalchemy import text

from app.analysis_service import _raw_team_lineup_scenarios
from app.db import SessionLocal

with SessionLocal() as db:
    teams = db.execute(
        text(
            """
            SELECT team, COUNT(*) FROM (
              SELECT home_team AS team FROM xttv_matches
              UNION ALL
              SELECT away_team FROM xttv_matches
            ) t
            WHERE team ILIKE '%luft%' OR team ILIKE '%tragwein%' OR team ILIKE '%kamig%'
            GROUP BY team
            ORDER BY team
            """
        )
    ).all()
    print("TEAMS")
    for row in teams:
        print(row)

    luft = next((t[0] for t in teams if "luft" in t[0].lower()), None)
    print("LUFT", luft)
    if not luft:
        raise SystemExit(0)

    rows = db.execute(
        text(
            """
            SELECT m.external_id, m.match_date, m.home_team, m.away_team,
                   mp.side, mp.position, mp.name, mp.external_player_id
            FROM xttv_matches m
            JOIN match_players mp ON mp.match_id = m.id
            WHERE m.home_team = :team OR m.away_team = :team
            ORDER BY m.match_date DESC, m.id DESC, mp.side, mp.position
            """
        ),
        {"team": luft},
    ).mappings().all()

    by_match = {}
    for r in rows:
        by_match.setdefault(r["external_id"], {"date": r["match_date"], "home": r["home_team"], "away": r["away_team"], "players": []})
        by_match[r["external_id"]]["players"].append(r)

    counts = Counter()
    print("\nMATCHES", len(by_match))
    for ext, info in by_match.items():
        side = "home" if info["home"] == luft else "away"
        lineup = [p for p in info["players"] if p["side"] == side and str(p["position"]) in {"A", "B", "C", "D", "1", "2", "3", "4"}]
        names = [p["name"] for p in lineup]
        print(ext, info["date"], "vs", info["away"] if side == "home" else info["home"], " | ".join(f"{p['position']}:{p['name']}" for p in lineup))
        for p in lineup:
            counts[p["name"]] += 1
    print("\nCOUNTS")
    for name, n in counts.most_common():
        print(f"{n:3} {name}")

    scenarios, names = _raw_team_lineup_scenarios(db, luft)
    print("\nTOP SCENARIOS", len(scenarios))
    for prob, order in scenarios[:5]:
        print(round(prob, 3), [names.get(pid, pid) for pid in order])
