from fastapi import APIRouter, Query

from app.rc_events import debug_event

router = APIRouter(prefix="/api/rc", tags=["rc"])


@router.get("/events/debug")
def debug_rc_event(event_id: int = Query(...)):
    return debug_event(event_id)
