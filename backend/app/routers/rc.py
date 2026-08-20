from fastapi import APIRouter, Query

from app.rc_events import debug_event
from app.rc_index import import_index
from app.rc_matching import dry_run

router = APIRouter(tags=["rc"])


@router.get("/events/debug")
def debug_rc_event(event_id: int = Query(...)):
    return debug_event(event_id)


@router.get("/dry-run")
def rc_dry_run(
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return dry_run(limit=limit, offset=offset)


@router.get("/index")
def rc_index(
    limit: int = Query(30, ge=1, le=500),
    offset: int = Query(0, ge=0),
    force: bool = Query(False),
):
    return import_index(limit=limit, offset=offset, force=force)
