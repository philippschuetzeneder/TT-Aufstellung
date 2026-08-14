from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations, permutations
import math
import re
import time

from sqlalchemy import text
from .db import SessionLocal

MAX_SINGLE_GAMES = 12
DOUBLES_GAMES = 2
WIN_TARGET = 8
CALCULATION_BUDGET_SECONDS = 19.8
RECENCY_HALF_LIFE_DAYS = 45.0
LINEUP_SMOOTHING = 0.25

# Optimized analysis: one SQL snapshot, then all calculations are in-memory.

def _key(player_id, name): return str(player_id or f"name:{name}")

def _position_index(position):
    if not position: return None
    value = str(position).strip().upper()
    if value in "ABCD": return ord(value) - 65
    if value in {"1", "2", "3", "4"}: return int(value) - 1
    return None

def _score(result):
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    return (int(m.group(1)), int(m.group(2))) if m else None

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

def _load_snapshot(db):
    """Exactly one SQL query; all analysis afterwards is in memory."""
    result = db.execute(text("""
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team,
               mp.id AS player_row_id, mp.name AS player_name, mp.external_player_id,
               mp.side AS player_side, mp.position AS player_position,
               g.id AS game_id, g.sequence, g.game_type, g.home_position,
               g.away_position, g.result
        FROM xttv_matches m
        LEFT JOIN match_players mp ON mp.match_id = m.id
        LEFT JOIN match_games g ON g.match_id = m.id
        ORDER BY m.match_date, m.id, mp.id, g.sequence, g.id
    """))
    matches = {}
    for row in result.mappings():
        mid = int(row["match_id"])
        match = matches.setdefault(mid, {"id": mid, "match_date": row["match_date"], "home_team": row["home_team"], "away_team": row["away_team"], "players": {}, "games": {}})
        if row["player_row_id"] is not None:
            pid = int(row["player_row_id"])
            match["players"][pid] = {"id": _key(row["external_player_id"], row["player_name"]), "name": row["player_name"], "external_player_id": row["external_player_id"], "side": row["player_side"], "position": row["player_position"]}
        if row["game_id"] is not None:
            gid = int(row["game_id"])
            match["games"][gid] = {"sequence": row["sequence"], "game_type": row["game_type"], "home_position": row["home_position"], "away_position": row["away_position"], "result": row["result"]}
    return [{**m, "players": list(m["players"].values()), "games": list(m["games"].values())} for m in matches.values()]

def _build_stats(matches):
    overall = defaultdict(lambda: [0, 0]); h2h = defaultdict(lambda: [0, 0]); names = {}
    for match in matches:
        by_pos = {(p["side"], p["position"]): p for p in match["players"] if p["position"]}
        for p in match["players"]: names[p["id"]] = p["name"]
        for game in match["games"]:
            if game["game_type"] != "singles": continue
            score = _score(game["result"])
            if not score or score[0] == score[1]: continue
            home = by_pos.get(("home", game["home_position"])); away = by_pos.get(("away", game["away_position"]))
            if not home or not away: continue
            a, b = home["id"], away["id"]
            overall[a][1] += 1; overall[b][1] += 1; h2h[(a,b)][1] += 1; h2h[(b,a)][1] += 1
            if score[0] > score[1]: overall[a][0] += 1; h2h[(a,b)][0] += 1
            else: overall[b][0] += 1; h2h[(b,a)][0] += 1
    return names, overall, h2h

def _strength(pid, overall):
    wins, games = overall.get(pid, (0, 0)); return (wins + 5.0) / (games + 10.0)

def _matchup_probability(a, b, overall, h2h):
    base = 1.0 / (1.0 + math.exp(-7.0 * (_strength(a, overall) - _strength(b, overall))))
    wins, games = h2h.get((a,b), (0,0))
    if not games: return base
    direct = (wins + 2.0) / (games + 4.0); weight = min(0.55, games / 12.0)
    return (1.0-weight)*base + weight*direct

def _pair_probability(a,b,c,d,overall):
    left = (_strength(a,overall)+_strength(b,overall))/2.0; right = (_strength(c,overall)+_strength(d,overall))/2.0
    return 1.0/(1.0+math.exp(-7.0*(left-right)))

