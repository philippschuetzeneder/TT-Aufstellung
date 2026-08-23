from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from itertools import combinations
import re
import time

from sqlalchemy import text
from sqlalchemy.orm import selectinload

from .analysis_cache import ensure_analysis_cache, ensure_analysis_schema
from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, PlayerRatingSnapshot, XttvMatch, XttvPlayer
from .analysis_service import (
    _compute_trend_metrics,
    _recent_singles_window,
    _trend_snapshot_window,
    _parse_match_date,
    _weighted_rc_momentum,
    _win_rate,
)
from .player_analysis_service import resolve_latest_league_season, _season_label

MATCHUP_MIN_GAMES = 3
_LEAGUE_STATS_CACHE: dict[str, tuple[float, dict]] = {}
_LEAGUE_STATS_TTL_SEC = 120.0

_PLAYER_MATCHUPS_SQL = text("""
    WITH base AS (
        SELECT hp.external_player_id::text AS home_id, hp.name AS home_name,
               ap.external_player_id::text AS away_id, ap.name AS away_name,
               CASE WHEN split_part(trim(g.result), ':', 1)::int > split_part(trim(g.result), ':', 2)::int THEN 1 ELSE 0 END AS home_win
        FROM match_games g
        JOIN match_players hp ON hp.match_id = g.match_id AND hp.side = 'home' AND hp.position = g.home_position
        JOIN match_players ap ON ap.match_id = g.match_id AND ap.side = 'away' AND ap.position = g.away_position
        WHERE g.game_type = 'singles'
          AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
          AND (hp.external_player_id::text = :player_id OR ap.external_player_id::text = :player_id)
    ),
    directed AS (
        SELECT home_id AS player_id, away_id AS opponent_id, home_name AS player_name, away_name AS opponent_name, home_win AS win
        FROM base
        UNION ALL
        SELECT away_id, home_id, away_name, home_name, 1 - home_win
        FROM base
    ),
    agg AS (
        SELECT opponent_id, max(opponent_name) AS opponent_name, sum(win) AS wins, count(*) AS games
        FROM directed
        WHERE player_id = :player_id
        GROUP BY opponent_id
    )
    SELECT opponent_id, opponent_name, wins, games
    FROM agg
    WHERE games >= :min_games
    ORDER BY wins::float / games DESC, games DESC, opponent_name
""")

_PLAYER_SINGLES_SQL = text("""
    SELECT
        m.match_date,
        m.league,
        mp.side AS player_side,
        CASE WHEN mp.side = 'home' THEN ap.external_player_id::text ELSE hp.external_player_id::text END AS opponent_id,
        CASE WHEN mp.side = 'home' THEN ap.name ELSE hp.name END AS opponent_name,
        CASE WHEN mp.side = 'home'
            THEN split_part(trim(g.result), ':', 1)::int
            ELSE split_part(trim(g.result), ':', 2)::int
        END AS own_score,
        CASE WHEN mp.side = 'home'
            THEN split_part(trim(g.result), ':', 2)::int
            ELSE split_part(trim(g.result), ':', 1)::int
        END AS opp_score
    FROM match_players mp
    JOIN xttv_matches m ON m.id = mp.match_id
    JOIN match_games g ON g.match_id = m.id
        AND g.game_type = 'singles'
        AND (
            (mp.side = 'home' AND g.home_position = mp.position)
            OR (mp.side = 'away' AND g.away_position = mp.position)
        )
    JOIN match_players hp ON hp.match_id = m.id AND hp.side = 'home' AND hp.position = g.home_position
    JOIN match_players ap ON ap.match_id = m.id AND ap.side = 'away' AND ap.position = g.away_position
    WHERE mp.external_player_id::text = :player_id
      AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
    ORDER BY m.match_date NULLS LAST, m.id
""")


