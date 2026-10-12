from app.xttv_import import fetch_match
from app.xttv_parser import parse_match

for meid in (462235, 462237, 462238):
    html, *_ = fetch_match(meid)
    p = parse_match(html, meid)
    players = p.get("players") or []
    home = [x for x in players if x.get("side") == "home"]
    away = [x for x in players if x.get("side") == "away"]
    print(
        f"meid={meid} {p.get('home_team')} vs {p.get('away_team')} "
        f"result={p.get('team_result')} players={len(home)}/{len(away)} "
        f"walkover={p.get('has_walkover')} count={p.get('player_count')}"
    )