def _single_schedule(matches):
    counts = Counter()
    for match in matches:
        for game in match["games"]:
            if game["game_type"] != "singles" or game["sequence"] is None: continue
            seq = int(game["sequence"])
            if not 1 <= seq <= 14 or seq in (5,10): continue
            h, a = _position_index(game["home_position"]), _position_index(game["away_position"])
            if h is not None and a is not None: counts[(seq,h,a)] += 1
    schedule=[]
    for seq in range(1,15):
        if seq in (5,10): continue
        candidates=[x for x in counts if x[0]==seq]
        if candidates:
            best=max(candidates,key=lambda x: counts[x]); schedule.append((best[1],best[2]))
    return schedule[:12] if len(schedule)>=12 else [(0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1)]

def _team_win_probability(single_probabilities, doubles):
    distribution=[1.0]
    for p in (*single_probabilities,*doubles):
        nxt=[0.0]*(len(distribution)+1)
        for wins,mass in enumerate(distribution):
            nxt[wins]+=mass*(1.0-p); nxt[wins+1]+=mass*p
        distribution=nxt
    return sum(distribution[WIN_TARGET:])

def _team_lineups(matches, team):
    exact=Counter(); player_weighted=Counter(); pair_weighted=Counter(); names={}; relevant=[]; dates=[]
    for match in matches:
        for side, team_name in (("home",match["home_team"]),("away",match["away_team"])):
            if team_name != team: continue
            players=[p for p in match["players"] if p["side"]==side and _position_index(p["position"]) is not None]
            ids=tuple(sorted({p["id"] for p in players}))
            if len(ids)!=4: continue
            relevant.append((match,ids)); names.update({p["id"]:p["name"] for p in players})
            parsed=_parse_date(match["match_date"])
            if parsed: dates.append(parsed)
    reference=max(dates,default=None); total_weight=0.0
    for match,ids in relevant:
        weight=_recency_weight(match["match_date"],reference); total_weight+=weight; exact[ids]+=weight
        for pid in ids: player_weighted[pid]+=weight
        for pair in combinations(ids,2): pair_weighted[pair]+=weight
    if total_weight==0: return [],len(relevant),names
    candidates=sorted(names)
    if len(candidates)<4: return [],len(relevant),names
    scored=[]
    for lineup in combinations(candidates,4):
        exact_weight=exact[lineup]
        inclusion=sum(player_weighted[p]/total_weight for p in lineup)/4.0
        cohesion=sum(pair_weighted[pair]/total_weight for pair in combinations(lineup,2))/6.0
        score=exact_weight+LINEUP_SMOOTHING*(0.5*inclusion+0.5*cohesion) if exact_weight else LINEUP_SMOOTHING*(0.55*inclusion+0.45*cohesion)
        scored.append((lineup,score))
    selected=sorted(scored,key=lambda x:(-x[1],x[0]))[:25]; total=sum(x[1] for x in selected)
    if not total: return [],len(relevant),names
    return ([{"player_ids":list(lineup),"players":[{"id":pid,"name":names[pid]} for pid in lineup],"probability":score/total} for lineup,score in selected],len(relevant),names)

def _position_variants(matches, player_ids):
    selected=set(player_ids); position_counts=Counter(); player_counts=Counter()
    for match in matches:
        for side in ("home","away"):
            players=[p for p in match["players"] if p["side"]==side and p["id"] in selected]
            if len(players)!=4 or len({p["id"] for p in players})!=4: continue
            for p in players:
                pos=_position_index(p["position"])
                if pos is not None: position_counts[(p["id"],pos)]+=1; player_counts[p["id"]]+=1
    scored=[]
    for order in permutations(player_ids):
        score=1.0
        for pos,pid in enumerate(order): score*=(position_counts[(pid,pos)]+0.5)/(player_counts[pid]+2.0)
        scored.append((order,score))
    total=sum(x[1] for x in scored)
    if total<=0: return [(tuple(order),1.0/24.0) for order,_ in scored]
    return [(tuple(order),score/total) for order,score in scored]

