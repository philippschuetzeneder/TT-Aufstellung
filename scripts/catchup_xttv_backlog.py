"""One-off: drain all schedule + forward MEID backlog, then RC + analysis cache."""
from __future__ import annotations

import json
import sys
import time

from app.analysis_cache import refresh_analysis_cache
from app.analysis_service import clear_analysis_runtime_caches
from app.data_refresh_service import _import_rc_for_players
from app.db import create_all
from app.rc_matching import apply_matches_all
from app.xttv_db_import import MAX_IMPORT, import_new_reports, player_ids_from_meids
from app.xttv_schedule_import import drain_schedule_backlog


def main() -> int:
    create_all()
    started = time.monotonic()
    out: dict = {"steps": []}

    print(
        "[catchup] Phase 1: Spielplan-Lücken (alle Ziel-Saisons). "
        "Dauert ~15–25 Min — Fortschritt erscheint als [xttv-schedule] …",
        flush=True,
    )
    schedule = drain_schedule_backlog(schedule_scope="all", batch_size=MAX_IMPORT, max_rounds=100)
    out["schedule"] = schedule
    print(json.dumps(schedule, ensure_ascii=False, default=str), flush=True)

    forward_meids: list[int] = []
    forward_rounds = 0
    while forward_rounds < 30:
        forward_rounds += 1
        fwd = import_new_reports(limit=MAX_IMPORT)
        out["steps"].append({"forward_round": forward_rounds, "imported": fwd.get("imported")})
        print(f"[catchup] forward round {forward_rounds}: imported={fwd.get('imported')}", flush=True)
        ids = fwd.get("imported_meids") or []
        if not ids:
            break
        forward_meids.extend(ids)

    all_meids = list(schedule.get("imported_meids") or []) + forward_meids
    out["total_imported"] = len(all_meids)
    out["missing_remaining"] = schedule.get("discovery", {}).get("missing_remaining")

    rc_summary = {"skipped": True}
    if all_meids:
        print(f"[catchup] RC + cache for {len(all_meids)} new reports...", flush=True)
        match_result = apply_matches_all(batch_size=200, import_history=True, only_unmapped=True)
        player_ids = player_ids_from_meids(all_meids)
        rc_summary = {
            "newly_mapped": int(match_result.get("applied") or 0),
            **_import_rc_for_players(player_ids),
        }
        clear_analysis_runtime_caches()
        cache = refresh_analysis_cache()
        rc_summary["analysis_cache_ok"] = cache.get("ok", True)

    out["rc"] = rc_summary
    out["elapsed_seconds"] = round(time.monotonic() - started, 1)
    print("[catchup] done", json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return 0 if out.get("missing_remaining", 0) == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
