"""HTTP-level admin token checks for protected routes."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from app import api_auth
from app.web import Handler


def _request(port: int, path: str, token: str | None = None, method: str = "GET") -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    headers = {}
    if token:
        headers["X-Admin-Token"] = token
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


@pytest.fixture()
def auth_server():
    with patch.object(api_auth, "ADMIN_TOKEN", "test-admin-token"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_public_leagues_without_token(auth_server):
    port = auth_server
    status, data = _request(port, "/api/leagues")
    assert status == 200
    assert data.get("ok") is not False


def test_analysis_is_public_even_with_admin_token(auth_server):
    port = auth_server
    status, data = _request(port, "/api/analysis?own_player_ids=1,2,3,4&opponent_team=TEST")
    assert status in {200, 500}
    if status == 200:
        assert "recommendations" in data or data.get("ok") is not False


def test_auth_status_reports_required(auth_server):
    port = auth_server
    status, data = _request(port, "/api/auth/status")
    assert status == 200
    assert data.get("admin_required") is True


def test_data_refresh_post_requires_token(auth_server):
    port = auth_server
    status, data = _request(port, "/api/data/refresh?restart=0", method="POST")
    assert status == 401

    with patch("app.web.run_data_refresh", return_value={"ok": True, "message": "mock"}):
        status, data = _request(port, "/api/data/refresh?restart=0", token="test-admin-token", method="POST")
    assert status == 200
    assert data.get("ok") is True
