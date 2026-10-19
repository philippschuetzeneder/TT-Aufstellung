from app.rc_import import fetch_player_history, parse_player_history, import_rc_player_updates

rc_id = 55291
html, _ = fetch_player_history(rc_id)
parsed = parse_player_history(html)
print("parsed current:", parsed["current"])
recent = parsed["history"][-5:] if parsed["history"] else []
print("last history events:", recent)

result = import_rc_player_updates(
    rc_id,
    xttv_external_player_id="23754",
    xttv_name="Hahn Julian",
    xttv_club="Tragwein/Kamig 2",
)
print("import result:", result)
