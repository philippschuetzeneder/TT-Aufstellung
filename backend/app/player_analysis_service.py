from __future__ import annotations
from collections import defaultdict
from datetime import timedelta
from itertools import permutations
import math, re
from sqlalchemy import text
from sqlalchemy.orm import selectinload
from .db import SessionLocal, create_all
from .models import MatchPlayer, XttvMatch
from .opponent_prediction_service import predict_opponent_lineups

WIN_TARGET = 8
_LEAGUE_SEASON_SUFFIX = re.compile(r"\s+(20\d{2}/20\d{2})\s*$")


def _league_group(league: str | None) -> str:
    if not league:
        return ""
    return _LEAGUE_SEASON_SUFFIX.sub("", league).strip()


def _season_label(league: str | None) -> str | None:
    match = _LEAGUE_SEASON_SUFFIX.search(league or "")
    return match.group(1) if match else None


def _season_sort_key(league: str) -> tuple[int, int]:
    label = _season_label(league)
    if not label:
        return (0, 0)
    start, end = label.split("/")
    return (int(start), int(end))


def resolve_latest_league_season(session, league_group: str) -> str | None:
    """Resolve a league group (without season suffix) to the newest season row in DB."""
    pattern = league_group.strip() + "%"
    rows = session.execute(
        text("SELECT league FROM xttv_matches WHERE league LIKE :pattern GROUP BY league ORDER BY league"),
        {"pattern": pattern},
    ).scalars().all()
    if not rows:
        return None
    return max(rows, key=_season_sort_key)


def list_leagues():
    """Distinct league groups with counts for the latest available season only."""
    with SessionLocal() as session:
        rows = session.execute(text(
            "SELECT league, COUNT(*) AS c FROM xttv_matches WHERE league IS NOT NULL GROUP BY league ORDER BY league"
        )).mappings()
        by_group: dict[str, list[tuple[str, int]]] = {}
        for row in rows:
            group = _league_group(row["league"])
            if not group:
                continue
            by_group.setdefault(group, []).append((row["league"], int(row["c"])))
    leagues = []
    for name, entries in sorted(by_group.items()):
        latest_league, match_count = max(entries, key=lambda item: _season_sort_key(item[0]))
        leagues.append({
            "id": name,
            "name": name,
            "season": _season_label(latest_league),
            "latest_league": latest_league,
            "match_count": match_count,
        })
    return {"ok": True, "leagues": leagues, "count": len(leagues)}

def _id(p): return str(p.external_player_id or f"name:{p.name}")
def _pos(v):
    if not v: return None
    v=v.strip().upper()
    if v in "ABCD": return ord(v)-65
    if v in {"1","2","3","4"}: return int(v)-1
    return None
def _score(v):
    m=re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*",v or "")
    return (int(m.group(1)),int(m.group(2))) if m else None

def _load():
    db=SessionLocal(); ms=db.query(XttvMatch).options(selectinload(XttvMatch.players),selectinload(XttvMatch.games)).all(); return db,ms

def _stats(ms):
    overall=defaultdict(lambda:[0,0]); h2h=defaultdict(lambda:[0,0]); names={}
    for m in ms:
        by={(p.side,p.position):p for p in m.players}
        for p in m.players: names[_id(p)]=p.name
        for g in m.games:
            s=_score(g.result)
            if not s or s[0]==s[1] or g.game_type!="singles": continue
            hp=by.get(("home",g.home_position)); ap=by.get(("away",g.away_position))
            if not hp or not ap: continue
            a,b=_id(hp),_id(ap); overall[a][1]+=1; overall[b][1]+=1; h2h[(a,b)][1]+=1; h2h[(b,a)][1]+=1
            if s[0]>s[1]: overall[a][0]+=1; h2h[(a,b)][0]+=1
            else: overall[b][0]+=1; h2h[(b,a)][0]+=1
    return names,overall,h2h

def _strength(pid,overall):
    w,n=overall.get(pid,(0,0)); return (w+5)/(n+10)

