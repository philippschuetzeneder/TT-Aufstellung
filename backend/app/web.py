from __future__ import annotations
import json, os, socket, urllib.request, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from bs4 import BeautifulSoup
from .analytics_service import lineup_stats, matchup_matrix, matchup_stats, player_stats
from .analytics_validation_service import validate_analytics
from .analysis_service import analyze_lineup
from .analysis_cache import start_background_refresh, refresh_analysis_cache
from .player_analysis_service import list_players, list_teams
from .db import database_health, SessionLocal
from .db_routes import get_match
from .validation_service import validate_database
from .xttv_import import MATCH_URL, fetch_match, inspect_html
from .xttv_db_import import DEFAULT_LIMIT, DEFAULT_RADIUS, REFERENCE_MEID, import_one, scan_and_import, rebuild_player_master
from .xttv_parser import parse_match
from .rc_import import import_rc_player, fetch_player_history, parse_player_history, bulk_import_rc
from .rc_matching import dry_run as rc_matching_dry_run
from .rc_index import import_index as rc_index_import, debug_search as rc_index_debug_search
from .rc_events import debug_event as rc_event_debug
from .models import XttvPlayer, PlayerRatingSnapshot
ROOT=Path(__file__).resolve().parents[2]

def http_fetch(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; TT-Aufstellung/0.1)","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"de-AT,de;q=0.9,en;q=0.5","Referer":"https://oettv.xttv.at/ed/index.php"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            body=r.read(); charset=r.headers.get_content_charset() or "iso-8859-1"; text=body.decode(charset,errors="replace")
            return {"ok":True,"status":r.status,"content_type":r.headers.get_content_type(),"bytes":len(body),"text_preview":text[:1000]}
    except Exception as exc: return {"ok":False,"error":f"{type(exc).__name__}: {exc}"}

def debug_xttv(meid):
    url=MATCH_URL.format(meid=meid); result={"meid":meid,"url":url,"checks":[]}
    for host in ("oettv.xttv.at","www.oettv.xttv.at","xttv.oettv.info"):
        try: result["checks"].append({"method":f"DNS {host}","ok":True,"addresses":sorted({x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})})
        except Exception as exc: result["checks"].append({"method":f"DNS {host}","ok":False,"error":f"{type(exc).__name__}: {exc}"})
    for label,candidate in [("direct HTTPS",url),("HTTP fallback",url.replace("https://","http://",1)),("www HTTPS",url.replace("https://oettv.xttv.at","https://www.oettv.xttv.at",1)),("legacy HTTPS",url.replace("https://oettv.xttv.at","https://xttv.oettv.info",1))]: result["checks"].append({"method":label,"url":candidate,**http_fetch(candidate)})
    result["direct_success"]=any(c.get("method")=="direct HTTPS" and c.get("ok") for c in result["checks"]); return result

def _norm(value: str) -> str:
    value=(value or "").lower(); value=value.replace("ä","a").replace("ö","o").replace("ü","u").replace("ß","ss"); return re.sub(r"[^a-z0-9]+","",value)

def find_xttv_players(name: str | None, team: str | None):
    with SessionLocal() as session: rows=session.query(XttvPlayer).order_by(XttvPlayer.name).all()
    normalized_name=_norm(name or ""); name_tokens=[_norm(t) for t in re.split(r"[\s,]+",name or "") if _norm(t)]; normalized_team=_norm(team or ""); matches=[]
    for r in rows:
        candidate_name=_norm(r.name); candidate_club=_norm(r.club or ""); score=0
        if normalized_name and candidate_name==normalized_name: score+=100
        elif normalized_name and all(t in candidate_name for t in name_tokens): score+=60
        if normalized_team:
            if normalized_team in candidate_club: score+=30
            elif normalized_team=="tragwein" and ("tragwein" in candidate_club or "kamig" in candidate_club or "trak" in candidate_club): score+=30
        if score: matches.append({"id":r.id,"external_player_id":r.external_player_id,"name":r.name,"club":r.club,"rc_player_id":r.rc_player_id,"score":score})
    matches.sort(key=lambda x:(-x["score"],x["name"])); return {"ok":True,"query":{"name":name,"team":team},"count":len(matches),"matches":matches[:20]}

def rc_history_debug(player_id:int):
    html,content_type=fetch_player_history(player_id); soup=BeautifulSoup(html,"html.parser"); tables=[]
    for ti,table in enumerate(soup.find_all("table")):
        rows=[]
        for row in table.find_all("tr")[:25]:
            cells=[" ".join(cell.stripped_strings) for cell in row.find_all(["th","td"])]
            if cells: rows.append(cells)
        tables.append({"index":ti,"rows":rows})
    parsed=None; parse_error=None
    try: parsed=parse_player_history(html)
    except Exception as exc: parse_error=f"{type(exc).__name__}: {exc}"
    return {"ok":True,"rc_player_id":player_id,"content_type":content_type,"html_bytes":len(html.encode("utf-8")),"table_count":len(tables),"tables":tables,"parsed":parsed,"parse_error":parse_error}

