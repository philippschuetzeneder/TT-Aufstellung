"""Sunday preflight before Monday weekly refresh (RC reachability + stale RC count)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from app.data_refresh_service import check_rc_connectivity, external_ids_needing_rc_after_match
from app.db import create_all
from app.refresh_report_email import send_sunday_preflight_report
from app.xttv_db_import import get_target_seasons


def main() -> int:
    create_all()
    seasons = sorted(get_target_seasons())
    rc_ok, rc_detail = check_rc_connectivity()
    stale = external_ids_needing_rc_after_match()
    checks = {
        "ok": rc_ok,
        "target_seasons": seasons,
        "ratings_central": {"ok": rc_ok, "detail": rc_detail},
        "stale_rc_targets": len(stale),
        "message": (
            "Sonntags-Check OK — Montags-RC kann vom VPS laufen."
            if rc_ok
            else "Sonntags-Check FEHLER — Ratings Central vom VPS nicht erreichbar."
        ),
    }
    mail = send_sunday_preflight_report(checks)
    print(json.dumps({"checks": checks, "mail": mail}, ensure_ascii=False))
    return 0 if rc_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
