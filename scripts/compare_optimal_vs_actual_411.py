"""Compare optimal lineup prediction vs actual XTTV results for league 411 2025/2026."""
from __future__ import annotations

import argparse
import contextlib
import random
import re
import sys
from datetime import date, timedelta
from itertools import permutations

from sqlalchemy import text

from app.analysis_service import (
    OPPONENT_POOL_YEARS,
    STATS_YEARS,
    _augment_profiles_spieltyp,
    _build_partial_opponent_scenarios,
    _cutoff,
    _filter_scenarios,
    _known_quartet_lineup_scenarios,
    _load_doubles_stats,
    _load_opponent_pool,
    _load_player_profiles,
    _raw_team_lineup_scenarios,
    _reference_date,
    analyze_lineup,
)
from app.db import SessionLocal

_LEAKAGE_REF: date | None = None
_ORIG_REFERENCE_DATE = _reference_date


def _patched_reference_date(db):
    if _LEAKAGE_REF is not None:
        return _LEAKAGE_REF
    return _ORIG_REFERENCE_DATE(db)


def _date_upper_sql() -> str:
    return (
        " AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') <= :ref_end"
        if _LEAKAGE_REF is not None
        else ""
    )


def _load_player_profiles_leakage_safe(db, ids, ref_date):
    """Wrap profile loading with an upper date bound when leakage ref is active."""
    if _LEAKAGE_REF is None:
        return _load_player_profiles(db, ids, ref_date)

    from sqlalchemy import bindparam

    ids = [str(x) for x in ids]
    if not ids:
        return {}, {}, {}
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    params = {"ids": ids, "cutoff": stats_cutoff, "ref_end": _LEAKAGE_REF}
    upper = _date_upper_sql()

    stats_stmt = text(
        f"""
        WITH games AS (
            SELECT hp.external_player_id::text AS player_id,
                   hp.name AS player_name,
                   'home'::text AS side,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {upper}
            UNION ALL
            SELECT ap.external_player_id::text, ap.name, 'away',
                   CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {upper}
        )
        SELECT player_id,
               max(player_name) AS player_name,
               sum(win) AS wins,
               count(*) AS games,
               sum(CASE WHEN side='home' THEN win ELSE 0 END) AS home_wins,
               sum(CASE WHEN side='home' THEN 1 ELSE 0 END) AS home_games,
               sum(CASE WHEN side='away' THEN win ELSE 0 END) AS away_wins,
               sum(CASE WHEN side='away' THEN 1 ELSE 0 END) AS away_games
        FROM games GROUP BY player_id
    """
    ).bindparams(bindparam("ids", expanding=True))

    names: dict[str, str] = {}
    profiles: dict[str, dict] = {}
    for r in db.execute(stats_stmt, params).mappings():
        pid = str(r["player_id"])
        names[pid] = r["player_name"]
        profiles[pid] = {
            "wins": int(r["wins"] or 0),
            "games": int(r["games"] or 0),
            "home_wins": int(r["home_wins"] or 0),
            "home_games": int(r["home_games"] or 0),
            "away_wins": int(r["away_wins"] or 0),
            "away_games": int(r["away_games"] or 0),
            "rc_rating": None,
            "rc_trend": None,
            "rc_trend_momentum": None,
            "trend_component": 0.0,
        }

    name_stmt = text(
        """
        SELECT external_player_id::text AS player_id, max(name) AS player_name
        FROM match_players WHERE external_player_id::text IN :ids
        GROUP BY external_player_id
    """
    ).bindparams(bindparam("ids", expanding=True))
    for r in db.execute(name_stmt, params).mappings():
        pid = str(r["player_id"])
        if r["player_name"]:
            names[pid] = r["player_name"]

    rc_stmt = text(
        """
        SELECT xp.external_player_id::text AS player_id, snap.rc_rating
        FROM xttv_players xp
        JOIN LATERAL (
            SELECT rc_rating
            FROM player_rating_snapshots
            WHERE player_id = xp.id
              AND source = 'ratingscentral'
              AND observed_at <= :ref_end
            ORDER BY observed_at DESC
            LIMIT 1
        ) snap ON true
        WHERE xp.external_player_id::text IN :ids
    """
    ).bindparams(bindparam("ids", expanding=True))
    for r in db.execute(rc_stmt, params).mappings():
        pid = str(r["player_id"])
        profiles.setdefault(pid, {})
        profiles[pid]["rc_rating"] = float(r["rc_rating"]) if r["rc_rating"] is not None else None

  # Reuse trend/h2h from original with upper bound via patched loader internals
    from collections import defaultdict
    from datetime import datetime

    from app.analysis_service import (
        _compute_trend_metrics,
        _trend_snapshot_window,
        _empty_profile,
    )

    trend_cutoff = datetime.combine(stats_cutoff, datetime.min.time())
    trend_stmt = text(
        f"""
        SELECT xp.external_player_id::text AS player_id, s.observed_at, s.rc_rating
        FROM xttv_players xp
        JOIN player_rating_snapshots s ON s.player_id = xp.id AND s.source = 'ratingscentral'
        WHERE xp.external_player_id::text IN :ids
          AND s.observed_at >= :cutoff
          AND s.observed_at <= :ref_end
        ORDER BY xp.external_player_id, s.observed_at
    """
    ).bindparams(bindparam("ids", expanding=True))
    trend_rows: dict[str, list] = defaultdict(list)
    for r in db.execute(trend_stmt, {**params, "cutoff": trend_cutoff}).mappings():
        trend_rows[str(r["player_id"])].append(
            {"observed_at": r["observed_at"], "rc_rating": r["rc_rating"]}
        )

    recent_singles_stmt = text(
        f"""
        WITH all_singles AS (
            SELECT hp.external_player_id::text AS player_id,
                   to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') AS match_day,
                   split_part(trim(g.result),':',1)::int AS own_score,
                   split_part(trim(g.result),':',2)::int AS opp_score
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {upper}
            UNION ALL
            SELECT ap.external_player_id::text,
                   to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'),
                   split_part(trim(g.result),':',2)::int,
                   split_part(trim(g.result),':',1)::int
            FROM match_games g
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {upper}
        ),
        ranked AS (
            SELECT player_id, own_score, opp_score, match_day,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY match_day DESC) AS rn
            FROM all_singles
        ),
        bounded AS (
            SELECT player_id, own_score, opp_score, match_day
            FROM ranked r
            WHERE r.match_day >= (
                SELECT r25.match_day
                FROM ranked r25
                WHERE r25.player_id = r.player_id AND r25.rn = 25
            )
            OR NOT EXISTS (
                SELECT 1 FROM ranked r25
                WHERE r25.player_id = r.player_id AND r25.rn = 25
            )
        )
        SELECT player_id, own_score, opp_score, match_day
        FROM bounded
        ORDER BY player_id, match_day DESC
    """
    ).bindparams(bindparam("ids", expanding=True))
    recent_singles_rows: dict[str, list] = defaultdict(list)
    for r in db.execute(recent_singles_stmt, params).mappings():
        recent_singles_rows[str(r["player_id"])].append(
            {
                "own_score": int(r["own_score"]),
                "opp_score": int(r["opp_score"]),
                "match_day": r["match_day"],
            }
        )

    for pid, snapshots in trend_rows.items():
        profiles.setdefault(pid, _empty_profile())
        net_change, component = _compute_trend_metrics(
            snapshots, recent_singles_rows.get(pid, [])
        )
        profiles[pid]["rc_trend"] = net_change
        trend_snapshots = _trend_snapshot_window(
            snapshots, recent_singles_rows.get(pid, [])
        )
        profiles[pid]["rc_trend_momentum"] = net_change
        profiles[pid]["trend_component"] = component

    h2h_stmt = text(
        f"""
        WITH base AS (
            SELECT hp.external_player_id::text AS home_id, ap.external_player_id::text AS away_id,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            JOIN xttv_matches m ON m.id = g.match_id
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND hp.external_player_id::text IN :ids AND ap.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {upper}
        )
        SELECT player_id, opponent_id, sum(win) AS wins, count(*) AS games
        FROM (
            SELECT home_id AS player_id, away_id AS opponent_id, home_win AS win FROM base
            UNION ALL
            SELECT away_id, home_id, 1 - home_win FROM base
        ) directed
        GROUP BY player_id, opponent_id
    """
    ).bindparams(bindparam("ids", expanding=True))
    matchups: dict[tuple[str, str], tuple[int, int]] = {}
    for r in db.execute(h2h_stmt, params).mappings():
        matchups[(str(r["player_id"]), str(r["opponent_id"]))] = (
            int(r["wins"] or 0),
            int(r["games"] or 0),
        )
    return names, profiles, matchups


