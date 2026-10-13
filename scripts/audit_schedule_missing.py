"""Classify schedule MEIDs that are still missing from DB."""
from __future__ import annotations

from collections import Counter

from app.xttv_db_import import _classify_meid, _is_imported
from app.xttv_schedule_import import collect_schedule_meids, sjids_for_scope


def main() -> None:
    for scope in ("current", "all"):
        print(f"\n=== scope={scope} ===", flush=True)
        disc = collect_schedule_meids(sjids=sjids_for_scope(scope))
        missing = [m for m in disc["meids"] if not _is_imported(m)]
        print(f"on_schedule={disc['meids_total']} missing={len(missing)}", flush=True)
        counts: Counter[str] = Counter()
        importable: list[int] = []
        for meid in missing[:500]:
            cls = _classify_meid(meid, check_db=False)
            status = cls.get("status") or "?"
            if cls.get("miss"):
                status = f"miss:{cls.get('reason')}"
            counts[status] += 1
            if cls.get("importable"):
                importable.append(meid)
        print("status_sample:", dict(counts.most_common(15)))
        print(f"importable_in_sample={len(importable)}", importable[:20])


if __name__ == "__main__":
    main()
