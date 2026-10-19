"""Preflight: can this host reach ratingscentral.com PlayerHistory? Exit 0/1."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from app.rc_import import fetch_player_history

DEFAULT_PROBE_RC_ID = 55291


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Ratings Central reachability from this host")
    parser.add_argument("--player-id", type=int, default=DEFAULT_PROBE_RC_ID)
    args = parser.parse_args()
    try:
        html, _ = fetch_player_history(int(args.player_id))
    except Exception as exc:
        print(f"RC connectivity FAIL: {type(exc).__name__}: {exc}")
        return 1
    if len(html) < 500:
        print(f"RC connectivity FAIL: response too short ({len(html)} bytes)")
        return 1
    print(f"RC connectivity OK (PlayerID={args.player_id}, bytes={len(html)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
