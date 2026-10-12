from app.xttv_import import fetch_match
from app.xttv_parser import parse_match

html, *_ = fetch_match(462233)
parsed = parse_match(html, 462233)
players = parsed.get("players") or []
home = [p for p in players if p.get("side") == "home"]
away = [p for p in players if p.get("side") == "away"]
print("player_count:", parsed.get("player_count"), "has_walkover:", parsed.get("has_walkover"))
print("home players:", len(home))
for p in home:
    print("  H", p.get("position"), p.get("name"), p.get("external_player_id"))
print("away players:", len(away))
for p in away:
    print("  A", p.get("position"), p.get("name"), p.get("external_player_id"))
print("team_result:", parsed.get("team_result"))
print("w.o. in html:", "w.o." in html.lower())
