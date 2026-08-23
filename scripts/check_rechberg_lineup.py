"""Check opponent lineup probability for Rechberg vs known quartet."""
import sys
from collections import Counter
from itertools import permutations

from sqlalchemy import text

from app.analysis_service import _known_quartet_lineup_scenarios, _load_analysis_data, _reference_date
from app.db import SessionLocal, create_all

create_all()

PLAYERS = {
    "Steinbichler Hansjörg": None,
    "Ebenhofer Alexander": None,
    "Wilging Philipp Michael": None,
    "Waser Thomas": None,
}


def find_player_ids(db):
    ids = {}
    for name in PLAYERS:
        row = db.execute(
            text(
                "SELECT external_player_id, name FROM match_players "
                "WHERE name ILIKE :pat ORDER BY id DESC LIMIT 1"
            ),
            {"pat": f"%{name.split()[0]}%"},
        ).first()
        if row:
            ids[name] = str(row[0])
            print(f"  {name}: {row[0]} ({row[1]})")
    return ids


def main():
    opponent_team = sys.argv[1] if len(sys.argv) > 1 else "Rechberg"
    with SessionLocal() as db:
        print("Opponent team:", opponent_team)
        print("Player lookup:")
        pid_map = find_player_ids(db)
        if len(pid_map) != 4:
            print("Could not resolve all 4 players")
            return
        actual = list(pid_map.values())
        ref = _reference_date(db)

        lineup_key = ",".join(sorted(actual))
        rows = list(
            db.execute(
                text(
                    "SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders "
                    "WHERE lineup_key=:key ORDER BY appearances DESC LIMIT 24"
                ),
                {"key": lineup_key},
            ).mappings()
        )
        print("\nCache analysis_lineup_orders for quartet:")
        total = sum(int(r["appearances"] or 0) for r in rows)
        for r in rows:
            p = (r["p1"], r["p2"], r["p3"], r["p4"])
            names = []
            for pid in p:
                n = db.execute(
                    text("SELECT name FROM match_players WHERE external_player_id=:id LIMIT 1"),
                    {"id": pid},
                ).scalar()
                names.append(n or pid)
            prob = int(r["appearances"]) / total if total else 0
            print(f"  {prob*100:.1f}%  {names}")

        scenarios, names = _known_quartet_lineup_scenarios(db, opponent_team, actual, ref)
        print("\nRaw quartet scenarios (_known_quartet_lineup_scenarios):")
        for prob, order in scenarios:
            label = [names.get(p, p) for p in order]
            print(f"  {prob*100:.1f}%  {label}")

        names2, profiles, matchups, scenarios2, source, ref_date, pool = _load_analysis_data(
            ["1", "2", "3", "4"], opponent_team, actual
        )
        print("\n_load_analysis_data source:", source)
        print("scenario count:", len(scenarios2))
        for prob, order in sorted(scenarios2, key=lambda x: -x[0])[:10]:
            label = [names2.get(p, p) for p in order]
            print(f"  {prob*100:.2f}%  {label}")


if __name__ == "__main__":
    main()