def _load_player_singles(db, player_id: str) -> list[dict]:
    rows = db.execute(_PLAYER_SINGLES_SQL, {"player_id": player_id}).mappings().all()
    singles = []
    for row in rows:
        own = int(row["own_score"])
        opp = int(row["opp_score"])
        day = _parse_match_date(row["match_date"])
        singles.append({
            "date": day.isoformat() if day else None,
            "opponent_id": str(row["opponent_id"]) if row["opponent_id"] else None,
            "opponent": row["opponent_name"],
            "side": row["player_side"],
            "own_score": own,
            "opp_score": opp,
            "win": own > opp,
            "draw": own == opp,
            "league": row["league"],
        })
    return singles


def _load_player_matchups(db, player_id: str, min_games: int = MATCHUP_MIN_GAMES) -> list[dict]:
    ensure_analysis_schema()
    if ensure_analysis_cache():
        rows = db.execute(
            text(
                """
                SELECT opponent_id, opponent_name, wins, games
                FROM analysis_matchups
                WHERE player_id = :player_id AND games >= :min_games
                ORDER BY wins::float / games DESC, games DESC, opponent_name
                """
            ),
            {"player_id": player_id, "min_games": min_games},
        ).mappings().all()
        return [
            {
                "opponent_id": str(row["opponent_id"]),
                "opponent": row["opponent_name"],
                "matches": int(row["games"]),
                "wins": int(row["wins"]),
                "losses": int(row["games"]) - int(row["wins"]),
                "win_rate": round(int(row["wins"]) / int(row["games"]), 4),
            }
            for row in rows
        ]

    # SQL fallback — never scan all matches via matchup_stats().
    rows = db.execute(
        _PLAYER_MATCHUPS_SQL,
        {"player_id": player_id, "min_games": min_games},
    ).mappings().all()
    return [
        {
            "opponent_id": str(row["opponent_id"]),
            "opponent": row["opponent_name"],
            "matches": int(row["games"]),
            "wins": int(row["wins"]),
            "losses": int(row["games"]) - int(row["wins"]),
            "win_rate": round(int(row["wins"]) / int(row["games"]), 4),
        }
        for row in rows
    ]


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
    cache_key = league or ""
    now = time.monotonic()
    cached = _LEAGUE_STATS_CACHE.get(cache_key)
    if cached and now - cached[0] < _LEAGUE_STATS_TTL_SEC:
        return cached[1]

    result = _compute_league_player_stats(league)
    _LEAGUE_STATS_CACHE[cache_key] = (now, result)
    return result


