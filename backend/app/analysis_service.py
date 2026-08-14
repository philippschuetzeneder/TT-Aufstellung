from __future__ import annotations

from itertools import permutations
import math
import time
from sqlalchemy import text
from .db import SessionLocal
from .analysis_cache import ensure_analysis_cache

WIN_TARGET = 8
SINGLE_GAMES = 12
# The analysis itself is deliberately tiny. This is a safety guard, not a normal
# timeout: a valid 4-vs-4 calculation should finish far below this value.
MAX_ANALYSIS_SECONDS = 3.0


def _strength(wins, games):
    return (wins + 5.0) / (games + 10.0)


def _matchup_probability(a, b, stats, matchups):
    aw, ag = stats.get(a, (0, 0)); bw, bg = stats.get(b, (0, 0))
    base = 1.0 / (1.0 + math.exp(-7.0 * (_strength(aw, ag) - _strength(bw, bg))))
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 2.0) / (games + 4.0)
    weight = min(0.55, games / 12.0)
    return (1.0 - weight) * base + weight * direct


def _pair_probability(a, b, c, d, stats):
    left = (_strength(*stats.get(a, (0, 0))) + _strength(*stats.get(b, (0, 0)))) / 2.0
    right = (_strength(*stats.get(c, (0, 0))) + _strength(*stats.get(d, (0, 0)))) / 2.0
    return 1.0 / (1.0 + math.exp(-7.0 * (left - right)))


def _team_win_probability(probs):
    distribution = [1.0]
    for p in probs:
        nxt = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        distribution = nxt
    return sum(distribution[WIN_TARGET:])


def _load_cache(own, opponent_team, actual):
    """Load only the tiny slice needed for this analysis.

    The old implementation selected every player statistic and every matchup in
    the database. That defeated the purpose of the cache on a large XTTV import.
    """
    ensure_analysis_cache()
    db = SessionLocal()
    try:
        # PostgreSQL must never leave an analysis request waiting indefinitely.
        db.execute(text("SET LOCAL statement_timeout = '1500ms'"))

        if actual is not None:
            lineup_key = ",".join(sorted(actual))
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders
                WHERE lineup_key=:key ORDER BY appearances DESC LIMIT 24
            """), {"key": lineup_key}).mappings())
            scenarios=[]
            total=sum(int(r["appearances"]) for r in rows)
            if total:
                scenarios=[(int(r["appearances"])/total,(str(r["p1"]),str(r["p2"]),str(r["p3"]),str(r["p4"]))) for r in rows]
            if not scenarios:
                scenarios=[(1.0/24.0,tuple(o)) for o in permutations(actual)]
            relevant=set(own)|set(actual)
            source="actual"
        else:
            rows=list(db.execute(text("""
                SELECT lineup_key,p1,p2,p3,p4,appearances FROM analysis_lineup_orders
                WHERE team=:team ORDER BY appearances DESC LIMIT 24
            """), {"team": opponent_team}).mappings())
            total=sum(int(r["appearances"]) for r in rows)
            scenarios=[(int(r["appearances"])/total,(str(r["p1"]),str(r["p2"]),str(r["p3"]),str(r["p4"]))) for r in rows] if total else []
            relevant=set(own)
            for _,order in scenarios: relevant.update(order)
            source="predicted"
            if not scenarios:
                return {},{}, {}, [], source

        ids=list(relevant)
        if not ids:
            return {},{}, {}, [], source
        # Parameterized ANY queries keep the DB work proportional to the 8-ish
        # players involved, regardless of the total XTTV database size.
        stats={}; names={}; matchups={}
        stat_rows=db.execute(text("""
            SELECT player_id,player_name,singles_wins,singles_games
            FROM analysis_player_stats WHERE player_id = ANY(:ids)
        """), {"ids":ids}).mappings()
        for r in stat_rows:
            pid=str(r["player_id"]); names[pid]=r["player_name"]; stats[pid]=(int(r["singles_wins"]),int(r["singles_games"]))
        matchup_rows=db.execute(text("""
            SELECT player_id,opponent_id,wins,games
            FROM analysis_matchups
            WHERE player_id = ANY(:ids) AND opponent_id = ANY(:ids)
        """), {"ids":ids}).mappings()
        for r in matchup_rows:
            matchups[(str(r["player_id"]),str(r["opponent_id"]))]=(int(r["wins"]),int(r["games"]))
        return names,stats,matchups,scenarios,source
    finally:
        db.close()


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24):
    started=time.monotonic()
    own=[str(x) for x in own_player_ids]
    if len(own)!=4 or len(set(own))!=4: raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team: raise ValueError("opponent_team is required")
    actual=None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual)!=4 or len(set(actual))!=4): raise ValueError("exactly four different actual_opponent_ids are required")

    names,stats,matchups,scenarios,source=_load_cache(own,opponent_team,actual)
    missing=[p for p in own if p not in names]
    if missing:
        # Keep the API explicit rather than waiting for a full historical scan.
        raise ValueError(f"Spielerstatistik fehlt für XTTV-IDs: {', '.join(missing)}. Analysis-Cache bitte aktualisieren.")
    if not scenarios:
        return {"ok":True,"phase":"B" if actual is not None else "A","recommendations":[],"warnings":["Keine historische Viereraufstellung für diesen Gegner gefunden."]}

    schedule=((0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1))
    # Precompute all 16 player matchup probabilities once.
    relevant=set(own)
    for _,o in scenarios: relevant.update(o)
    matchup_p={(a,b):_matchup_probability(a,b,stats,matchups) for a in relevant for b in relevant if a!=b}
    evaluated=[]
    for own_order in permutations(own):
        expected=0.0
        for scenario_probability,opp_order in scenarios:
            singles=[matchup_p.get((own_order[h],opp_order[a]),0.5) for h,a in schedule]
            doubles=[_pair_probability(own_order[0],own_order[1],opp_order[0],opp_order[1],stats),_pair_probability(own_order[2],own_order[3],opp_order[2],opp_order[3],stats)]
            expected += scenario_probability*_team_win_probability(singles+doubles)
        evaluated.append({"own_player_ids":list(own_order),"players":[names.get(pid,pid) for pid in own_order],"team_win_probability":round(expected,6)})
        if time.monotonic()-started>MAX_ANALYSIS_SECONDS:
            raise RuntimeError("Analysis exceeded the internal 3-second safety budget")

    evaluated.sort(key=lambda x:(-x["team_win_probability"],x["own_player_ids"]))
    for rank,item in enumerate(evaluated,1): item["rank"]=rank
    elapsed=time.monotonic()-started
    return {"ok":True,"phase":"B" if actual is not None else "A","opponent_team":opponent_team,"own_player_ids":own,"opponent_set_source":source,"recommendation":evaluated[0],"recommendations":evaluated,"opponent_predictions":[{"player_ids":list(o),"players":[{"id":p,"name":names.get(p,p)} for p in o],"probability":p} for p,o in scenarios],"model":{"version":"strength-h2h-doubles-v10-targeted-cache","win_target":WIN_TARGET,"single_games":SINGLE_GAMES,"doubles_games":2,"opponent_lineups":"observed historical position orders","actual_opponent_positions":"historical distribution; all 24 permutations if unseen"},"data_quality":{"scenario_variants":len(scenarios),"own_orders_evaluated":24,"runtime_seconds":round(elapsed,4),"runtime_data_source":"targeted-analysis-cache"}}
