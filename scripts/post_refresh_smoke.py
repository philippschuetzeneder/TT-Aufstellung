"""Fast post-refresh checks (DB only). Use after weekly-data-refresh-prod.sh."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import SessionLocal
from app.xttv_schedule_import import (
    collect_schedule_meids,
    count_missing_past_meids,
    schedule_import_candidates,
    sjids_for_scope,
)


def main() -> int:
    errors: list[str] = []
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT external_id FROM xttv_matches ORDER BY match_date DESC NULLS LAST LIMIT 1"),
        ).scalar()
        if not row:
            errors.append("Keine xttv_matches in DB")

    discovery = collect_schedule_meids(sjids=sjids_for_scope("current"))
    meid_dates = discovery.get("meid_dates") or {}
    imported = set()
    with SessionLocal() as db:
        for ext_id, in db.execute(text("SELECT external_id FROM xttv_matches")).all():
            if ext_id and str(ext_id).isdigit():
                imported.add(int(ext_id))
    missing = [m for m in discovery["meids"] if m not in imported]
    past_left = count_missing_past_meids(missing, meid_dates)
    future_left = len(missing) - len(schedule_import_candidates(missing, meid_dates))

    report = {
        "ok": past_left == 0,
        "missing_past_remaining": past_left,
        "missing_future_on_schedule": future_left,
        "missing_total_on_schedule": len(missing),
        "meids_on_schedules": discovery["meids_total"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if past_left:
        errors.append(f"{past_left} vergangene Spielplan-MEIDs fehlen noch in der DB")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
