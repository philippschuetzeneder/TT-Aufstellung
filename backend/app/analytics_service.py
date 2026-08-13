from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import re

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, XttvMatch


def _score(result: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _player_key(player: MatchPlayer) -> str:
    return str(player.external_player_id or f"name:{player.name}")


def _load_matches(db):
    return (
        db.query(XttvMatch)
        .options(selectinload(XttvMatch.players), selectinload(XttvMatch.games))
        .order_by(XttvMatch.match_date, XttvMatch.id)
        .all()
    )


def player_stats() -> dict:
    """Aggregate player master data and historical performance from imported matches."""
    create_all()
    db = SessionLocal()
    try:
        stats = {}
        for match in _load_matches(db):
            for p in match.players:
                key = _player_key(p)
                s = stats.setdefault(key, {
                    "external_player_id": p.external_player_id,
                    "name": p.name,
                    "matches": 0,
                    "singles": 0,
                    "singles_wins": 0,
                    "singles_losses": 0,
                    "singles_win_rate": None,
                    "team_appearances": Counter(),
                    "positions": Counter(),
                })
                s["matches"] += 1
                if p.position:
                    s["positions"][p.position] += 1
                team = match.home_team if p.side == "home" else match.away_team
                if team:
                    s["team_appearances"][team] += 1
            player_by_position = {(p.side, p.position): p for p in match.players}
            for g in match.games:
                if g.game_type != "singles":
                    continue
                sc = _score(g.result)
                if not sc:
                    continue
                for side, pos, won in (("home", g.home_position, sc[0] > sc[1]), ("away", g.away_position, sc[1] > sc[0])):
                    p = player_by_position.get((side, pos))
                    if not p:
                        continue
                    s = stats[_player_key(p)]
                    s["singles"] += 1
                    s["singles_wins"] += int(won)
                    s["singles_losses"] += int(not won)
        out = []
        for s in stats.values():
            s["singles_win_rate"] = round(s["singles_wins"] / s["singles"], 4) if s["singles"] else None
            s["team_appearances"] = dict(s["team_appearances"])
            s["positions"] = dict(s["positions"])
            out.append(s)
        out.sort(key=lambda x: (-x["singles"], x["name"]))
        return {"ok": True, "players": out, "count": len(out)}
    finally:
        db.close()


def lineup_stats(team: str | None = None) -> dict:
    """Return historical four-player lineups, player position frequencies and co-occurrence."""
    create_all()
    db = SessionLocal()
    try:
        lineup_counts = Counter()
        position_counts = Counter()
        cooccurrence = Counter()
        appearances = Counter()
        matches = 0
        for match in _load_matches(db):
            for side, team_name in (("home", match.home_team), ("away", match.away_team)):
                if team and team_name != team:
                    continue
                players = [p for p in match.players if p.side == side]
                if not players:
                    continue
                matches += 1
                keys = tuple(sorted(_player_key(p) for p in players))
                lineup_counts[keys] += 1
                for p in players:
                    key = _player_key(p)
                    appearances[key] += 1
                    position_counts[(key, p.position)] += 1
                for pair in combinations(keys, 2):
                    cooccurrence[pair] += 1
        lineups = [{"players": list(k), "count": v, "probability": round(v / matches, 4) if matches else None} for k, v in lineup_counts.most_common()]
        positions = [{"player": k[0], "position": k[1], "count": v, "probability": round(v / appearances[k[0]], 4) if appearances[k[0]] else None} for k, v in position_counts.items()]
        pairs = [{"players": list(k), "count": v, "probability": round(v / matches, 4) if matches else None} for k, v in cooccurrence.most_common()]
        return {"ok": True, "team": team, "matches": matches, "lineups": lineups, "positions": positions, "cooccurrence": pairs}
    finally:
        db.close()


def matchup_stats(player_id: str | None = None, opponent_id: str | None = None) -> dict:
    """Aggregate historical singles matchups. IDs are XTTV external player IDs."""
    create_all()
    db = SessionLocal()
    try:
        rows = {}
        for match in _load_matches(db):
            by_pos = {(p.side, p.position): p for p in match.players}
            for g in match.games:
                if g.game_type != "singles":
                    continue
                sc = _score(g.result)
                hp = by_pos.get(("home", g.home_position))
                ap = by_pos.get(("away", g.away_position))
                if not hp or not ap or not sc:
                    continue
                h_id, a_id = _player_key(hp), _player_key(ap)
                if player_id and player_id not in (h_id, a_id):
                    continue
                if opponent_id and opponent_id not in (h_id, a_id):
                    continue
                key = (h_id, a_id)
                r = rows.setdefault(key, {"home_player_id": h_id, "home_player": hp.name, "away_player_id": a_id, "away_player": ap.name, "matches": 0, "home_wins": 0, "away_wins": 0})
                r["matches"] += 1
                if sc[0] > sc[1]: r["home_wins"] += 1
                else: r["away_wins"] += 1
        out = []
        for r in rows.values():
            r["home_win_rate"] = round(r["home_wins"] / r["matches"], 4)
            r["away_win_rate"] = round(r["away_wins"] / r["matches"], 4)
            out.append(r)
        out.sort(key=lambda x: (-x["matches"], x["home_player"], x["away_player"]))
        return {"ok": True, "player_id": player_id, "opponent_id": opponent_id, "matchups": out, "count": len(out)}
    finally:
        db.close()


def matchup_matrix() -> dict:
    """Return position-independent player-v-player historical results."""
    raw = matchup_stats()["matchups"]
    matrix = {}
    for r in raw:
        for a_id, a_name, b_id, b_name, wins, losses in ((r["home_player_id"], r["home_player"], r["away_player_id"], r["away_player"], r["home_wins"], r["away_wins"]), (r["away_player_id"], r["away_player"], r["home_player_id"], r["home_player"], r["away_wins"], r["home_wins"])):
            key = (a_id, b_id)
            existing = matrix.setdefault(key, {"player_id": a_id, "player": a_name, "opponent_id": b_id, "opponent": b_name, "matches": 0, "wins": 0, "losses": 0})
            existing["matches"] += wins + losses
            existing["wins"] += wins
            existing["losses"] += losses
    out = list(matrix.values())
    for r in out:
        r["win_rate"] = round(r["wins"] / r["matches"], 4) if r["matches"] else None
    out.sort(key=lambda x: (-x["matches"], x["player"], x["opponent"]))
    return {"ok": True, "matchups": out, "count": len(out)}
