from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.refresh_report_email import (
    build_html,
    build_plain_text,
    send_weekly_refresh_report,
    smtp_configured,
    _status,
)

VIENNA = ZoneInfo("Europe/Vienna")


def _sample_ok_result():
    return {
        "ok": True,
        "data_changed": True,
        "elapsed_seconds": 42.3,
        "message": "3 neue Spielberichte importiert.",
        "summary": {
            "xttv": {
                "imported": 3,
                "checked": 120,
                "errors": 0,
                "last_known_meid": 440000,
                "max_imported_meid": 440003,
                "range": "440001-440120",
                "imported_meids": [440001, 440002, 440003],
                "stopped_after_empty_streak": True,
                "scan_frontier_after": 440120,
            },
            "rc": {"newly_mapped": 1, "imported": 2, "errors": 0, "targets": 2},
            "analysis_cache": {"ok": True},
        },
    }


def test_status_ok_and_error():
    assert _status({"ok": True, "summary": {"xttv": {"errors": 0}, "rc": {}}})[0] == "OK"
    assert _status({"ok": False, "error": "boom"})[0] == "FEHLER"
    assert _status({"ok": True, "summary": {"xttv": {"errors": 1}, "rc": {}}})[0] == "FEHLER"


def test_plain_text_contains_key_metrics():
    text = build_plain_text(
        _sample_ok_result(),
        restart_done=True,
        when=datetime(2026, 10, 5, 4, 5, tzinfo=VIENNA),
    )
    assert "Status: OK" in text
    assert "imported: 3" in text
    assert "440001" in text
    assert "App-Neustart: ja" in text


def test_html_shows_green_banner():
    html = build_html(_sample_ok_result(), when=datetime(2026, 10, 5, 4, 5, tzinfo=VIENNA))
    assert "#2e7d32" in html
    assert "Daten-Update: OK" in html


def test_send_skipped_without_smtp(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    out = send_weekly_refresh_report(_sample_ok_result())
    assert out["sent"] is False
    assert out["reason"] == "smtp_not_configured"


def test_send_local_postfix_without_auth(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "host.docker.internal")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_USE_AUTH", "0")
    monkeypatch.setenv("SMTP_FROM", "tt-aufstellung@tt-aufstellung.at")
    monkeypatch.setenv("REFRESH_REPORT_EMAIL_TO", "p.schuetzeneder@gmail.com")
    assert smtp_configured() is True

    smtp_instance = MagicMock()
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("app.refresh_report_email.smtplib.SMTP", return_value=smtp_instance):
        out = send_weekly_refresh_report(_sample_ok_result(), restart_done=False)

    assert out["sent"] is True
    smtp_instance.login.assert_not_called()
    smtp_instance.starttls.assert_not_called()


def test_send_success_with_auth(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "1")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("REFRESH_REPORT_EMAIL_TO", "p.schuetzeneder@gmail.com")
    assert smtp_configured() is True

    smtp_instance = MagicMock()
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("app.refresh_report_email.smtplib.SMTP", return_value=smtp_instance):
        out = send_weekly_refresh_report(_sample_ok_result(), restart_done=False)

    assert out["sent"] is True
    assert out["to"] == ["p.schuetzeneder@gmail.com"]
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("user@example.com", "secret")
    smtp_instance.sendmail.assert_called_once()