def _raw_team_lineup_scenarios_leakage_safe(db, team, required_ids=None, ref_date=None, opponent_pool=None):
    ref_date = ref_date or _patched_reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    opponent_pool = opponent_pool or _load_opponent_pool_leakage_safe(db, team, ref_date)
    upper = _date_upper_sql()
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """
        ),
        {"team": team, "cutoff": stats_cutoff, "ref_end": _LEAKAGE_REF},
    ).mappings()
    from collections import Counter, defaultdict

    matches = defaultdict(list)
    for row in rows:
        matches[row["match_id"]].append(row)
    counts = Counter()
    names = {}
    required = set(str(x) for x in required_ids) if required_ids is not None else None
    from app.analysis_service import _position_index

    for players in matches.values():
        by_id = {}
        for r in players:
            pid = str(r["player_id"])
            by_id.setdefault(pid, r)
            names.setdefault(pid, r["player_name"])
        ids = set(by_id)
        if required is not None:
            if not required.issubset(ids):
                continue
            if len(required) == 4 and ids != required:
                continue
        elif len(ids) != 4:
            continue
        order = [None] * 4
        valid = True
        for pid, row in by_id.items():
            idx = _position_index(row["position"])
            if idx is None or order[idx] is not None:
                valid = False
                break
            order[idx] = pid
        if valid and all(order):
            counts[tuple(order)] += 1
    total = sum(counts.values())
    if not total:
        return [], names
    common = counts.most_common(24)
    top_total = sum(c for _, c in common)
    return [(count / top_total, order) for order, count in common], names


def _known_quartet_lineup_scenarios_leakage_safe(db, team, player_ids, ref_date=None):
    ref_date = ref_date or _patched_reference_date(db)
    stats_cutoff = _cutoff(ref_date, STATS_YEARS)
    ids = [str(x) for x in player_ids]
    bind_names = [f"known_id_{i}" for i in range(len(ids))]
    id_params = {name: value for name, value in zip(bind_names, ids)}
    placeholders = ",".join(f":{name}" for name in bind_names)
    upper = _date_upper_sql()
    rows = db.execute(
        text(
            f"""
        SELECT m.id AS match_id, m.match_date, mp.external_player_id AS player_id,
               mp.name AS player_name, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
          AND mp.external_player_id IS NOT NULL
          AND mp.external_player_id::text IN ({placeholders})
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
        ORDER BY m.match_date DESC NULLS LAST, m.id DESC
    """
        ),
        {"team": team, "cutoff": stats_cutoff, "ref_end": _LEAKAGE_REF, **id_params},
    ).mappings()
    from collections import Counter, defaultdict

    matches = defaultdict(list)
    names = {}
    required = set(ids)
    from app.analysis_service import _position_index

    for row in rows:
        matches[row["match_id"]].append(row)
        names.setdefault(str(row["player_id"]), row["player_name"])

    counts = Counter()
    for players in matches.values():
        by_id = {str(row["player_id"]): row for row in players}
        if set(by_id) != required:
            continue
        order = [None] * 4
        valid = True
        for pid in ids:
            idx = _position_index(by_id[pid]["position"])
            if idx is None or order[idx] is not None:
                valid = False
                break
            order[idx] = pid
        if valid and all(order):
            counts[tuple(order)] += 1
    total = sum(counts.values())
    if not total:
        return [], names
    return [(count / total, order) for order, count in counts.most_common(24)], names


def _load_opponent_pool_leakage_safe(db, team, ref_date):
    cutoff = _cutoff(ref_date, OPPONENT_POOL_YEARS)
    upper = _date_upper_sql()
    rows = db.execute(
        text(
            f"""
        SELECT DISTINCT mp.external_player_id::text AS player_id
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
          {upper}
          AND ((m.home_team = :team AND mp.side = 'home') OR (m.away_team = :team AND mp.side = 'away'))
    """
        ),
        {"team": team, "cutoff": cutoff, "ref_end": _LEAKAGE_REF},
    ).scalars().all()
    return {str(pid) for pid in rows}


def _load_analysis_data_leakage_safe(own, opponent_team, actual, use_spieltyp=False):
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '5000ms'"))
        db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]
        actual = None if actual is None else [str(x) for x in actual]
        ref_date = _patched_reference_date(db)
        opponent_pool = _load_opponent_pool_leakage_safe(db, opponent_team, ref_date)
        fallback_names = {}
        if actual is not None and len(actual) > 0:
            if len(actual) == 4:
                scenarios, fallback_names = _known_quartet_lineup_scenarios_leakage_safe(
                    db, opponent_team, actual, ref_date
                )
                source = "known-opponent-historical-raw"
                if not scenarios:
                    scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]
                    source = "all-24-uniform-fallback"
            else:
                scenarios, fallback_names = _raw_team_lineup_scenarios_leakage_safe(
                    db, opponent_team, actual, ref_date, opponent_pool
                )
                source = "known-opponent-historical-raw"
                if not scenarios:
                    scenarios, fallback_names = _build_partial_opponent_scenarios(
                        db, opponent_team, actual, opponent_pool, ref_date
                    )
                    source = "known-opponent-combination-fallback"
        else:
            scenarios, fallback_names = _raw_team_lineup_scenarios_leakage_safe(
                db, opponent_team, None, ref_date, opponent_pool
            )
            source = "predicted-historical-raw"
            if not scenarios:
                return {}, {}, {}, [], source, ref_date, opponent_pool
        if source != "all-24-uniform-fallback":
            scenarios = _filter_scenarios(scenarios, opponent_pool)
        relevant = set(own)
        for _, order in scenarios:
            relevant.update(order)
        ids = list(relevant)
        names, profiles, matchups = _load_player_profiles_leakage_safe(db, ids, ref_date)
        if use_spieltyp:
            _augment_profiles_spieltyp(db, ids, profiles, ref_date)
        names.update({k: v for k, v in fallback_names.items() if v})
        for pid in ids:
            profiles.setdefault(pid, {})
            names.setdefault(pid, f"Spieler {pid}")
        return names, profiles, matchups, scenarios, source, ref_date, opponent_pool
    finally:
        db.close()


@contextlib.contextmanager
def leakage_safe(ref_date: date):
    global _LEAKAGE_REF
    import app.analysis_service as svc

    old_ref = _LEAKAGE_REF
    old_fn = svc._reference_date
    old_load = svc._load_analysis_data
    _LEAKAGE_REF = ref_date
    svc._reference_date = _patched_reference_date
    svc._load_analysis_data = _load_analysis_data_leakage_safe
    try:
        yield
    finally:
        _LEAKAGE_REF = old_ref
        svc._reference_date = old_fn
        svc._load_analysis_data = old_load


def parse_team_result(result: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def outcome_label(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "Sieg"
    if home_score < away_score:
        return "Niederlage"
    return "7:7"


def predicted_outcome(win_prob: float, draw_prob: float) -> str:
    if draw_prob >= 0.25 and abs(win_prob - 0.5) < 0.08:
        return "7:7"
    if win_prob > 0.5:
        return "Sieg"
    if win_prob < 0.5:
        return "Niederlage"
    return "7:7"


def parse_match_date(value: str | None) -> date | None:
    from app.analysis_service import _parse_match_date

    return _parse_match_date(value)


def build_match_sql(team_filter: str | None, limit: int | None) -> text:
    team_clause = ""
    if team_filter:
        team_clause = (
            " AND (m.home_team ILIKE :team OR m.away_team ILIKE :team)"
        )
    order_limit = " ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY')"
    if limit is not None:
        order_limit = " ORDER BY random() LIMIT :limit"
    return text(
        f"""
    SELECT m.id, m.match_date, m.home_team, m.away_team, m.team_result
    FROM xttv_matches m
    WHERE m.league LIKE '%411%'
      AND m.season = '2025/2026'
      AND m.team_result IS NOT NULL
      AND m.team_result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
      AND (
        SELECT COUNT(*) FROM match_players mp
        WHERE mp.match_id = m.id AND mp.side = 'home' AND mp.external_player_id IS NOT NULL
      ) = 4
      AND (
        SELECT COUNT(*) FROM match_players mp
        WHERE mp.match_id = m.id AND mp.side = 'away' AND mp.external_player_id IS NOT NULL
      ) = 4
      {team_clause}
    {order_limit}
