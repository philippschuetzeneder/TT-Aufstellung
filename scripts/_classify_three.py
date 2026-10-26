from app.xttv_db_import import fetch_match, _classify_meid

for meid in (461346, 467535, 468033):
    cls = _classify_meid(meid, check_db=False)
    print("---", meid, cls.get("status"))
    for key in ("parse_error", "season", "is_three_player", "reason", "importable"):
        if key in cls:
            print(key, cls[key])
    print({k: cls[k] for k in cls if k not in {"html"}})
