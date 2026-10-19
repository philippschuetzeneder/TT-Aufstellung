from app.refresh_report_email import send_sunday_preflight_report


def test_send_sunday_preflight_report_skips_without_smtp(monkeypatch):
    monkeypatch.setattr("app.refresh_report_email.smtp_configured", lambda: False)
    out = send_sunday_preflight_report({"ok": True, "stale_rc_targets": 3})
    assert out["sent"] is False