def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=25):
    started=time.monotonic(); own=[str(pid) for pid in own_player_ids]
    if len(own)!=4 or len(set(own))!=4: raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team: raise ValueError("opponent_team is required")
    actual=None if actual_opponent_ids is None else [str(pid) for pid in actual_opponent_ids]
    if actual is not None and (len(actual)!=4 or len(set(actual))!=4): raise ValueError("exactly four different actual_opponent_ids are required")
    if opponent_limit<1 or opponent_limit>100: raise ValueError("opponent_limit must be between 1 and 100")

    db=SessionLocal()
    try: matches=_load_snapshot(db)
    finally: db.close()

    names,overall,h2h=_build_stats(matches)
    missing=[pid for pid in own if pid not in names]
    if missing: raise ValueError(f"unknown own player IDs: {missing}")
    schedule=_single_schedule(matches)
    if actual is not None:
        scenarios=[{"player_ids":actual,"probability":1.0}]; source="actual"
    else:
        scenarios,_,_=_team_lineups(matches,opponent_team); scenarios=scenarios[:opponent_limit]; source="predicted"
        total=sum(float(x["probability"]) for x in scenarios)
        if total:
            for s in scenarios: s["probability"]=float(s["probability"])/total
    if not scenarios: return {"ok":True,"phase":"B" if actual is not None else "A","recommendations":[],"warnings":["No historical opponent lineups available for this team."]}

    relevant_ids=set(own)
    for s in scenarios: relevant_ids.update(s["player_ids"])
    matchup={(a,b):_matchup_probability(a,b,overall,h2h) for a in relevant_ids for b in relevant_ids if a!=b}

    # Precompute the complete opponent scenario set ONCE and cache position variants.
    scenario_rows=[]
    position_cache={}
    for s in scenarios:
        key=tuple(sorted(s["player_ids"]))
        variants=position_cache.get(key)
        if variants is None:
            variants=_position_variants(matches,key)
            position_cache[key]=variants
        set_p=float(s["probability"])
        for opponent_order,pos_p in variants:
            scenario_rows.append((set_p*pos_p,opponent_order))

    # Cache matchup-derived values for the hot loop.
    double_cache={}
    single_cache={}
    evaluated=[]
    scenario_counter=0
    for own_order in permutations(own):
        weighted=0.0
        for scenario_p,opponent_order in scenario_rows:
            pair_key=(own_order,opponent_order)
            doubles=double_cache.get(pair_key)
            if doubles is None:
                doubles=(_pair_probability(own_order[0],own_order[1],opponent_order[0],opponent_order[1],overall), _pair_probability(own_order[2],own_order[3],opponent_order[2],opponent_order[3],overall))
                double_cache[pair_key]=doubles
            singles=single_cache.get(pair_key)
            if singles is None:
                singles=[matchup.get((own_order[h],opponent_order[a]),0.5) for h,a in schedule]
                single_cache[pair_key]=singles
            weighted += scenario_p*_team_win_probability(singles,doubles)
            scenario_counter+=1
        evaluated.append({"own_player_ids":list(own_order),"players":[names[pid] for pid in own_order],"team_win_probability":weighted})

        if time.monotonic()-started > CALCULATION_BUDGET_SECONDS:
            return {"ok":False,"error":"analysis_timeout","message":"Die Berechnung konnte innerhalb des 20-Sekunden-Limits nicht abgeschlossen werden.","elapsed_seconds":round(time.monotonic()-started,3)}

    evaluated.sort(key=lambda x:(-x["team_win_probability"],x["own_player_ids"]))
    for rank,item in enumerate(evaluated,1):
        item["rank"]=rank; item["team_win_probability"]=round(item["team_win_probability"],6)
    elapsed=time.monotonic()-started
    if elapsed>CALCULATION_BUDGET_SECONDS:
        return {"ok":False,"error":"analysis_timeout","message":"Die Berechnung konnte innerhalb des 20-Sekunden-Limits nicht abgeschlossen werden.","elapsed_seconds":round(elapsed,3)}
    return {"ok":True,"phase":"B" if actual is not None else "A","opponent_team":opponent_team,"own_player_ids":own,"opponent_set_source":source,"recommendation":evaluated[0],"recommendations":evaluated,"opponent_predictions":scenarios,"model":{"version":"strength-h2h-doubles-v4-single-query-precomputed","win_target":WIN_TARGET,"single_games":MAX_SINGLE_GAMES,"doubles_games":DOUBLES_GAMES,"unseen_h2h":"overall-strength baseline","doubles":"individual-strength pair model"},"data_quality":{"historical_matches":len(matches),"historical_matchup_pairs":len(h2h),"scenarios_evaluated":scenario_counter,"database_queries":1,"elapsed_seconds":round(elapsed,3)}}