def _prob(a,b,overall,h2h):
    base=1/(1+math.exp(-7*(_strength(a,overall)-_strength(b,overall))))
    w,n=h2h.get((a,b),(0,0))
    if n:
        direct=(w+2)/(n+4); weight=min(.55,n/12); return (1-weight)*base+weight*direct
    return base

def _pair_prob(a,b,c,d,overall):
    left=(_strength(a,overall)+_strength(b,overall))/2; right=(_strength(c,overall)+_strength(d,overall))/2
    return 1/(1+math.exp(-7*(left-right)))

def _schedule(ms):
    counts=defaultdict(int)
    for m in ms:
        for g in m.games:
            if g.game_type!="singles" or g.sequence is None: continue
            h,a=_pos(g.home_position),_pos(g.away_position); seq=int(g.sequence)
            if h is not None and a is not None and 1<=seq<=14 and seq not in (5,10): counts[(seq,h,a)]+=1
    out=[]
    for seq in range(1,15):
        if seq in (5,10): continue
        c=[x for x in counts if x[0]==seq]
        if c:
            b=max(c,key=lambda x:counts[x]); out.append((b[1],b[2]))
    return out[:12] if len(out)>=12 else [(0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1)]

def _team_win(single,doubles):
    dist=[1.0]
    for p in list(single)+list(doubles):
        nxt=[0.0]*(len(dist)+1)
        for w,mass in enumerate(dist): nxt[w]+=mass*(1-p); nxt[w+1]+=mass*p
        dist=nxt
    return sum(dist[WIN_TARGET:])

def list_teams(league: str | None = None):
    with SessionLocal() as session:
        if league:
            resolved = resolve_latest_league_season(session, league)
            if not resolved:
                return {"ok": True, "league": league, "season": None, "teams": [], "count": 0}
            rows = session.execute(
                text("""
                    SELECT team_name, COUNT(DISTINCT match_id) AS match_count
                    FROM (
                        SELECT home_team AS team_name, id AS match_id
                        FROM xttv_matches WHERE league = :league AND home_team IS NOT NULL
                        UNION ALL
                        SELECT away_team AS team_name, id AS match_id
                        FROM xttv_matches WHERE league = :league AND away_team IS NOT NULL
                    ) t
                    GROUP BY team_name
                    ORDER BY team_name
                """),
                {"league": resolved},
            ).mappings()
            teams = [
                {"id": row["team_name"], "name": row["team_name"], "player_count": int(row["match_count"])}
                for row in rows
            ]
            return {
                "ok": True,
                "league": league,
                "season": _season_label(resolved),
                "latest_league": resolved,
                "teams": teams,
                "count": len(teams),
            }

        rows = session.execute(
            text("""
                SELECT team_name, COUNT(DISTINCT external_player_id) AS player_count
                FROM (
                    SELECT m.home_team AS team_name, mp.external_player_id
                    FROM xttv_matches m JOIN match_players mp ON mp.match_id = m.id AND mp.side = 'home'
                    WHERE m.home_team IS NOT NULL AND mp.external_player_id IS NOT NULL
                    UNION
                    SELECT m.away_team AS team_name, mp.external_player_id
                    FROM xttv_matches m JOIN match_players mp ON mp.match_id = m.id AND mp.side = 'away'
                    WHERE m.away_team IS NOT NULL AND mp.external_player_id IS NOT NULL
                ) t
                GROUP BY team_name
                ORDER BY team_name
            """)
        ).mappings()
        teams = [
            {"id": row["team_name"], "name": row["team_name"], "player_count": int(row["player_count"])}
            for row in rows
        ]
        return {"ok": True, "teams": teams, "count": len(teams)}

