"""Audit league seasons in DB vs list_leagues()."""
from collections import defaultdict

from sqlalchemy import text

from app.db import SessionLocal
from app.player_analysis_service import (
    CURRENT_SEASON,
    _league_group,
    _season_label,
    _season_sort_key,
    list_leagues,
)

with SessionLocal() as session:
    rows = session.execute(
        text(
            "SELECT league, COUNT(*) AS c FROM xttv_matches "
            "WHERE league IS NOT NULL GROUP BY league ORDER BY league"
        )
    ).mappings()
    by_group: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        group = _league_group(row["league"])
        if not group:
            continue
        by_group.setdefault(group, []).append((row["league"], int(row["c"])))

print("=== Groups where latest season is NOT", CURRENT_SEASON, "===")
for group in sorted(by_group):
    entries = by_group[group]
    latest_league, match_count = max(entries, key=lambda item: _season_sort_key(item[0]))
    season = _season_label(latest_league)
    if season != CURRENT_SEASON:
        seasons = sorted({_season_label(league) for league, _ in entries})
        print(f"  {group}: latest={season} ({match_count} matches) seasons_in_db={seasons}")

data = list_leagues()
not_current = [item for item in data["leagues"] if item.get("season") != CURRENT_SEASON]
print(f"\n=== list_leagues(): {data['count']} total, {len(not_current)} not {CURRENT_SEASON} ===")
for item in not_current:
    print(f"  {item['id']} -> {item['season']} ({item['match_count']} matches)")
