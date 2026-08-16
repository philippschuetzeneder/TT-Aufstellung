from __future__ import annotations
import json, os, socket, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from sqlalchemy import or_
from .analytics_service import lineup_stats, matchup_matrix, matchup_stats, player_stats
from .analytics_validation_service import validate_analytics
from .analysis_service import analyze_lineup
from .analysis_cache import start_background_refresh, refresh_analysis_cache
from .player_analysis_service import list_players, list_teams
from .db import database_health, SessionLocal
from .db_routes import get_match
from .models import XttvPlayer
from .validation_service import validate_database
from .xttv_import import MATCH_URL, fetch_match, inspect_html
from .xttv_db_import import DEFAULT_LIMIT, DEFAULT_RADIUS, REFERENCE_MEID, import_one, scan_and_import
from .xttv_parser import parse_match
from .rc_import import import_rc_player
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

def find_xttv_players(name: str | None, team: str | None):
    with SessionLocal() as session:
        query = session.query(XttvPlayer)
        if name:
            normalized = " ".join(name.strip().split())
            parts = normalized.replace(",", " ").split()
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                query = query.filter(or_(XttvPlayer.name.ilike(f"%{normalized}%"), XttvPlayer.name.ilike(f"%{first}%{last}%"), XttvPlayer.name.ilike(f"%{last}%{first}%")))
            else:
                query = query.filter(XttvPlayer.name.ilike(f"%{normalized}%"))
        if team:
            query = query.filter(XttvPlayer.club.ilike(f"%{team.strip()}%"))
        rows = query.order_by(XttvPlayer.name).limit(50).all()
        return {"ok": True, "count": len(rows), "matches": [{"id": r.id, "external_player_id": r.external_player_id, "name": r.name, "club": r.club, "rc_player_id": r.rc_player_id} for r in rows]}

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
            if parsed.path=="/api/xttv/player-find": return self.send_json(find_xttv_players(query.get("name",[None])[0], query.get("team",[None])[0]))
            team_prefix="/api/teams/"
            if parsed.path.startswith(team_prefix) and parsed.path.endswith("/players"):
                team_name=unquote(parsed.path[len(team_prefix):-len("/players")]); return self.send_json(list_players(team_name))
            if parsed.path=="/api/analysis":
                own=[v.strip() for v in query.get("own_player_ids",[""])[0].split(",") if v.strip()]
                opponent=query.get("opponent_team",[""])[0].strip(); raw=query.get("actual_opponent_ids",[""])[0]; actual=[v.strip() for v in raw.split(",") if v.strip()] if raw else None
                return self.send_json(analyze_lineup(own,opponent,actual,int(query.get("opponent_limit",["24"])[0])))
            if parsed.path=="/api/xttv/debug": return self.send_json(debug_xttv(meid))
            if parsed.path=="/api/xttv/fetch": return self.send_json({"meid":meid,**http_fetch(MATCH_URL.format(meid=meid))})
            if parsed.path=="/api/xttv/inspect":
                html,status,content_type,url=fetch_match(meid); inspection=inspect_html(html); inspection.update({"meid":meid,"status":status,"content_type":content_type,"url":url}); return self.send_json(inspection)
            if parsed.path=="/api/xttv/parse":
                html,status,content_type,url=fetch_match(meid); return self.send_json({"meid":meid,"status":status,"content_type":content_type,"url":url,**parse_match(html,meid)})
            if parsed.path=="/api/xttv/import": return self.send_json({"meid":meid,**import_one(meid)})
            if parsed.path=="/api/xttv/scan-import":
                start=int(query.get("start",[str(REFERENCE_MEID-DEFAULT_RADIUS)])[0]); end=int(query.get("end",[str(REFERENCE_MEID+DEFAULT_RADIUS)])[0]); limit=int(query.get("limit",[str(DEFAULT_LIMIT)])[0]); delay=float(query.get("delay",["0.05"])[0]); return self.send_json(scan_and_import(start,end,limit=limit,delay=delay))
            if parsed.path=="/api/rc/import":
                raw_rc_id=query.get("player_id",[""])[0].strip()
                if not raw_rc_id or not raw_rc_id.isdigit(): return self.send_json({"ok":False,"error":"player_id must be a numeric RatingsCentral player ID"},400)
                return self.send_json(import_rc_player(int(raw_rc_id)))
        except Exception as exc: return self.send_json({"ok":False,"error":f"{type(exc).__name__}: {exc}"},500)
        rel="index.html" if parsed.path in ("","/") else parsed.path.lstrip("/"); target=(ROOT/rel).resolve()
        if not str(target).startswith(str(ROOT.resolve())) or not target.is_file(): return self.send_error(404)
        content=target.read_bytes(); types={".html":"text/html",".css":"text/css",".mjs":"text/javascript",".js":"text/javascript"}; self.send_response(200); self.send_header("Content-Type",types.get(target.suffix,"application/octet-stream")+"; charset=utf-8"); self.send_header("Content-Length",str(len(content))); self.end_headers(); self.wfile.write(content)

def main():
    start_background_refresh()
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),Handler).serve_forever()
if __name__=="__main__": main()
