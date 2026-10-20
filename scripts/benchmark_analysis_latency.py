"""Benchmark analyze_lineup for common opponent-selection counts (needs DATABASE_URL)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from app.analysis_service import MAX_ANALYSIS_SECONDS, analyze_lineup
from app.db import SessionLocal, create_all
from sqlalchemy import text

CASES = [
    {
        "label": "TRAK vs Allerheiligen — 0 Gegner",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": None,
    },
    {
        "label": "TRAK vs Allerheiligen — 1 Gegner (Karlinger)",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": ["24494"],
    },
    {
        "label": "TRAK vs Allerheiligen — 2 Gegner",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": ["24494", "17996"],
    },
    {
        "label": "TRAK vs Allerheiligen — 3 Gegner",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": ["24494", "17996", "17003"],
    },
    {
        "label": "TRAK vs Allerheiligen — 0 Gegner, Heim+Gast-Mittel",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": None,
        "own_is_home": None,
    },
    {
        "label": "TRAK vs Allerheiligen — 1 Gegner + Spieltyp",
        "own": ["23754", "23784", "21771", "22183"],
        "opponent_team": "Allerheiligen 1",
        "own_team": "Tragwein/Kamig 2",
        "actual": ["24494"],
        "use_spieltyp": True,
    },
]


def main() -> int:
    create_all()
    with SessionLocal() as db:
        if not db.execute(text("SELECT 1 FROM xttv_matches LIMIT 1")).scalar():
            print("No match data — import XTTV first.")
            return 1
    rows = []
    failed = 0
    for case in CASES:
        started = time.monotonic()
        try:
            result = analyze_lineup(
                case["own"],
                case["opponent_team"],
                actual_opponent_ids=case["actual"],
                own_is_home=case.get("own_is_home", True),
                own_team=case.get("own_team"),
                use_spieltyp=case.get("use_spieltyp", False),
            )
            elapsed = time.monotonic() - started
            recs = result.get("recommendations") or []
            ok = bool(recs) and bool(result.get("recommendation")) and elapsed <= MAX_ANALYSIS_SECONDS + 0.5
            if not recs:
                failed += 1
            rows.append({
                "case": case["label"],
                "elapsed_s": round(elapsed, 3),
                "recommendations": len(recs),
                "within_budget": elapsed <= MAX_ANALYSIS_SECONDS + 0.5,
                "phase": result.get("phase"),
                "warnings": len(result.get("warnings") or []),
            })
        except Exception as exc:
            failed += 1
            rows.append({"case": case["label"], "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"budget_s": MAX_ANALYSIS_SECONDS, "results": rows}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
