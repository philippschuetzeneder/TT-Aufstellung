from __future__ import annotations
import html as html_lib
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup
from .db import SessionLocal, create_all
from .models import RcPlayerIndex, XttvPlayer

RC_BASE="https://www.ratingscentral.com"
USER_AGENT="TT-Aufstellung/0.1 (+public RatingsCentral player index)"

def clean_text(v:str)->str:
    v=unicodedata.normalize("NFKC",v or "")
    return "".join(c for c in v if unicodedata.category(c) not in {"Cf","Cc"} or c in "\t\n\r").strip()

def norm(v:str)->str:
    v=clean_text(v).casefold().translate(str.maketrans({"ä":"a","ö":"o","ü":"u","ß":"ss"}))
    return " ".join(re.sub(r"[^a-z0-9]+"," ",v).split())

def _parse_rows(html:str)->list[dict]:
    soup=BeautifulSoup(html,"html.parser"); out={}
    def add(i,n,extra=None):
        n=clean_text(html_lib.unescape(n))
        if i and n and "," in n and len(n)<=160: out[int(i)]={"rc_player_id":int(i),"name":n,"name_norm":norm(n),"cells":extra or [n]}
    for row in soup.find_all("tr"):
        cells=[clean_text(" ".join(c.stripped_strings)) for c in row.find_all(["th","td"])]
        if len(cells)>=2 and re.fullmatch(r"\d+",cells[0] or "") and "," in cells[1]: add(cells[0],cells[1],cells)
    for a in soup.find_all("a",href=True):
        m=re.search(r"[?&]PlayerID=(\d+)",a.get("href",""),re.I)
        if not m: continue
        i=int(m.group(1)); candidates=[clean_text(" ".join(a.stripped_strings))]
        for p in [a.parent,*list(a.parents)[:3]]: candidates.append(clean_text(" ".join(p.stripped_strings)) if p else "")
        n=next((x for x in candidates if "," in x and len(x)<=160),None)
        if n: add(i,n)
    if not out:
        for m in re.finditer(r"PlayerID=(\d+)[^<]{0,500}",html,re.I):
            frag=BeautifulSoup(m.group(0),"html.parser").get_text(" ",strip=True)
            n=re.search(r"([A-ZÀ-ÖØ-Ý][^<>\n]{1,80},\s*[A-Za-zÀ-ÿ][^<>\n]{1,80})",frag)
            if n: add(m.group(1),n.group(1))
    return list(out.values())

def parse_rc_players(html:str)->list[dict]: return _parse_rows(html)

def fetch_search(name_prefix:str)->tuple[str,str]:
    url=f"{RC_BASE}/PlayerSearch.php?{urlencode({'Name':name_prefix,'PlayerSport':'Table Tennis','Search':'Search'})}"
    req=Request(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html,application/xhtml+xml"})
    with urlopen(req,timeout=20) as r:return r.read().decode("utf-8",errors="replace"),url

def debug_search(surname:str)->dict:
    html,url=fetch_search(f"{surname.strip()},"); soup=BeautifulSoup(html,"html.parser"); players=parse_rc_players(html)
    links=[]
    for a in soup.find_all("a",href=True):
        m=re.search(r"[?&]PlayerID=(\d+)",a.get("href",""),re.I)
        if m: links.append({"rc_player_id":int(m.group(1)),"text":clean_text(" ".join(a.stripped_strings)),"href":a.get("href")})
    return {"ok":True,"surname":surname,"url":url,"html_bytes":len(html.encode()),"table_count":len(soup.find_all("table")),"player_link_count":len(links),"player_links":links[:50],"players":players[:50]}

def _surname(name:str)->str:
    return next((p for p in re.split(r"[\s,]+",clean_text(name)) if p),"")

def import_index(limit:int=30,offset:int=0,force:bool=False)->dict:
    create_all()
    with SessionLocal() as s: players=s.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all(); surnames=sorted({_surname(p.name) for p in players if _surname(p.name)})
    results=[]; fetched=stored=0
    for surname in surnames:
        key=f"surname:{norm(surname)}"
        with SessionLocal() as s:
            cached=s.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
            if cached and not force: results.append({"search_key":key,"surname":surname,"status":"cached","players":cached.player_count}); continue
        try:
            html,url=fetch_search(f"{surname},"); found=parse_rc_players(html); fetched+=1
            with SessionLocal.begin() as s:
                e=s.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
                if e is None:e=RcPlayerIndex(search_key=key);s.add(e)
                e.url=url;e.fetched_at=datetime.utcnow();e.player_count=len(found);e.players_json=found;stored+=len(found)
            results.append({"search_key":key,"surname":surname,"status":"fetched","players":len(found)})
        except Exception as exc: results.append({"search_key":key,"surname":surname,"status":"error","error":f"{type(exc).__name__}: {exc}"})
    return {"ok":True,"mode":"rc_index","offset":offset,"limit":limit,"requested_players":len(players),"unique_surnames":len(surnames),"requests_made":fetched,"candidate_rows_stored":stored,"results":results}

def local_candidates(name:str,limit:int=20)->list[dict]:
    key=f"surname:{norm(_surname(name))}"
    with SessionLocal() as s:
        e=s.query(RcPlayerIndex).filter_by(search_key=key).one_or_none();return list(e.players_json or [])[:limit] if e else []
