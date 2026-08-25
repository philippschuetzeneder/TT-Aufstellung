"""Minimal admin-token gate for write and heavy API routes."""

from __future__ import annotations

import hmac
import os

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

# Schreibende / importierende Endpunkte.
ADMIN_WRITE_PATHS = frozenset({
    "/api/data/refresh",
    "/api/spieltyp/bulk",
    "/api/xttv/import",
    "/api/xttv/scan-import",
    "/api/rc/import",
    "/api/rc/match-apply",
    "/api/rc/match-apply-all",
    "/api/rc/index/import",
    "/api/rc/sync-ratings-from-index",
    "/api/rc/bulk",
    "/api/analysis/cache-refresh",
})

# Lesende Endpunkte mit hohem DB-/Netzwerkaufwand (nicht Kern-UI).
ADMIN_HEAVY_READ_PATHS = frozenset({
    "/api/db/validate",
    "/api/analytics/validate",
    "/api/rc/match-dry-run",
    "/api/rc/match-dry-run-all",
    "/api/xttv/debug",
    "/api/xttv/fetch",
    "/api/xttv/inspect",
    "/api/xttv/parse",
    "/api/rc/debug-history",
    "/api/rc/check",
    "/api/rc/events/debug",
    "/api/rc/index/debug-search",
})


def admin_required() -> bool:
    """True when the server expects a valid admin token on protected routes."""
    return bool(ADMIN_TOKEN)


def path_requires_admin(path: str) -> bool:
    return path in ADMIN_WRITE_PATHS or path in ADMIN_HEAVY_READ_PATHS


def extract_admin_token(headers) -> str:
    auth = headers.get("Authorization", "") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("X-Admin-Token") or "").strip()


def token_is_valid(token: str) -> bool:
    if not ADMIN_TOKEN:
        return True
    if not token:
        return False
    return hmac.compare_digest(token, ADMIN_TOKEN)
