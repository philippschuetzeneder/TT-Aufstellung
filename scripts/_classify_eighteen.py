from app.xttv_db_import import _classify_meid

MEIDS = [
    468033, 468064, 467535, 467536, 467537,
    469117, 469118, 469119, 469120, 469121, 469122, 469125, 469126, 469127, 469128,
    461695, 468650, 461346,
]
for meid in MEIDS:
    cls = _classify_meid(meid, check_db=False)
    print(
        meid,
        cls.get("status"),
        "season=", cls.get("season"),
        "three=", cls.get("three_player"),
        "filter=", cls.get("filter_reason"),
        "parse=", cls.get("parse_error"),
        "invalid=", cls.get("invalid_reason"),
        "teams=", cls.get("home_team"), cls.get("away_team"),
    )