def rc_snapshot_check(player_id:int):
    with SessionLocal() as session:
        player=session.query(XttvPlayer).filter_by(rc_player_id=player_id).one_or_none()
        if player is None:return {"ok":False,"error":"No XTTV player mapped to RC player","rc_player_id":player_id}
        snapshots=session.query(PlayerRatingSnapshot).filter_by(player_id=player.id,source="ratingscentral").order_by(PlayerRatingSnapshot.observed_at.asc()).all(); dates=[s.observed_at.date().isoformat() for s in snapshots]; ratings=[s.rc_rating for s in snapshots]; deviations=[s.rc_deviation for s in snapshots]; unique_keys=len(set((s.observed_at,s.source) for s in snapshots))
        return {"ok":True,"player":{"db_id":player.id,"external_player_id":player.external_player_id,"rc_player_id":player.rc_player_id,"name":player.name,"club":player.club},"snapshot_count":len(snapshots),"unique_observation_keys":unique_keys,"first_observed_at":dates[0] if dates else None,"last_observed_at":dates[-1] if dates else None,"min_rating":min(ratings) if ratings else None,"max_rating":max(ratings) if ratings else None,"missing_deviation_count":sum(1 for v in deviations if v is None),"duplicate_dates":sorted({d for d in dates if dates.count(d)>1}),"first_5":[{"observed_at":s.observed_at.isoformat(),"rc_rating":s.rc_rating,"rc_deviation":s.rc_deviation} for s in snapshots[:5]],"last_5":[{"observed_at":s.observed_at.isoformat(),"rc_rating":s.rc_rating,"rc_deviation":s.rc_deviation} for s in snapshots[-5:]]}

