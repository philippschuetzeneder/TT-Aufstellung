from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .xttv_import import MATCH_URL

ROOT = Path(__file__).resolve().parents[2]


def http_fetch(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.1)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
            "Referer": "https://oettv.xttv.at/ed/index.php",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, errors="replace")
            return {"ok": True, "status": r.status, "content_type": r.headers.get_content_type(), "bytes": len(body), "text_preview": text[:1000]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def debug_xttv(meid: int) -> dict:
    url = MATCH_URL.format(meid=meid)
    result = {"meid": meid, "url": url, "checks": []}

    for host in ("oettv.xttv.at", "www.oettv.xttv.at", "xttv.oettv.info"):
        try:
            addresses = sorted({x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
            result["checks"].append({"method": f"DNS {host}", "ok": True, "addresses": addresses})
        except Exception as exc:
            result["checks"].append({"method": f"DNS {host}", "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    for label, candidate in [
        ("direct HTTPS", url),
        ("HTTP fallback", url.replace("https://", "http://", 1)),
        ("www HTTPS", url.replace("https://oettv.xttv.at", "https://www.oettv.xttv.at", 1)),
        ("legacy HTTPS", url.replace("https://oettv.xttv.at", "https://xttv.oettv.info", 1)),
    ]:
        result["checks"].append({"method": label, "url": candidate, **http_fetch(candidate)})

    # DNS-over-HTTPS is diagnostic only; if Render can reach Cloudflare/Google,
    # this distinguishes XTTV DNS failure from a general network failure.
    for resolver, endpoint in [
        ("Cloudflare DoH", "https://cloudflare-dns.com/dns-query?name=oettv.xttv.at&type=A"),
        ("Google DoH", "https://dns.google/resolve?name=oettv.xttv.at&type=A"),
    ]:
        req = urllib.request.Request(endpoint, headers={"Accept": "application/dns-json", "User-Agent": "TT-Aufstellung/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read().decode("utf-8"))
                answers = [a.get("data") for a in payload.get("Answer", []) if a.get("type") == 1]
                result["checks"].append({"method": resolver, "ok": True, "addresses": answers, "status": r.status})
        except Exception as exc:
            result["checks"].append({"method": resolver, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    result["direct_success"] = any(c.get("method") == "direct HTTPS" and c.get("ok") for c in result["checks"])
    return result


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self.send_json({"ok": True})
        if parsed.path == "/api/xttv/debug":
            meid = int(parse_qs(parsed.query).get("meid", ["437757"])[0])
            return self.send_json(debug_xttv(meid))
        if parsed.path == "/api/xttv/fetch":
            meid = int(parse_qs(parsed.query).get("meid", ["437757"])[0])
            return self.send_json({"meid": meid, **http_fetch(MATCH_URL.format(meid=meid))})

        rel = "index.html" if parsed.path in ("", "/") else parsed.path.lstrip("/")
        target = (ROOT / rel).resolve()
        if not str(target).startswith(str(ROOT.resolve())) or not target.is_file():
            self.send_error(404)
            return
        content = target.read_bytes()
        types = {".html": "text/html", ".css": "text/css", ".mjs": "text/javascript", ".js": "text/javascript"}
        self.send_response(200)
        self.send_header("Content-Type", types.get(target.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
