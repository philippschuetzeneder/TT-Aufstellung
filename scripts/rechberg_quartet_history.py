"""Historical lineup orders for Rechberg 2 quartet."""
from sqlalchemy import text
from app.db import SessionLocal, create_all

IDS = ["24260", "26054", "23505", "24383"]  # Ebenhofer, Steinbichler, Wilging, Waser
TEAM = "Rechberg 2"
create_all()

with SessionLocal() as db:
    lineup_key = ",".join(sorted(IDS))
    print("lineup_key:", lineup_key)
    rows = db.execute(
        text(
            "SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders "
            "WHERE lineup_key=:key ORDER BY appearances DESC"
        ),
        {"key": lineup_key},
    ).mappings()
    total = 0
    for r in rows:
        total += int(r["appearances"])
        names = []
        for col in ("p1", "p2", "p3", "p4"):
            n = db.execute(
                text("SELECT name FROM match_players WHERE external_player_id=:id LIMIT 1"),
                {"id": r[col]},
            ).scalar()
            names.append(n or r[col])
        print(f"  {r['appearances']}x  {' | '.join(names)}")
    print("total appearances in cache:", total)

    print("\nRaw matches Rechberg 2 with exactly these 4 players:")
    rows2 = db.execute(
        text(
            """
            SELECT m.id, m.match_date, m.home_team, m.away_team,
                   mp.external_player_id, mp.name, mp.position, mp.side
            FROM xttv_matches m
            JOIN match_players mp ON mp.match_id = m.id
            WHERE (m.home_team = :team AND mp.side = 'home')
               OR (m.away_team = :team AND mp.side = 'away')
            ORDER BY m.match_date DESC, m.id
            """
        ),
        {"team": TEAM},
    ).mappings()

    from collections import defaultdict

    matches = defaultdict(list)
    for r in rows2:
        matches[r["id"]].append(r)

    count_orders = {}
    match_list = []
    for mid, players in matches.items():
        ids = {str(p["external_player_id"]) for p in players if p["external_player_id"]}
        if ids != set(IDS):
            continue
        by_pos = {}
        for p in players:
            if p["external_player_id"] and p["position"] in ("A", "B", "C", "D", "1", "2", "3", "4"):
                by_pos[p["position"]] = (str(p["external_player_id"]), p["name"])
        if len(by_pos) < 4:
            continue
        # normalize to position index
        pos_map = {"A": 0, "1": 0, "B": 1, "2": 1, "C": 2, "3": 2, "D": 3, "4": 3}
        order = []
        for pos in ("A", "B", "C", "D"):
            alt = str(int(pos) - ord("A") + 1) if pos in ("A", "B", "C", "D") else pos
            entry = by_pos.get(pos) or by_pos.get(str(ord(pos) - ord("A") + 1))
            if entry:
                order.append(entry[1])
        key = tuple(order)
        count_orders[key] = count_orders.get(key, 0) + 1
        sample = players[0]
        match_list.append((sample["match_date"], sample["home_team"], sample["away_team"], order))

    print(f"Matches with exact quartet: {len(match_list)}")
    for date, home, away, order in match_list[:15]:
        print(f"  {date}  {home} vs {away}  -> {' | '.join(order)}")

    print("\nOrder frequency:")
    for order, c in sorted(count_orders.items(), key=lambda x: -x[1]):
        print(f"  {c}x  {' | '.join(order)}")
