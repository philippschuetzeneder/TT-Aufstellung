"""Manual Daten Refresh — one simple pipeline, no full RC reimport."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from .analysis_cache import refresh_analysis_cache
from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_import import import_rc_player
from .rc_matching import apply_matches_all
from .xttv_db_import import import_new_reports, player_ids_from_meids

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def _import_rc_for_players(external_ids: list[str]) -> dict:
    external_ids = sorted({str(x) for x in external_ids if x})
    if not external_ids:
        return {"imported": 0, "errors": 0, "targets": 0}
    with SessionLocal() as session:
        players = (
            session.query(XttvPlayer)
            .filter(
                XttvPlayer.external_player_id.in_(external_ids),
                XttvPlayer.rc_player_id.isnot(None),
            )
            .all()
        )
    imported = errors = 0
    for player in players:
        try:
            import_rc_player(
                int(player.rc_player_id),
                xttv_external_player_id=str(player.external_player_id),
                xttv_name=player.name,
                xttv_club=player.club,
            )
            imported += 1
        except Exception as exc:
            errors += 1
            logger.warning("RC import %s: %s", player.external_player_id, exc)
    return {"imported": imported, "errors": errors, "targets": len(players)}


def run_data_refresh(*, restart_server: bool = False) -> dict:
    """Find new XTTV reports forward from max MEID; update RC only if needed."""
    create_all()
    started = time.monotonic()
    summary: dict = {}

    try:
        xttv = import_new_reports()
        summary["xttv"] = {
            "imported": xttv["imported"],
            "checked": xttv["checked"],
            "errors": xttv.get("errors", 0),
            "last_known_meid": xttv["last_known_meid"],
            "max_imported_meid": xttv.get("max_imported_meid"),
            "range": xttv["range"],
            "imported_meids": xttv.get("imported_meids") or [],
            "stopped_after_empty_streak": bool(xttv.get("stopped_after_empty_streak")),
            "scan_frontier_after": xttv.get("scan_frontier_after"),
        }

        new_meids = xttv.get("imported_meids") or []
        player_ids = player_ids_from_meids(new_meids) if new_meids else []

        rc_mapped = 0
        rc_history = {"imported": 0, "errors": 0, "targets": 0}
        if player_ids:
            match_result = apply_matches_all(batch_size=200, import_history=True, only_unmapped=True)
            rc_mapped = int(match_result.get("applied", 0) or 0)
            rc_history = _import_rc_for_players(player_ids)
            summary["rc"] = {"newly_mapped": rc_mapped, **rc_history}
        else:
            summary["rc"] = {"skipped": True, "reason": "no_new_reports"}

        data_changed = bool(new_meids) or rc_history.get("imported", 0) > 0 or rc_mapped > 0
        if data_changed:
            cache = refresh_analysis_cache()
            summary["analysis_cache"] = {"ok": cache.get("ok", True)}
        else:
            summary["analysis_cache"] = {"skipped": True}

    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "summary": summary,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }

    result = {
        "ok": True,
        "data_changed": data_changed,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "summary": summary,
        "message": (
            f"{xttv['imported']} neue Spielberichte importiert."
            if new_meids
            else "Keine neuen Spielberichte gefunden."
        ),
    }

    if restart_server and data_changed:
        result["restart_scheduled"] = True
        threading.Thread(target=_schedule_server_restart, daemon=True).start()
    elif restart_server:
        result["restart_scheduled"] = False
        result["restart_note"] = "Kein Neustart — nichts Neues importiert."

    return result


def _schedule_server_restart() -> None:
    time.sleep(1.5)
    script = ROOT / "scripts" / "restart-dev.ps1"
    if not script.is_file():
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        logger.exception("restart failed: %s", exc)
