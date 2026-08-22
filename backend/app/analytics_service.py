from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from itertools import combinations
import re

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, PlayerRatingSnapshot, XttvMatch, XttvPlayer
from .analysis_service import _compute_trend_metrics, _parse_match_date, _win_rate
from .player_analysis_service import resolve_latest_league_season, _season_label


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


def league_player_stats(league: str | None = None) -> dict:
    """Return one batch of player metrics for the selected, latest league season.

    The RC trend and venue rates deliberately use the same helpers and
    smoothing thresholds as the lineup analysis, but are calculated only from
    matches in this league and from a league-specific reference date.
    """
    create_all()
    with SessionLocal() as db:
        resolved = resolve_latest_league_season(db, league or "")
        if not resolved:
            return {"ok": True, "league": league, "latest_league": None, "season": None, "players": [], "count": 0}

        matches = (
            db.query(XttvMatch)
            .options(selectinload(XttvMatch.players), selectinload(XttvMatch.games))
            .filter(XttvMatch.league == resolved)
            .order_by(XttvMatch.id)
            .all()
        )
        if not matches:
            return {"ok": True, "league": league, "latest_league": resolved, "season": _season_label(resolved), "players": [], "count": 0}

        def match_day(match):
            return _parse_match_date(match.match_date)

        ref_date = max((match_day(m) for m in matches if match_day(m)), default=None)
        stats_cutoff = ref_date - timedelta(days=round(3 * 365.25)) if ref_date else None
        stats = {}
        recent_singles = defaultdict(list)
        names = {}
        teams = defaultdict(set)

        for match in matches:
            day = match_day(match)
            if stats_cutoff and (day is None or day < stats_cutoff):
                continue
            by_position = {(p.side, p.position): p for p in match.players}
            for player in match.players:
                if not player.external_player_id:
                    continue
                pid = str(player.external_player_id)
                names[pid] = player.name
                team = match.home_team if player.side == "home" else match.away_team
                if team:
                    teams[pid].add(team)
                stats.setdefault(pid, {
                    "games": 0, "wins": 0, "home_games": 0, "home_wins": 0,
                    "away_games": 0, "away_wins": 0,
                })
            for game in match.games:
                score = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", game.result or "")
                if game.game_type != "singles" or not score:
                    continue
                home = by_position.get(("home", game.home_position))
                away = by_position.get(("away", game.away_position))
                if not home or not away or not home.external_player_id or not away.external_player_id:
                    continue
                home_score, away_score = map(int, score.groups())
                for player, side, won in (
                    (home, "home", home_score > away_score),
                    (away, "away", away_score > home_score),
                ):
                    pid = str(player.external_player_id)
                    entry = stats.setdefault(pid, {
                        "games": 0, "wins": 0, "home_games": 0, "home_wins": 0,
                        "away_games": 0, "away_wins": 0,
                    })
                    entry["games"] += 1
                    entry["wins"] += int(won)
                    entry[f"{side}_games"] += 1
                    entry[f"{side}_wins"] += int(won)
                    if day:
                        recent_singles[pid].append({
                            "own_score": home_score if side == "home" else away_score,
                            "opp_score": away_score if side == "home" else home_score,
                            "match_day": day,
                        })

        player_ids = set(names)
        db_players = db.query(XttvPlayer).filter(XttvPlayer.external_player_id.in_(player_ids)).all()
        player_by_db_id = {p.id: p for p in db_players}
        snapshot_rows = []
        if db_players:
            snapshot_rows = (
                db.query(PlayerRatingSnapshot)
                .filter(
                    PlayerRatingSnapshot.player_id.in_([p.id for p in db_players]),
                    PlayerRatingSnapshot.source == "ratingscentral",
                )
                .order_by(PlayerRatingSnapshot.observed_at)
                .all()
            )
        snapshots = defaultdict(list)
        for snapshot in snapshot_rows:
            player = player_by_db_id.get(snapshot.player_id)
            if not player or (ref_date and snapshot.observed_at.date() > ref_date):
                continue
            snapshots[str(player.external_player_id)].append({
                "observed_at": snapshot.observed_at,
                "rc_rating": snapshot.rc_rating,
            })

        output = []
        for pid, name in names.items():
            entry = stats.get(pid, {"games": 0, "wins": 0, "home_games": 0, "home_wins": 0, "away_games": 0, "away_wins": 0})
            series = snapshots.get(pid, [])
            trend_series = [row for row in series if not ref_date or row["observed_at"].date() >= ref_date - timedelta(days=round(365.25))]
            trend, _ = _compute_trend_metrics(trend_series, sorted(recent_singles.get(pid, []), key=lambda row: row["match_day"], reverse=True)[:3])
            current_rc = series[-1]["rc_rating"] if series else None
            output.append({
                "id": pid,
                "name": name,
                "team": ", ".join(sorted(teams.get(pid, set()))) or None,
                "rc_rating": float(current_rc) if current_rc is not None else None,
                "rc_trend": round(trend, 1) if series else None,
                "home_strength": round(_win_rate(entry["home_wins"], entry["home_games"]) * 100, 1) if entry["home_games"] else None,
                "away_strength": round(_win_rate(entry["away_wins"], entry["away_games"]) * 100, 1) if entry["away_games"] else None,
                "games": entry["games"],
                "wins": entry["wins"],
            })
        output.sort(key=lambda row: (row["rc_rating"] is None, -(row["rc_rating"] or 0), row["name"]))
        return {
            "ok": True,
            "league": league,
            "latest_league": resolved,
            "season": _season_label(resolved),
            "reference_date": ref_date.isoformat() if ref_date else None,
            "players": output,
            "count": len(output),
        }


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
