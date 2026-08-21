from __future__ import annotations

import re
from sqlalchemy import text

from .db import SessionLocal
from .models import XttvPlayer

SPIELTYP_OFFENSIVE = "offensive"
SPIELTYP_PIPS = "pips"
SPIELTYP_DEFENSIVE = "defensive"

SPIELTYP_LABELS = {
    SPIELTYP_OFFENSIVE: "Offensivspieler",
    SPIELTYP_PIPS: "Noppenspieler",
    SPIELTYP_DEFENSIVE: "Defensivspieler",
}

SPIELTYP_ALIASES = {
    "o": SPIELTYP_OFFENSIVE,
    "off": SPIELTYP_OFFENSIVE,
    "offensive": SPIELTYP_OFFENSIVE,
    "offensiv": SPIELTYP_OFFENSIVE,
    "offensivspieler": SPIELTYP_OFFENSIVE,
    "n": SPIELTYP_PIPS,
    "nop": SPIELTYP_PIPS,
    "noppen": SPIELTYP_PIPS,
    "noppenspieler": SPIELTYP_PIPS,
    "pips": SPIELTYP_PIPS,
    "d": SPIELTYP_DEFENSIVE,
    "def": SPIELTYP_DEFENSIVE,
    "defensive": SPIELTYP_DEFENSIVE,
    "defensiv": SPIELTYP_DEFENSIVE,
    "defensivspieler": SPIELTYP_DEFENSIVE,
    "-": None,
    "": None,
    "none": None,
    "kein": None,
    "leer": None,
    "x": None,
}


def normalize_spieltyp(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = re.sub(r"[^a-z0-9]+", "", (raw or "").strip().lower())
    if not key:
        return None
    if key in SPIELTYP_ALIASES:
        return SPIELTYP_ALIASES[key]
    if key[:1] in SPIELTYP_ALIASES:
        return SPIELTYP_ALIASES[key[:1]]
    if key[:3] in SPIELTYP_ALIASES:
        return SPIELTYP_ALIASES[key[:3]]
    return None


def parse_bulk_lines(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "|" in stripped:
            parts = [p.strip() for p in stripped.split("|")]
            if len(parts) >= 2 and parts[0].isdigit():
                pass_id, raw_type = parts[0], parts[1]
            else:
                continue
        else:
            match = re.match(r"^(\d{4,6})\s+(.+)$", stripped)
            if not match:
                continue
            pass_id, raw_type = match.group(1), match.group(2)
        rows.append({"pass_id": pass_id, "spieltyp": normalize_spieltyp(raw_type), "raw": raw_type.strip()})
    return rows


def apply_spieltyp_rows(rows: list[dict]) -> dict:
    applied = 0
    cleared = 0
    missing: list[str] = []
    invalid: list[dict] = []
    with SessionLocal() as session:
        for row in rows:
            pass_id = str(row.get("pass_id") or "").strip()
            if not pass_id:
                continue
            spieltyp = row.get("spieltyp")
            if spieltyp is None and row.get("raw"):
                invalid.append({"pass_id": pass_id, "raw": row.get("raw")})
                continue
            player = session.query(XttvPlayer).filter_by(external_player_id=pass_id).one_or_none()
            if player is None:
                missing.append(pass_id)
                continue
            player.spieltyp = spieltyp
            if spieltyp:
                applied += 1
            else:
                cleared += 1
        session.commit()
    return {
        "ok": True,
        "applied": applied,
        "cleared": cleared,
        "missing_pass_ids": missing,
        "invalid": invalid,
        "total_rows": len(rows),
    }


def bulk_import_text(text: str) -> dict:
    rows = parse_bulk_lines(text)
    result = apply_spieltyp_rows(rows)
    result["parsed_rows"] = len(rows)
    return result


def list_spieltyp(pass_ids: list[str] | None = None) -> dict:
    with SessionLocal() as session:
        query = session.query(XttvPlayer).order_by(XttvPlayer.name)
        if pass_ids:
            query = query.filter(XttvPlayer.external_player_id.in_([str(x) for x in pass_ids]))
        players = [
            {
                "pass_id": p.external_player_id,
                "name": p.name,
                "club": p.club,
                "spieltyp": p.spieltyp,
                "spieltyp_label": SPIELTYP_LABELS.get(p.spieltyp) if p.spieltyp else None,
            }
            for p in query.all()
            if p.spieltyp or not pass_ids
        ]
    return {"ok": True, "players": players, "count": len(players)}


def spieltyp_map_for_ids(external_ids: list[str]) -> dict[str, str | None]:
    ids = [str(x) for x in external_ids]
    if not ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT external_player_id::text AS pass_id, spieltyp "
                "FROM xttv_players WHERE external_player_id::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).mappings()
        return {str(r["pass_id"]): r["spieltyp"] for r in rows}
