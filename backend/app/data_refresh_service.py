"""Manual Daten Refresh — one simple pipeline, no full RC reimport."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from sqlalchemy import bindparam, func, text

from .analysis_cache import refresh_analysis_cache
from .analysis_service import clear_analysis_runtime_caches
from .db import SessionLocal, create_all
from .models import PlayerRatingSnapshot, XttvPlayer
from .rc_import import fetch_player_history, import_rc_player, import_rc_player_updates
from .rc_matching import apply_matches_all
from .xttv_db_import import import_new_reports
from .xttv_schedule_import import drain_schedule_backlog, measure_schedule_import_gaps

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
def _refresh_not_before() -> date | None:
    raw = os.environ.get("DATA_REFRESH_NOT_BEFORE", "").strip()
    if not raw or raw.lower() in {"0", "false", "off", "none"}:
        return None
    return date.fromisoformat(raw)


def _refresh_paused() -> tuple[bool, str | None]:
    not_before = _refresh_not_before()
    if not_before is None:
        return False, None
    today = date.today()
    if today < not_before:
        return True, f"Sommerpause bis {not_before.isoformat()} (heute: {today.isoformat()})"
    return False, None


def match_linked_player_external_ids(*, seasons: frozenset[str] | None = None) -> list[str]:
    """Distinct Pass-Nr. from imported matches (default: rolling target seasons)."""
    from .xttv_db_import import get_target_seasons

    season_list = sorted(seasons or get_target_seasons())
    if not season_list:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT mp.external_player_id::text AS pass_id
                FROM match_players mp
                JOIN xttv_matches m ON m.id = mp.match_id
                WHERE mp.external_player_id IS NOT NULL
                  AND m.season IN :seasons
                ORDER BY pass_id
                """
            ).bindparams(bindparam("seasons", expanding=True)),
            {"seasons": season_list},
        ).scalars().all()
    return [str(x) for x in rows if x]