def _compute_league_player_stats(league: str | None = None) -> dict:
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
            if not player:
                continue
            # Current RC must reflect the latest Ratings Central observation, not the
            # league's last match day (stichtag). Lineup analysis keeps stichtag-safe
            # RC for predictions; the statistics view shows today's RC.
            snapshots[str(player.external_player_id)].append({
                "observed_at": snapshot.observed_at,
                "rc_rating": snapshot.rc_rating,
            })

        output = []
        for pid, name in names.items():
            entry = stats.get(pid, {"games": 0, "wins": 0, "home_games": 0, "home_wins": 0, "away_games": 0, "away_wins": 0})
            series = snapshots.get(pid, [])
            trend_singles = _recent_singles_window(recent_singles.get(pid, []))
            trend_series = _trend_snapshot_window(series, trend_singles)
            trend, _ = _compute_trend_metrics(series, trend_singles)
            current_rc = series[-1]["rc_rating"] if series else None
            output.append({
                "id": pid,
                "name": name,
                "team": ", ".join(sorted(teams.get(pid, set()))) or None,
                "rc_rating": float(current_rc) if current_rc is not None else None,
                "rc_trend": round(trend, 1) if trend is not None else None,
                "rc_trend_momentum": round(trend, 1) if trend is not None else None,
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


def _player_name_and_team(db, player_id: str, league: str) -> tuple[str | None, str | None]:
    name = db.execute(
        text(
            """
            SELECT max(name) FROM match_players
            WHERE external_player_id::text = :player_id
            """
        ),
        {"player_id": player_id},
    ).scalar()
    team = db.execute(
        text(
            """
            SELECT DISTINCT CASE WHEN mp.side = 'home' THEN m.home_team ELSE m.away_team END AS team
            FROM match_players mp
            JOIN xttv_matches m ON m.id = mp.match_id
            WHERE mp.external_player_id::text = :player_id AND m.league = :league
            LIMIT 1
            """
        ),
        {"player_id": player_id, "league": league},
    ).scalar()
    return name, team


def _build_player_summary(
    player_id: str,
    name: str | None,
    team: str | None,
    selected_singles: list[dict],
    snapshots: list,
) -> dict:
    home_games = sum(1 for row in selected_singles if row["side"] == "home")
    home_wins = sum(1 for row in selected_singles if row["side"] == "home" and row["win"])
    away_games = sum(1 for row in selected_singles if row["side"] == "away")
    away_wins = sum(1 for row in selected_singles if row["side"] == "away" and row["win"])
    series = [
        {"observed_at": item.observed_at, "rc_rating": item.rc_rating}
        for item in snapshots
        if item.rc_rating is not None
    ]
    recent_for_trend = [
        {
            "own_score": row["own_score"],
            "opp_score": row["opp_score"],
            "match_day": _parse_match_date(row["date"]) if row.get("date") else None,
        }
        for row in selected_singles
        if row.get("date")
    ]
    trend_singles = _recent_singles_window(recent_for_trend)
    trend, _ = _compute_trend_metrics(series, trend_singles)
    current_rc = series[-1]["rc_rating"] if series else None
    games = len(selected_singles)
    wins = sum(1 for row in selected_singles if row["win"])
    return {
        "id": player_id,
        "name": name,
        "team": team,
        "rc_rating": float(current_rc) if current_rc is not None else None,
        "rc_trend": round(trend, 1) if trend is not None else None,
        "rc_trend_momentum": round(trend, 1) if trend is not None else None,
        "home_strength": round(_win_rate(home_wins, home_games) * 100, 1) if home_games else None,
        "away_strength": round(_win_rate(away_wins, away_games) * 100, 1) if away_games else None,
        "games": games,
        "wins": wins,
    }


def player_profile(league: str | None, player_id: str, opponent_id: str | None = None) -> dict:
    """Return a complete profile, with optional detail for one opponent."""
    player_id = str(player_id)
    create_all()
    cache_key = league or ""
    cached_ranking = _LEAGUE_STATS_CACHE.get(cache_key)
    ranking = cached_ranking[1] if cached_ranking and time.monotonic() - cached_ranking[0] < _LEAGUE_STATS_TTL_SEC else None

    with SessionLocal() as db:
        db.execute(text("SET statement_timeout = '25000ms'"))
        db.execute(text("SET lock_timeout = '3000ms'"))
        resolved = resolve_latest_league_season(db, league or "")
        if not resolved:
            return {"ok": False, "error": "Spieler oder Liga nicht gefunden"}

        singles = _load_player_singles(db, player_id)
        matchups = _load_player_matchups(db, player_id)
        snapshots = (
            db.query(PlayerRatingSnapshot)
            .join(XttvPlayer)
            .filter(
                XttvPlayer.external_player_id == player_id,
                PlayerRatingSnapshot.source == "ratingscentral",
            )
            .order_by(PlayerRatingSnapshot.observed_at)
            .all()
        )
        selected_singles = [row for row in singles if row["league"] == resolved]
        if not selected_singles:
            return {"ok": False, "error": "Spieler oder Liga nicht gefunden"}

        ranked = ranking.get("players", []) if ranking else []
        player = next((row for row in ranked if str(row.get("id")) == player_id), None)
        if player is None:
            name, team = _player_name_and_team(db, player_id, resolved)
            player = _build_player_summary(player_id, name, team, selected_singles, snapshots)

    season = ranking.get("season") if ranking else _season_label(resolved)
    valid_games = len(selected_singles)
    wins = sum(1 for row in selected_singles if row["win"])
    draws = sum(1 for row in selected_singles if row["draw"])
    losses = valid_games - wins - draws
    recent = list(reversed(selected_singles))

    def form_stats(rows):
        games = len(rows)
        row_wins = sum(1 for row in rows if row["win"])
        row_draws = sum(1 for row in rows if row["draw"])
        return {
            "games": games,
            "wins": row_wins,
            "losses": games - row_wins - row_draws,
            "draws": row_draws,
            "win_rate": round(row_wins / games, 4) if games else None,
        }

    snapshot_data = [
        {"date": item.observed_at.isoformat(), "rc_rating": item.rc_rating}
        for item in snapshots if item.rc_rating is not None
    ]
    ranked_position = next(
        (index + 1 for index, row in enumerate(ranked) if str(row.get("id")) == player_id), None
    ) if ranked else None

    matchup_by_opponent = {row["opponent_id"]: row for row in matchups}
    selected_opponent = None
    if opponent_id:
        selected_opponent = matchup_by_opponent.get(str(opponent_id))
        if selected_opponent:
            selected_opponent = {
                **selected_opponent,
                "results": [
                    row for row in reversed(singles)
                    if row["opponent_id"] == str(opponent_id)
                ][:10],
            }

    league_cutoff = max(
        (row_date for row_date in (row["date"] for row in singles) if row_date),
        default=None,
    )
    if league_cutoff:
        league_cutoff = _parse_match_date(league_cutoff) - timedelta(days=round(3 * 365.25))
    recent_leagues = []
    for row in reversed(singles):
        if row["league"] and (not league_cutoff or not row["date"] or row["date"] >= league_cutoff.isoformat()):
            if row["league"] not in recent_leagues:
                recent_leagues.append(row["league"])

    current_team = player.get("team")
    for row in reversed(selected_singles):
        if row.get("league") == resolved:
            # Team name is not in singles SQL; keep ranking team unless we add it later.
            break

    venue_stats = {}
    for side in ("home", "away"):
        rows = [row for row in selected_singles if row["side"] == side]
        side_games = len(rows)
        side_wins = sum(1 for row in rows if row["win"])
        venue_stats[side] = {
            "games": side_games,
            "wins": side_wins,
            "losses": side_games - side_wins - sum(1 for row in rows if row["draw"]),
            "draws": sum(1 for row in rows if row["draw"]),
            "win_rate": round(side_wins / side_games, 4) if side_games else None,
            "strength": player.get(f"{side}_strength"),
        }

    positive_matchups = [row for row in matchups if row["wins"] > row["losses"]]
    negative_matchups = [row for row in matchups if row["losses"] > row["wins"]]
    best = sorted(
        positive_matchups,
        key=lambda row: (-row["win_rate"], -row["matches"], row["opponent"]),
    )[:5]
    difficult = sorted(
        negative_matchups,
        key=lambda row: (row["win_rate"], -row["matches"], row["opponent"]),
    )[:5]

    return {
        "ok": True,
        "league": league,
        "latest_league": resolved,
        "season": season,
        "player": {
            **player,
            "team": current_team,
            "rank": ranked_position,
            "matches": valid_games,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "win_rate": round(wins / valid_games, 4) if valid_games else None,
        },
        "form": {
            "last_5": form_stats(recent[:5]),
            "last_10": form_stats(recent[:10]),
            "games": list(reversed(recent[:10])),
        },
        "current_season": form_stats(selected_singles),
        "home_away": venue_stats,
        "rc_history": snapshot_data[-40:],
        "leagues_last_3_years": [
            {"name": league_name, "season": _season_label(league_name)}
            for league_name in recent_leagues
        ],
        "matchups": {
            "minimum_games": MATCHUP_MIN_GAMES,
            "best": best,
            "difficult": difficult,
            "count": len(matchups),
        },
        "opponent_detail": selected_opponent,
    }
