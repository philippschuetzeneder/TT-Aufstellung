from app.xttv_db_import import _is_valid_4_player_report, _quick_report_info
from app.xttv_parser import parse_match
from app.xttv_import import fetch_match

for meid in [437859, 437877, 437880, 437916, 437928]:
    html, status, _, _ = fetch_match(meid)
    quick = _quick_report_info(html)
    try:
        parsed = parse_match(html, meid)
        valid = _is_valid_4_player_report(parsed)
        print(f"\n=== meid {meid} ===")
        print("quick:", quick)
        print("valid:", valid)
        print(
            "player_count", parsed.get("player_count"),
            "singles", parsed.get("singles_count"),
            "doubles", parsed.get("doubles_count"),
        )
        print("league:", parsed.get("league"))
        print("home:", parsed.get("home_team"), "away:", parsed.get("away_team"))
        print("team_result:", parsed.get("team_result"))
    except Exception as exc:
        print(f"\n=== meid {meid} PARSE ERROR ===")
        print(type(exc).__name__, exc)
        print("quick:", quick)
