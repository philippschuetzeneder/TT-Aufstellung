from app.xttv_db_import import _classify_meid

for meid in [437859, 437877, 437880, 437916, 437928]:
    c = _classify_meid(meid, check_db=False)
    print(meid, c)
