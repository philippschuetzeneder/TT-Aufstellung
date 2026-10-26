"""List schedule MEIDs with a past date that are still missing in the DB, and why import skipped them."""
from __future__ import annotations

import json

from app.xttv_db_import import _classify_meid, _is_imported
from app.xttv_schedule_import import (
    collect_schedule_meids,
    count_missing_past_meids,
    schedule_import_candidates,
    sjids_for_scope,
)

discovery = collect_schedule_meids(sjids=sjids_for_scope("current"))
meid_dates = discovery.get("meid_dates") or {}
missing = [m for m in discovery["meids"] if not _is_imported(m)]
past = [
    m
    for m in schedule_import_candidates(missing, meid_dates)
    if meid_dates.get(m) is not None
]
undated = [m for m in missing if meid_dates.get(m) is None]
rows = []
for meid in sorted(past, key=lambda m: meid_dates[m]):
    cls = _classify_meid(meid, check_db=False)
    rows.append(
        {
            "meid": meid,
            "date": meid_dates[meid].isoformat(),
            "status": cls.get("status"),
            "reason": cls.get("reason"),
            "importable": bool(cls.get("importable")),
        }
    )

print(
    json.dumps(
        {
            "missing_total": len(missing),
            "missing_past_dated": count_missing_past_meids(missing, meid_dates),
            "missing_undated": len(undated),
            "rows": rows,
        },
        ensure_ascii=False,
        indent=2,
    )
)