"""
    )


def load_players(db, match_id: int) -> tuple[list[str], list[str]]:
    rows = db.execute(
        text(
            """
            SELECT side, external_player_id::text AS pid
            FROM match_players
            WHERE match_id = :mid AND external_player_id IS NOT NULL
            ORDER BY side, position
        """
        ),
        {"mid": match_id},
    ).mappings().all()
    home = [r["pid"] for r in rows if r["side"] == "home"]
    away = [r["pid"] for r in rows if r["side"] == "away"]
    return home, away


def analyze_match(
    own_ids: list[str],
    opp_ids: list[str],
    opponent_team: str,
    ref_date: date,
    *,
    own_is_home: bool,
) -> dict:
    with leakage_safe(ref_date):
        result = analyze_lineup(
            own_ids,
            opponent_team,
            actual_opponent_ids=opp_ids,
            own_is_home=own_is_home,
            use_spieltyp=False,
        )
    rec = result.get("recommendation") or {}
    return {
        "win_prob": rec.get("team_win_probability", 0.0),
        "draw_prob": rec.get("team_draw_probability", 0.0),
        "expected_score": rec.get("expected_score_display", ""),
        "phase": result.get("phase"),
        "warnings": result.get("warnings") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", help="Filter matches where home or away team matches (ILIKE)")
    parser.add_argument("--limit", type=int, default=20, help="Random sample size (default 20)")
    parser.add_argument("--all", action="store_true", help="Use all qualifying matches (chronological)")
    args = parser.parse_args()

    random.seed(42)
    rows_out: list[dict] = []
    errors: list[str] = []

    team_pattern = f"%{args.team}%" if args.team else None
    limit = None if args.all or args.team else args.limit
    match_sql = build_match_sql(team_pattern, limit)
    params: dict = {}
    if team_pattern:
        params["team"] = team_pattern
    if limit is not None:
        params["limit"] = limit

    with SessionLocal() as db:
        matches = db.execute(match_sql, params).mappings().all()
        if not args.all and not args.team and len(matches) < args.limit:
            print(f"WARN: only {len(matches)} qualifying matches found")
        for m in matches:
            match_day = parse_match_date(m["match_date"])
            if not match_day:
                errors.append(f"match {m['id']}: no parseable date")
                continue
            ref = match_day - timedelta(days=1)
            home_ids, away_ids = load_players(db, m["id"])
            if len(home_ids) != 4 or len(away_ids) != 4:
                errors.append(f"match {m['id']}: incomplete lineups")
                continue

            own_team_filter = (args.team or "").lower()
            own_is_home = own_team_filter in (m["home_team"] or "").lower() if own_team_filter else True
            if own_team_filter:
                if own_is_home:
                    own_ids, opp_ids, opponent_team = home_ids, away_ids, m["away_team"]
                else:
                    own_ids, opp_ids, opponent_team = away_ids, home_ids, m["home_team"]
            else:
                own_ids, opp_ids, opponent_team = home_ids, away_ids, m["away_team"]

            try:
                pred = analyze_match(
                    own_ids, opp_ids, opponent_team, ref, own_is_home=own_is_home
                )
            except Exception as exc:
                errors.append(f"match {m['id']}: {exc}")
                continue
            parsed = parse_team_result(m["team_result"])
            if not parsed:
                errors.append(f"match {m['id']}: bad team_result")
                continue
            home_score, away_score = parsed
            if own_team_filter and not own_is_home:
                own_score, opp_score = away_score, home_score
            elif own_team_filter:
                own_score, opp_score = home_score, away_score
            else:
                own_score, opp_score = home_score, away_score
            actual_outcome = outcome_label(own_score, opp_score)
            pred_outcome = predicted_outcome(pred["win_prob"], pred["draw_prob"])
            match_ok = pred_outcome == actual_outcome
            rows_out.append(
                {
                    "datum": (m["match_date"] or "")[:10],
                    "heim": m["home_team"],
                    "gast": m["away_team"],
                    "optimal": f"{pred['win_prob']*100:.1f}% / {pred['expected_score']}",
                    "tatsaechlich": f"{own_score}:{opp_score} ({actual_outcome})",
                    "match": "Ja" if match_ok else "Nein",
                    "phase": pred["phase"],
                }
            )

    if args.team:
        print(f"Filter: {args.team} ({len(rows_out)} Spiele)\n")
    print("| Datum | Heim | Gast | Optimal erwartet | Tatsächlich XTTV | Match? |")
    print("|-------|------|------|------------------|------------------|--------|")
    for r in rows_out:
        print(
            f"| {r['datum']} | {r['heim']} | {r['gast']} | {r['optimal']} | "
            f"{r['tatsaechlich']} | {r['match']} |"
        )
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  - {e}")
    print(f"\nSummary: {sum(1 for r in rows_out if r['match']=='Ja')}/{len(rows_out)} outcome matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
