from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import permutations
import math
import re
import time

from sqlalchemy import bindparam, text
from .db import SessionLocal

WIN_TARGET = 8
SINGLE_GAMES = 12
RECENCY_HALF_LIFE_DAYS = 45.0


def _key(player_id, name):
    return str(player_id or f"name:{name}")


def _position_index(position):
    if not position: return None
    value = str(position).strip().upper()
    if value in "ABCD": return ord(value) - 65
    if value in {"1", "2", "3", "4"}: return int(value) - 1
    return None


def _parse_date(value):
    if not value: return None
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    value = str(value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try: return datetime.strptime(value[:16], fmt).date()
        except ValueError: pass
    return None


def _recency_weight(match_date, reference_date):
    parsed = _parse_date(match_date)
    if not parsed or not reference_date: return 1.0
    return math.pow(0.5, max(0, (reference_date - parsed).days) / RECENCY_HALF_LIFE_DAYS)


def _matchup_probability(a,b,overall,h2h):
    aw,ag=overall.get(a,(0,0)); bw,bg=overall.get(b,(0,0))
    astr=(aw+5.0)/(ag+10.0); bstr=(bw+5.0)/(bg+10.0)
    base=1.0/(1.0+math.exp(-7.0*(astr-bstr)))
    wins,games=h2h.get((a,b),(0,0))
    if not games: return base
    direct=(wins+2.0)/(games+4.0); weight=min(0.55,games/12.0)
    return (1.0-weight)*base+weight*direct


def _pair_probability(a,b,c,d,overall):
    def strength(pid):
        w,g=overall.get(pid,(0,0)); return (w+5.0)/(g+10.0)
    left=(strength(a)+strength(b))/2.0; right=(strength(c)+strength(d))/2.0
    return 1.0/(1.0+math.exp(-7.0*(left-right)))


def _team_win_probability(probs):
    distribution=[1.0]
    for p in probs:
        nxt=[0.0]*(len(distribution)+1)
        for wins,mass in enumerate(distribution):
            nxt[wins]+=mass*(1.0-p); nxt[wins+1]+=mass*p
        distribution=nxt
    return sum(distribution[WIN_TARGET:])


def _opponent_lineups(db,team,limit=24):
    rows=db.execute(text("""
        SELECT m.id,m.match_date,mp.external_player_id,mp.name,mp.position
        FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id
        WHERE ((m.home_team=:team AND mp.side='home') OR (m.away_team=:team AND mp.side='away'))
        ORDER BY m.match_date DESC,m.id DESC,mp.id
    """),{"team":team}).mappings()
    matches={}; names={}
    for r in rows:
        pos=_position_index(r["position"])
        if pos is None: continue
        mid=int(r["id"]); m=matches.setdefault(mid,{"date":r["match_date"],"positions":{}})
        pid=_key(r["external_player_id"],r["name"]); m["positions"][pos]=pid; names[pid]=r["name"]
    relevant=[m for m in matches.values() if len(m["positions"])==4 and len(set(m["positions"].values()))==4]
    if not relevant: return [],set()
    reference=max((_parse_date(x["date"]) for x in relevant),default=None)
    observed=Counter()
    for m in relevant: observed[tuple(m["positions"][i] for i in range(4))]+=_recency_weight(m["date"],reference)
    ranked=observed.most_common(limit); total=sum(v for _,v in ranked)
    return ([{"player_ids":list(o),"players":[{"id":p,"name":names[p]} for p in o],"probability":w/total} for o,w in ranked],set(names))


def _aggregate_stats(db,relevant_ids):
    """Return only aggregated player-vs-player rows, never the full game history."""
    ids=list(relevant_ids)
    if not ids: return {},defaultdict(lambda:[0,0]),defaultdict(lambda:[0,0]),0
    stmt=text("""
        SELECT hp.external_player_id AS home_id,hp.name AS home_name,
               ap.external_player_id AS away_id,ap.name AS away_name,
               COUNT(*) AS games,
               SUM(CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END) AS home_wins
        FROM match_games g
        JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
        JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
        WHERE g.game_type='singles'
          AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
          AND (hp.external_player_id IN :ids OR ap.external_player_id IN :ids)
        GROUP BY hp.external_player_id,hp.name,ap.external_player_id,ap.name
    """).bindparams(bindparam("ids",expanding=True))
    rows=list(db.execute(stmt,{"ids":ids}).mappings())
    selected=set(ids); names={}; overall=defaultdict(lambda:[0,0]); h2h=defaultdict(lambda:[0,0])
    for r in rows:
        h=_key(r["home_id"],r["home_name"]); a=_key(r["away_id"],r["away_name"])
        names[h]=r["home_name"]; names[a]=r["away_name"]
        games=int(r["games"] or 0); hw=int(r["home_wins"] or 0); aw=games-hw
        if h in selected: overall[h][0]+=hw; overall[h][1]+=games
        if a in selected: overall[a][0]+=aw; overall[a][1]+=games
        if h in selected and a in selected:
            h2h[(h,a)][0]+=hw; h2h[(h,a)][1]+=games
            h2h[(a,h)][0]+=aw; h2h[(a,h)][1]+=games
    return names,overall,h2h,len(rows)


def _actual_opponent_positions(db,player_ids):
    """Infer secret positions from historical four-player appearances."""
    stmt=text("""
        SELECT m.id,mp.side,mp.external_player_id,mp.position
        FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id
        WHERE mp.external_player_id IN :ids
    """).bindparams(bindparam("ids",expanding=True))
    rows=db.execute(stmt,{"ids":list(player_ids)}).mappings(); selected=set(player_ids); matches={}
    for r in rows:
        pid=str(r["external_player_id"]); pos=_position_index(r["position"])
        if pid in selected and pos is not None: matches.setdefault((int(r["id"]),r["side"]),{})[pid]=pos
    observed=Counter()
    for ps in matches.values():
        if len(ps)!=4 or len(set(ps.values()))!=4: continue
        order=[None]*4
        for pid,pos in ps.items(): order[pos]=pid
        observed[tuple(order)]+=1
    if not observed: return [(tuple(o),1/24) for o in permutations(player_ids)]
    total=sum(observed.values()); return [(o,c/total) for o,c in observed.items()]


def analyze_lineup(own_player_ids,opponent_team,actual_opponent_ids=None,opponent_limit=24):
    started=time.monotonic(); own=[str(x) for x in own_player_ids]
    if len(own)!=4 or len(set(own))!=4: raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team: raise ValueError("opponent_team is required")
    actual=None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual)!=4 or len(set(actual))!=4): raise ValueError("exactly four different actual_opponent_ids are required")
    db=SessionLocal()
    try:
        if actual is not None:
            scenarios=[{"player_ids":actual,"probability":1.0}]; opponent_ids=set(actual); mode="actual"
        else:
            scenarios,opponent_ids=_opponent_lineups(db,opponent_team,24)
            if not scenarios: return {"ok":True,"phase":"A","recommendations":[],"warnings":["Keine historische Viereraufstellung für diesen Gegner gefunden."]}
            mode="predicted"
        relevant_ids=set(own)|set(opponent_ids)
        names,overall,h2h,aggregate_rows=_aggregate_stats(db,relevant_ids)
        for pid in relevant_ids: names.setdefault(pid,pid)
        # Fixed order: 12 singles, exactly two singles per player, plus 2 doubles.
        schedule=[(0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1)]
        if mode=="actual":
            scenario_rows=[(p,o) for o,p in _actual_opponent_positions(db,actual)]
        else:
            # Historical opponent position orders are already known here.
            scenario_rows=[(float(s["probability"]),tuple(s["player_ids"])) for s in scenarios]
        evaluated=[]
        for own_order in permutations(own):
            weighted=0.0
            for scenario_p,opp_order in scenario_rows:
                singles=[_matchup_probability(own_order[h],opp_order[a],overall,h2h) for h,a in schedule]
                doubles=[_pair_probability(own_order[0],own_order[1],opp_order[0],opp_order[1],overall),_pair_probability(own_order[2],own_order[3],opp_order[2],opp_order[3],overall)]
                weighted+=scenario_p*_team_win_probability(singles+doubles)
            evaluated.append({"own_player_ids":list(own_order),"players":[names.get(pid,pid) for pid in own_order],"team_win_probability":round(weighted,6)})
        evaluated.sort(key=lambda x:(-x["team_win_probability"],x["own_player_ids"]))
        for rank,item in enumerate(evaluated,1): item["rank"]=rank
        elapsed=time.monotonic()-started
        return {"ok":True,"phase":"B" if actual is not None else "A","opponent_team":opponent_team,"own_player_ids":own,"opponent_set_source":mode,"recommendation":evaluated[0],"recommendations":evaluated,"opponent_predictions":scenarios,"model":{"version":"strength-h2h-doubles-v8-db-aggregate","win_target":WIN_TARGET,"single_games":SINGLE_GAMES,"doubles_games":2,"opponent_lineups":"observed position orders","actual_opponent_positions":"historical distribution"},"data_quality":{"aggregate_pair_rows":aggregate_rows,"scenario_variants":len(scenario_rows),"own_orders_evaluated":24,"elapsed_seconds":round(elapsed,3)}}
    finally:
        db.close()