def list_players(team_name, league: str | None = None):
    if not team_name:
        return {"ok": False, "error": "team is required", "players": []}
    with SessionLocal() as session:
        resolved = resolve_latest_league_season(session, league) if league else None
        ref_row = session.execute(text("""
            SELECT max(to_date(substring(match_date from 1 for 10), 'DD.MM.YYYY')) AS latest
            FROM xttv_matches WHERE match_date IS NOT NULL
        """)).mappings().first()
        ref_date = ref_row["latest"] if ref_row and ref_row["latest"] else None
        cutoff = None
        if ref_date:
            cutoff = ref_date - timedelta(days=int(round(2 * 365.25)))
        league_clause = ""
        params = {"team": team_name}
        if resolved:
            params["league_pattern"] = _league_group(resolved) + "%"
            league_clause = "AND m.league LIKE :league_pattern"
        if cutoff:
            params["cutoff"] = cutoff
            league_clause += " AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff"
        rows = session.execute(
            text(f"""
                WITH team_players AS (
                    SELECT mp.external_player_id::text AS external_id,
                           max(mp.name) AS name
                    FROM match_players mp
                    JOIN xttv_matches m ON m.id = mp.match_id
                    WHERE mp.external_player_id IS NOT NULL
                      AND (
                        (m.home_team = :team AND mp.side = 'home')
                        OR (m.away_team = :team AND mp.side = 'away')
                      )
                      {league_clause}
                    GROUP BY mp.external_player_id
                )
                SELECT tp.external_id AS id,
                       tp.name,
                       xp.rc_player_id,
                       snap.rc_rating,
                       snap.rc_deviation
                FROM team_players tp
                LEFT JOIN xttv_players xp ON xp.external_player_id = tp.external_id
                LEFT JOIN LATERAL (
                    SELECT rc_rating, rc_deviation
                    FROM player_rating_snapshots
                    WHERE player_id = xp.id AND source = 'ratingscentral'
                    ORDER BY observed_at DESC
                    LIMIT 1
                ) snap ON true
                ORDER BY tp.name
            """),
            params,
        ).mappings()
    players = [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "rc_matched": row["rc_player_id"] is not None,
            "rc_rating": float(row["rc_rating"]) if row["rc_rating"] is not None else None,
            "rc_deviation": float(row["rc_deviation"]) if row["rc_deviation"] is not None else None,
        }
        for row in rows
    ]
    return {
        "ok": True,
        "team": team_name,
        "season": _season_label(resolved) if resolved else None,
        "player_window_years": 2,
        "players": players,
    }

def analyze(own_ids,opponent_team,actual_opponent_ids=None,limit=25):
    own=[str(x) for x in own_ids]
    if len(own)!=4 or len(set(own))!=4: raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team: raise ValueError("opponent_team is required")
    actual=None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual)!=4 or len(set(actual))!=4): raise ValueError("exactly four different actual_opponent_ids are required")
    create_all(); db,ms=_load()
    try:
        names,overall,h2h=_stats(ms); unknown=[x for x in own if x not in names]
        if unknown: raise ValueError(f"unknown own player IDs: {unknown}")
        schedule=_schedule(ms)
        if actual is not None: scenarios=[(tuple(actual),1.0)]
        else:
            pred=predict_opponent_lineups(opponent_team,limit=limit); scenarios=[(tuple(x["player_ids"]),float(x.get("probability",1))) for x in pred.get("predictions",[])]
        if not scenarios: return {"ok":True,"recommendations":[],"warnings":["No opponent lineup scenarios available."]}
        results=[]
        for order in permutations(own):
            total=0
            for opp,weight in scenarios:
                for oo in permutations(opp):
                    singles=[_prob(order[h],oo[a],overall,h2h) for h,a in schedule]
                    doubles=[_pair_prob(oo[0],oo[1],order[0],order[1],overall),_pair_prob(oo[2],oo[3],order[2],order[3],overall)]
                    total += weight*_team_win(singles,doubles)/24
            results.append({"player_ids":list(order),"players":[names[x] for x in order],"team_win_probability":round(total,6)})
        results.sort(key=lambda x:(-x["team_win_probability"],x["player_ids"]))
        return {"ok":True,"phase":"B" if actual else "A","opponent_team":opponent_team,"recommendation":results[0],"recommendations":results,"opponent_predictions":[{"player_ids":list(x),"probability":p} for x,p in scenarios],"model":{"version":"strength-h2h-doubles-v2","single_games":12,"doubles_games":2,"unseen_h2h":"overall-strength baseline","doubles":"individual-strength pair model"}}
    finally: db.close()
