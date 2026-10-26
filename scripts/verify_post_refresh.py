"""Verify schedule backlog and key MEIDs after weekly refresh (prod/local)."""
from __future__ import annotations

import json
import sys

from sqlalchemy import text

from app.db import SessionLocal
from app.xttv_schedule_import import collect_schedule_meids, count_missing_past_meids, sjids_for_scope


def main() -> int:
    discovery = collect_schedule_meids(sjids=sjids_for_scope("current"))
    meid_dates = discovery.get("meid_dates") or {}
    with SessionLocal() as db:
        imported = {
            int(row[0])
            for row in db.execute(text("SELECT external_id FROM xttv_matches")).fetchall()
            if str(row[0]).isdigit()
        }
    missing = [m for m in discovery["meids"] if m not in imported]
    missing_past = count_missing_past_meids(missing, meid_dates)
    checks = {
        "meids_on_schedules": discovery["meids_total"],
        "missing_total": len(missing),
        "missing_past": missing_past,
        "meid_462244_imported": 462244 in imported,
        "trak_421_matches": [],
    }
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT external_id, match_date, home_team, away_team
                FROM xttv_matches
                WHERE season = '2026/2027'
                  AND league ILIKE '%421%'
                  AND (home_team ILIKE '%Tragwein%' OR away_team ILIKE '%Tragwein%')
                ORDER BY match_date DESC
                LIMIT 10
                """
            ),
        ).mappings().all()
        checks["trak_421_matches"] = [dict(r) for r in rows]
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if missing_past > 0 or not checks["meid_462244_imported"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
