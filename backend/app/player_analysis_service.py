from __future__ import annotations
from collections import defaultdict
from itertools import permutations
import math, re
from sqlalchemy.orm import selectinload
from .db import SessionLocal, create_all
from .models import MatchPlayer, XttvMatch
from .opponent_prediction_service import predict_opponent_lineups

WIN_TARGET = 8

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

def list_teams():
    create_all(); db,ms=_load()
    try:
        teams={}
        for m in ms:
            for side,name in (("home",m.home_team),("away",m.away_team)):
                if name: teams.setdefault(name,set()).update(_id(p) for p in m.players if p.side==side)
        return {"ok":True,"teams":[{"id":n,"name":n,"player_count":len(ids)} for n,ids in sorted(teams.items())]}
    finally: db.close()

def list_players(team_name):
    create_all(); db,ms=_load()
    try:
        names,overall,_=_stats(ms); ids=set()
        for m in ms:
            if m.home_team==team_name: ids.update(_id(p) for p in m.players if p.side=="home")
            if m.away_team==team_name: ids.update(_id(p) for p in m.players if p.side=="away")
        return {"ok":True,"team":team_name,"players":[{"id":pid,"name":names.get(pid,pid),"games":overall.get(pid,[0,0])[1],"win_rate":round(_strength(pid,overall),4)} for pid in sorted(ids,key=lambda x:names.get(x,x))]}
    finally: db.close()

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
