import threading
from sqlalchemy import text
from .db import engine

_LOCK = threading.Lock()
_READY = False

_SCHEMA_SQL = [
    "CREATE TABLE IF NOT EXISTS analysis_cache_meta (cache_name text PRIMARY KEY, source_match_count bigint NOT NULL DEFAULT 0, refreshed_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS analysis_player_stats (player_id text PRIMARY KEY, player_name text NOT NULL, singles_wins bigint NOT NULL, singles_games bigint NOT NULL)",
    "CREATE TABLE IF NOT EXISTS analysis_matchups (player_id text NOT NULL, opponent_id text NOT NULL, player_name text NOT NULL, opponent_name text NOT NULL, wins bigint NOT NULL, games bigint NOT NULL, PRIMARY KEY (player_id, opponent_id))",
    "CREATE TABLE IF NOT EXISTS analysis_lineup_orders (team text NOT NULL, lineup_key text NOT NULL, order_key text NOT NULL, p1 text NOT NULL, p2 text NOT NULL, p3 text NOT NULL, p4 text NOT NULL, appearances bigint NOT NULL, PRIMARY KEY (team, lineup_key, order_key))",
    "CREATE INDEX IF NOT EXISTS ix_analysis_lineup_team ON analysis_lineup_orders(team)",
    "CREATE INDEX IF NOT EXISTS ix_analysis_lineup_key ON analysis_lineup_orders(lineup_key)",
    "CREATE INDEX IF NOT EXISTS ix_match_players_external_player_id ON match_players(external_player_id)",
    "CREATE INDEX IF NOT EXISTS ix_match_players_match_side_position ON match_players(match_id, side, position)",
    "CREATE INDEX IF NOT EXISTS ix_match_games_match_type_positions ON match_games(match_id, game_type, home_position, away_position)",
]


def ensure_analysis_schema() -> None:
    with engine.begin() as db:
        for sql in _SCHEMA_SQL:
            db.execute(text(sql))


def refresh_analysis_cache() -> dict:
    global _READY
    with _LOCK:
        ensure_analysis_schema()
        with engine.begin() as db:
            source_count = int(db.execute(text("SELECT COUNT(*) FROM xttv_matches")).scalar() or 0)
            cached_count = db.execute(text("SELECT source_match_count FROM analysis_cache_meta WHERE cache_name='main'")).scalar()
            if cached_count is not None and int(cached_count) == source_count:
                _READY = True
                return {"ok": True, "refreshed": False, "source_matches": source_count}
            db.execute(text("TRUNCATE analysis_player_stats, analysis_matchups, analysis_lineup_orders"))
            db.execute(text("""
                INSERT INTO analysis_player_stats(player_id,player_name,singles_wins,singles_games)
                WITH gp AS (
                    SELECT hp.external_player_id player_id,hp.name player_name,CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END win
                    FROM match_games g JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
                    WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                    UNION ALL
                    SELECT ap.external_player_id,ap.name,CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
                    FROM match_games g JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
                    WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                ) SELECT player_id,max(player_name),sum(win),count(*) FROM gp WHERE player_id IS NOT NULL GROUP BY player_id
            """))
            db.execute(text("""
                INSERT INTO analysis_matchups(player_id,opponent_id,player_name,opponent_name,wins,games)
                WITH base AS (
                    SELECT hp.external_player_id home_id,hp.name home_name,ap.external_player_id away_id,ap.name away_name,
                    CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END home_win
                    FROM match_games g JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
                    JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
                    WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                )
                SELECT home_id,away_id,max(home_name),max(away_name),sum(home_win),count(*) FROM base WHERE home_id IS NOT NULL AND away_id IS NOT NULL GROUP BY home_id,away_id
                UNION ALL
                SELECT away_id,home_id,max(away_name),max(home_name),sum(1-home_win),count(*) FROM base WHERE home_id IS NOT NULL AND away_id IS NOT NULL GROUP BY away_id,home_id
                ON CONFLICT (player_id,opponent_id) DO UPDATE SET player_name=EXCLUDED.player_name,opponent_name=EXCLUDED.opponent_name,wins=analysis_matchups.wins+EXCLUDED.wins,games=analysis_matchups.games+EXCLUDED.games
            """))
            db.execute(text("""
                INSERT INTO analysis_lineup_orders(team,lineup_key,order_key,p1,p2,p3,p4,appearances)
                WITH side_players AS (
                    SELECT m.id match_id,m.home_team team,mp.external_player_id player_id,mp.position FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id AND mp.side='home'
                    UNION ALL
                    SELECT m.id,m.away_team,mp.external_player_id,mp.position FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id AND mp.side='away'
                ), four AS (
                    SELECT match_id,team,string_agg(player_id,',' ORDER BY player_id) lineup_key,
                    max(player_id) FILTER (WHERE position IN ('A','1')) p1,max(player_id) FILTER (WHERE position IN ('B','2')) p2,
                    max(player_id) FILTER (WHERE position IN ('C','3')) p3,max(player_id) FILTER (WHERE position IN ('D','4')) p4
                    FROM side_players WHERE player_id IS NOT NULL GROUP BY match_id,team
                    HAVING count(*)=4 AND count(DISTINCT player_id)=4 AND count(DISTINCT position)=4
                ) SELECT team,lineup_key,concat(p1,',',p2,',',p3,',',p4),p1,p2,p3,p4,count(*) FROM four GROUP BY team,lineup_key,p1,p2,p3,p4
            """))
            db.execute(text("INSERT INTO analysis_cache_meta(cache_name,source_match_count,refreshed_at) VALUES ('main',:n,CURRENT_TIMESTAMP) ON CONFLICT (cache_name) DO UPDATE SET source_match_count=EXCLUDED.source_match_count,refreshed_at=EXCLUDED.refreshed_at"), {"n": source_count})
        _READY = True
        return {"ok": True, "refreshed": True, "source_matches": source_count}


def ensure_analysis_cache() -> bool:
    global _READY
    if _READY: return True
    try:
        ensure_analysis_schema()
        with engine.connect() as db:
            row = db.execute(text("SELECT source_match_count FROM analysis_cache_meta WHERE cache_name='main'")).first()
            if row is None: return False
            _READY = True; return True
    except Exception: return False


def start_background_refresh() -> None:
    try: ensure_analysis_schema()
    except Exception: pass
    threading.Thread(target=refresh_analysis_cache, name="analysis-cache-refresh", daemon=True).start()