def external_ids_needing_rc_after_match(*, seasons: frozenset[str] | None = None) -> list[str]:
    """Mapped players whose latest RC snapshot is older than their latest imported match."""
    from .xttv_db_import import get_target_seasons

    season_list = sorted(seasons or get_target_seasons())
    if not season_list:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                WITH player_match AS (
                    SELECT
                        p.id AS player_id,
                        p.external_player_id::text AS external_player_id,
                        MAX(
                            to_timestamp(
                                (regexp_match(m.match_date, '(\\d{2}\\.\\d{2}\\.\\d{4})'))[1],
                                'DD.MM.YYYY'
                            )
                        ) AS last_match
                    FROM xttv_players p
                    JOIN match_players mp
                      ON mp.external_player_id::text = p.external_player_id
                    JOIN xttv_matches m ON m.id = mp.match_id
                    WHERE p.rc_player_id IS NOT NULL
                      AND m.season IN :seasons
                      AND m.match_date ~ '^\\d{2}\\.\\d{2}\\.\\d{4}'
                    GROUP BY p.id, p.external_player_id
                ),
                player_rc AS (
                    SELECT player_id, MAX(observed_at) AS last_rc
                    FROM player_rating_snapshots
                    WHERE source = 'ratingscentral'
                    GROUP BY player_id
                )
                SELECT pm.external_player_id
                FROM player_match pm
                LEFT JOIN player_rc pr ON pr.player_id = pm.player_id
                WHERE pr.last_rc IS NULL OR pr.last_rc < pm.last_match
                ORDER BY pm.external_player_id
                """
            ).bindparams(bindparam("seasons", expanding=True)),
            {"seasons": season_list},
        ).scalars().all()
    return [str(x) for x in rows if x]


def external_ids_from_imported_meids(meids: list[int | str]) -> list[str]:
    """All lineup players from newly imported XTTV match reports."""
    ids = [str(m) for m in meids if m]
    if not ids:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT mp.external_player_id::text
                FROM match_players mp
                JOIN xttv_matches m ON m.id = mp.match_id
                WHERE m.external_id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).scalars().all()
    return sorted({str(x) for x in rows if x})


def external_ids_without_rc_mapping(external_ids: list[str]) -> list[str]:
    """Pass-Nr. in scope with no RC player mapping yet."""
    ids = sorted({str(x) for x in external_ids if x})
    if not ids:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT external_player_id::text
                FROM xttv_players
                WHERE external_player_id::text IN :ids
                  AND rc_player_id IS NULL
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).scalars().all()
    return sorted({str(x) for x in rows if x})


def check_rc_connectivity(*, probe_player_id: int = 55291) -> tuple[bool, str]:
    """Quick Ratings Central reachability check from this runtime (VPS / container)."""
    try:
        html, _ = fetch_player_history(int(probe_player_id))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if len(html) < 500:
        return False, f"response too short ({len(html)} bytes)"
    return True, "ok"


def _import_rc_for_players(external_ids: list[str]) -> dict:
    """Update RC for players in new XTTV reports — incremental when history already exists."""
    external_ids = sorted({str(x) for x in external_ids if x})
    if not external_ids:
        return {"imported": 0, "errors": 0, "targets": 0, "full_imports": 0, "incremental_updates": 0}
    with SessionLocal() as session:
        players = (
            session.query(XttvPlayer)
            .filter(
                XttvPlayer.external_player_id.in_(external_ids),
                XttvPlayer.rc_player_id.isnot(None),
            )
            .all()
        )
        snapshot_counts = {
            row.player_id: row.cnt
            for row in session.query(
                PlayerRatingSnapshot.player_id,
                func.count(PlayerRatingSnapshot.id).label("cnt"),
            )
            .filter(
                PlayerRatingSnapshot.source == "ratingscentral",
                PlayerRatingSnapshot.player_id.in_([p.id for p in players]),
            )
            .group_by(PlayerRatingSnapshot.player_id)
        }
    jobs = [
        {
            "external_player_id": str(player.external_player_id),
            "rc_player_id": int(player.rc_player_id),
            "name": player.name,
            "club": player.club,
            "incremental": snapshot_counts.get(player.id, 0) > 0,
        }
        for player in players
    ]

    def _run_one(job: dict) -> tuple[bool, str | None]:
        try:
            importer = import_rc_player_updates if job["incremental"] else import_rc_player
            result = importer(
                job["rc_player_id"],
                xttv_external_player_id=job["external_player_id"],
                xttv_name=job["name"],
                xttv_club=job["club"],
            )
            mode = result.get("mode")
            return True, mode if mode in {"incremental", "full"} else "full"
        except Exception as exc:
            logger.warning("RC import %s: %s", job["external_player_id"], exc)
            return False, None

    imported = errors = full_imports = incremental_updates = 0
    workers = max(1, min(int(os.environ.get("RC_IMPORT_WORKERS", "1") or "1"), 8))
    if workers == 1:
        outcomes = [_run_one(job) for job in jobs]
    else:
        outcomes = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, job) for job in jobs]
            for fut in as_completed(futures):
                outcomes.append(fut.result())

    for ok, mode in outcomes:
        if ok:
            imported += 1
            if mode == "incremental":
                incremental_updates += 1
            else:
                full_imports += 1
        else:
            errors += 1
    return {
        "imported": imported,
        "errors": errors,
        "targets": len(players),
        "full_imports": full_imports,
        "incremental_updates": incremental_updates,
    }


def _schedule_scope() -> str:
    raw = os.environ.get("DATA_REFRESH_SCHEDULE_SCOPE", "current").strip().lower()
    return raw if raw in {"all", "current"} else "current"


def _forward_import_limit() -> int:
    """0 / unset = no cap on forward MEID scan during weekly refresh."""
    raw = os.environ.get("DATA_REFRESH_FORWARD_LIMIT", "0").strip().lower()
    if raw in {"", "0", "all", "unlimited", "none"}:
        return 0
    return max(int(raw), 1) if raw.isdigit() else 0


def run_data_refresh(*, restart_server: bool = False) -> dict:
    """Schedule gaps (current season by default) + forward MEID scan; RC if needed."""
    paused, pause_reason = _refresh_paused()
    if paused:
        return {
            "ok": True,
            "skipped": True,
            "data_changed": False,
            "reason": pause_reason,
            "message": pause_reason,
        }

    create_all()
    started = time.monotonic()
    summary: dict = {}
    missing_remaining = 0
    missing_past_remaining = 0
    schedule: dict = {}
    forward: dict = {}
    new_meids: list = []
    data_changed = False

    try:
        schedule_scope = _schedule_scope()
        schedule = drain_schedule_backlog(schedule_scope=schedule_scope)
        discovery = schedule.get("discovery") or {}
        missing_remaining = int(discovery.get("missing_remaining") or 0)
        missing_past_remaining = int(discovery.get("missing_past_remaining") or 0)

        if missing_past_remaining > 0:
            forward = {
                "ok": True,
                "skipped": True,
                "reason": "schedule_past_backlog",
                "missing_remaining": missing_remaining,
                "missing_past_remaining": missing_past_remaining,
                "imported": 0,
                "imported_meids": [],
            }
        else:
            forward = import_new_reports(limit=_forward_import_limit())

        imported_meids = list(schedule.get("imported_meids") or []) + list(
            forward.get("imported_meids") or []
        )
        xttv = {
            "imported": len(imported_meids),
            "imported_meids": imported_meids,
            "schedule": schedule,
            "forward": forward,
        }
        summary["xttv"] = {
            "imported": xttv["imported"],
            "imported_meids": imported_meids,
            "schedule_imported": schedule.get("imported", 0),
            "schedule_rounds": schedule.get("rounds", 0),
            "forward_imported": forward.get("imported", 0),
            "forward_skipped": bool(forward.get("skipped")),
            "checked": int(schedule.get("checked") or 0) + int(forward.get("checked") or 0),
            "errors": int(schedule.get("errors") or 0) + int(forward.get("errors") or 0),
            "last_known_meid": forward.get("last_known_meid"),
            "max_imported_meid": forward.get("max_imported_meid"),
            "range": forward.get("range"),
            "discovery": discovery,
            "schedule_scope": schedule_scope,
            "missing_remaining": missing_remaining,
            "missing_past_remaining": missing_past_remaining,
            "stopped_after_empty_streak": bool(forward.get("stopped_after_empty_streak")),
            "scan_frontier_after": forward.get("scan_frontier_after"),
        }

        new_meids = imported_meids
        rc_mapped = 0
        rc_history = {"imported": 0, "errors": 0, "targets": 0}
        rc_ok, rc_note = check_rc_connectivity()
        summary["rc_preflight"] = {"ok": rc_ok, "detail": rc_note}
        match_result = apply_matches_all(batch_size=200, import_history=True, only_unmapped=True)
        rc_mapped = int(match_result.get("applied", 0) or 0)
        if new_meids:
            extra_map = apply_matches_all(batch_size=200, import_history=True, only_unmapped=True)
            rc_mapped += int(extra_map.get("applied", 0) or 0)
        stale_after_match = external_ids_needing_rc_after_match()
        from_new_reports = external_ids_from_imported_meids(new_meids)
        player_ids = sorted(set(stale_after_match) | set(from_new_reports))
        summary["rc_plan"] = {
            "stale_after_match": len(stale_after_match),
            "from_new_reports": len(from_new_reports),
            "new_meids_this_run": len(new_meids),
            "targets_union": len(player_ids),
        }
        if not rc_ok:
            proxy = os.environ.get("RC_HTTPS_PROXY", "").strip()
            hint = (
                "Ratings Central von diesem Host nicht erreichbar. "
                "RC_HTTPS_PROXY in .env setzen oder später erneut versuchen."
            )
            if proxy:
                hint += f" (RC_HTTPS_PROXY={proxy!r} ist gesetzt — prüfen ob Proxy läuft.)"
            summary["rc"] = {
                "skipped": True,
                "reason": "connectivity",
                "detail": rc_note,
                "hint": hint,
                "newly_mapped": rc_mapped,
                "stale_after_match": len(stale_after_match),
                "targets_planned": len(player_ids),
            }
        elif not player_ids:
            summary["rc"] = {
                "skipped": True,
                "reason": "no_stale_rc",
                "newly_mapped": rc_mapped,
                "stale_after_match": 0,
            }
        else:
            rc_history = _import_rc_for_players(player_ids)
            if int(rc_history.get("errors") or 0) > 0:
                retry_ids = sorted(
                    set(external_ids_needing_rc_after_match()) | set(from_new_reports),
                )
                if retry_ids:
                    retry = _import_rc_for_players(retry_ids)
                    rc_history["imported"] = int(rc_history.get("imported") or 0) + int(
                        retry.get("imported") or 0,
                    )
                    rc_history["errors"] = int(retry.get("errors") or 0)
                    rc_history["targets"] = max(
                        int(rc_history.get("targets") or 0),
                        int(retry.get("targets") or 0),
                    )
            summary["rc"] = {
                "newly_mapped": rc_mapped,
                "stale_after_match": len(stale_after_match),
                **rc_history,
            }

        gap_final = measure_schedule_import_gaps(schedule_scope=schedule_scope)
        missing_remaining = int(gap_final.get("missing_remaining") or 0)
        missing_past_remaining = int(gap_final.get("missing_past_remaining") or 0)
        summary["xttv"]["discovery_final"] = gap_final
        summary["xttv"]["missing_remaining"] = missing_remaining
        summary["xttv"]["missing_past_remaining"] = missing_past_remaining

        lineup_scope = sorted(
            set(from_new_reports) | set(external_ids_needing_rc_after_match()),
        )
        unmapped_lineup = external_ids_without_rc_mapping(lineup_scope)
        stale_remaining = external_ids_needing_rc_after_match()
        summary["rc_verify"] = {
            "stale_after_match_remaining": len(stale_remaining),
            "unmapped_lineup_players": len(unmapped_lineup),
        }

        data_changed = bool(new_meids) or rc_history.get("imported", 0) > 0 or rc_mapped > 0
        if data_changed:
            clear_analysis_runtime_caches()
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

    rc_block = summary.get("rc") or {}
    rc_verify = summary.get("rc_verify") or {}
    rc_connectivity_skip = bool(rc_block.get("skipped")) and rc_block.get("reason") == "connectivity"
    rc_errors = int(rc_block.get("errors") or 0)
    rc_stale_left = int(rc_verify.get("stale_after_match_remaining") or 0)
    rc_unmapped = int(rc_verify.get("unmapped_lineup_players") or 0)

    schedule_complete = missing_past_remaining == 0
    rc_complete = (
        not rc_connectivity_skip
        and rc_errors == 0
        and rc_stale_left == 0
        and rc_unmapped == 0
    )
    result = {
        "ok": schedule_complete and rc_complete,
        "data_changed": data_changed,
        "schedule_complete": schedule_complete,
        "rc_complete": rc_complete,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "summary": summary,
        "message": (
            f"{xttv['imported']} neue Spielberichte importiert "
            f"(Spielplan-Drain: {schedule.get('imported', 0)} in {schedule.get('rounds', 0)} Runden, "
            f"Forward: {forward.get('imported', 0)})."
            if new_meids
            else "Keine neuen Spielberichte gefunden."
        ),
    }
    warnings: list[str] = []
    if not schedule_complete:
        warnings.append(
            f"Spielplan unvollständig: {missing_past_remaining} vergangene Spiele mit MEID "
            f"fehlen noch in der DB (gesamt offen: {missing_remaining}).",
        )
    if rc_connectivity_skip:
        warnings.append("RC nicht erreichbar — keine RC-Updates in diesem Lauf.")
    elif rc_errors:
        warnings.append(f"RC-Import: {rc_errors} Fehler.")
    elif rc_stale_left:
        warnings.append(f"RC noch veraltet für {rc_stale_left} Spieler nach dem Lauf.")
    elif rc_unmapped:
        warnings.append(f"{rc_unmapped} Spieler in Aufstellungen ohne RC-Zuordnung.")
    if warnings:
        result["warning"] = " ".join(warnings)
        if not new_meids:
            result["message"] = result["warning"]

    if restart_server and data_changed:
        result["restart_scheduled"] = True
        threading.Thread(target=_schedule_server_restart, daemon=True).start()
    elif restart_server:
        result["restart_scheduled"] = False
        result["restart_note"] = "Kein Neustart — nichts Neues importiert."

    return result


def _schedule_server_restart() -> None:
    time.sleep(1.5)
    custom = os.environ.get("DATA_REFRESH_RESTART_CMD", "").strip()
    if custom:
        try:
            subprocess.Popen(custom, shell=True, cwd=str(ROOT))
        except Exception as exc:
            logger.exception("restart failed: %s", exc)
        return

    if sys.platform == "win32":
        script = ROOT / "scripts" / "restart-dev.ps1"
        if not script.is_file():
            return
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                cwd=str(ROOT),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as exc:
            logger.exception("restart failed: %s", exc)
        return

    prod_script = ROOT / "scripts" / "restart-prod.sh"
    if prod_script.is_file():
        try:
            subprocess.Popen(["/bin/bash", str(prod_script)], cwd=str(ROOT))
        except Exception as exc:
            logger.exception("restart failed: %s", exc)