class Handler(BaseHTTPRequestHandler):
    def send_json(self,payload,status=200):
        data=json.dumps(payload,ensure_ascii=False,default=str).encode("utf-8"); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed=urlparse(self.path); query=parse_qs(parsed.query)
        try: meid=int(query.get("meid",["437757"])[0])
        except ValueError: return self.send_json({"ok":False,"error":"meid must be an integer"},400)
        try:
            if parsed.path=="/health": return self.send_json({"ok":True})
            if parsed.path=="/api/db/health": return self.send_json(database_health())
            if parsed.path=="/api/db/validate": return self.send_json(validate_database())
            if parsed.path=="/api/analytics/validate": return self.send_json(validate_analytics())
            if parsed.path=="/api/db/match": return self.send_json(get_match(meid))
            if parsed.path=="/api/analytics/players": return self.send_json(player_stats())
            if parsed.path=="/api/analytics/lineups": return self.send_json(lineup_stats(query.get("team",[None])[0]))
            if parsed.path=="/api/analytics/matchups": return self.send_json(matchup_stats(query.get("player_id",[None])[0],query.get("opponent_id",[None])[0]))
            if parsed.path=="/api/analytics/matchup-matrix": return self.send_json(matchup_matrix())
            if parsed.path=="/api/analysis/cache-refresh": return self.send_json(refresh_analysis_cache())
            if parsed.path=="/api/teams": return self.send_json(list_teams())
            if parsed.path=="/api/teams/players": return self.send_json(list_players(query.get("team",[""])[0]))
            if parsed.path=="/api/xttv/player-find": return self.send_json(find_xttv_players(query.get("name",[None])[0],query.get("team",[None])[0]))
            if parsed.path=="/api/xttv/player-master-rebuild":
                try: limit=min(max(int(query.get("limit",["5000"])[0]),1),10000); offset=max(int(query.get("offset",["0"])[0]),0)
                except ValueError:return self.send_json({"ok":False,"error":"limit and offset must be integers"},400)
                return self.send_json(rebuild_player_master(limit=limit,offset=offset))
            if parsed.path=="/api/rc/events/debug":
                raw=query.get("event_id",[""])[0].strip()
                if not raw.isdigit(): return self.send_json({"ok":False,"error":"event_id must be numeric"},400)
                return self.send_json(rc_event_debug(int(raw)))
            if parsed.path=="/api/rc/index/debug-search":
                surname=query.get("surname",[""])[0].strip()
                if not surname:return self.send_json({"ok":False,"error":"surname is required"},400)
                return self.send_json(rc_index_debug_search(surname))
            if parsed.path=="/api/rc/index/import":
                try: limit=min(max(int(query.get("limit",["30"])[0]),1),500); offset=max(int(query.get("offset",["0"])[0]),0); force=query.get("force",["0"])[0] in {"1","true","yes"}
                except ValueError:return self.send_json({"ok":False,"error":"limit and offset must be integers"},400)
                return self.send_json(rc_index_import(limit=limit,offset=offset,force=force))
            if parsed.path=="/api/rc/debug-history":
                raw=query.get("player_id",[""])[0].strip()
                if not raw.isdigit():return self.send_json({"ok":False,"error":"player_id must be numeric"},400)
                return self.send_json(rc_history_debug(int(raw)))
            if parsed.path=="/api/rc/check":
                raw=query.get("player_id",[""])[0].strip()
                if not raw.isdigit():return self.send_json({"ok":False,"error":"player_id must be numeric"},400)
                return self.send_json(rc_snapshot_check(int(raw)))
            if parsed.path=="/api/rc/match-dry-run":
                try: limit=min(max(int(query.get("limit",["30"])[0]),1),100); offset=max(int(query.get("offset",["0"])[0]),0)
                except ValueError:return self.send_json({"ok":False,"error":"limit and offset must be integers"},400)
                return self.send_json(rc_matching_dry_run(limit=limit,offset=offset))
            if parsed.path=="/api/rc/bulk":
                try: limit=min(max(int(query.get("limit",["30"])[0]),1),100); offset=max(int(query.get("offset",["0"])[0]),0)
                except ValueError:return self.send_json({"ok":False,"error":"limit and offset must be integers"},400)
                return self.send_json(bulk_import_rc(limit=limit,offset=offset))
            team_prefix="/api/teams/"
            if parsed.path.startswith(team_prefix) and parsed.path.endswith("/players"):
                team_name=unquote(parsed.path[len(team_prefix):-len("/players")]); return self.send_json(list_players(team_name))
            if parsed.path=="/api/analysis":
                own=[v.strip() for v in query.get("own_player_ids",[""])[0].split(",") if v.strip()]; opponent=query.get("opponent_team",[""])[0].strip(); raw=query.get("actual_opponent_ids",[""])[0]; actual=[v.strip() for v in raw.split(",") if v.strip()] if raw else None
                return self.send_json(analyze_lineup(own,opponent,actual,int(query.get("opponent_limit",["24"])[0])))
            if parsed.path=="/api/xttv/debug":return self.send_json(debug_xttv(meid))
            if parsed.path=="/api/xttv/fetch":return self.send_json({"meid":meid,**http_fetch(MATCH_URL.format(meid=meid))})
            if parsed.path=="/api/xttv/inspect":
                html,status,content_type,url=fetch_match(meid); inspection=inspect_html(html); inspection.update({"meid":meid,"status":status,"content_type":content_type,"url":url}); return self.send_json(inspection)
            if parsed.path=="/api/xttv/parse":
                html,status,content_type,url=fetch_match(meid); return self.send_json({"meid":meid,"status":status,"content_type":content_type,"url":url,**parse_match(html,meid)})
            if parsed.path=="/api/xttv/import":return self.send_json({"meid":meid,**import_one(meid)})
            if parsed.path=="/api/xttv/scan-import":
                start=int(query.get("start",[str(REFERENCE_MEID-DEFAULT_RADIUS)])[0]); end=int(query.get("end",[str(REFERENCE_MEID+DEFAULT_RADIUS)])[0]); limit=int(query.get("limit",[str(DEFAULT_LIMIT)])[0]); delay=float(query.get("delay",["0.05"])[0]); return self.send_json(scan_and_import(start,end,limit=limit,delay=delay))
            if parsed.path=="/api/rc/import":
                raw_rc_id=query.get("player_id",[""])[0].strip()
                if not raw_rc_id or not raw_rc_id.isdigit():return self.send_json({"ok":False,"error":"player_id must be a numeric RatingsCentral player ID"},400)
                return self.send_json(import_rc_player(int(raw_rc_id),xttv_player_id=query.get("xttv_player_id",[None])[0],xttv_external_player_id=query.get("xttv_external_player_id",[None])[0],xttv_name=query.get("xttv_name",[None])[0],xttv_club=query.get("xttv_club",[None])[0]))
        except Exception as exc:return self.send_json({"ok":False,"error":f"{type(exc).__name__}: {exc}"},500)
        rel="index.html" if parsed.path in ("","/") else parsed.path.lstrip("/"); target=(ROOT/rel).resolve()
        if not str(target).startswith(str(ROOT.resolve())) or not target.is_file():return self.send_error(404)
        content=target.read_bytes(); types={".html":"text/html",".css":"text/css",".mjs":"text/javascript",".js":"text/javascript"}; self.send_response(200); self.send_header("Content-Type",types.get(target.suffix,"application/octet-stream")+"; charset=utf-8"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)

def main():
    start_background_refresh(); ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),Handler).serve_forever()
if __name__=="__main__":main()
