"""Weekly data-refresh report e-mail (plain text + HTML)."""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any
from zoneinfo import ZoneInfo

VIENNA = ZoneInfo("Europe/Vienna")
DEFAULT_TO = "p.schuetzeneder@gmail.com"
DEFAULT_FROM = "tt-aufstellung@tt-aufstellung.at"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def smtp_use_auth() -> bool:
    explicit = _env("SMTP_USE_AUTH", "").lower()
    if explicit in {"1", "true", "yes"}:
        return True
    if explicit in {"0", "false", "no"}:
        return False
    return bool(_env("SMTP_PASSWORD"))


def smtp_configured() -> bool:
    if not _env("SMTP_HOST"):
        return False
    if not (_env("SMTP_FROM") or _env("SMTP_USER")):
        return False
    if smtp_use_auth():
        return bool(_env("SMTP_USER") and _env("SMTP_PASSWORD"))
    return True


def report_recipients() -> list[str]:
    raw = _env("REFRESH_REPORT_EMAIL_TO", DEFAULT_TO)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _status(result: dict[str, Any]) -> tuple[str, str, str]:
    """Return (label, color_hex, css_class)."""
    if not result.get("ok", False):
        return "FEHLER", "#c62828", "bad"
    summary = result.get("summary") or {}
    xttv = summary.get("xttv") or {}
    rc = summary.get("rc") or {}
    errors = int(xttv.get("errors") or 0) + int(rc.get("errors") or 0)
    if errors:
        return "FEHLER", "#c62828", "bad"
    return "OK", "#2e7d32", "good"


def _subject(result: dict[str, Any], *, when: datetime | None = None) -> str:
    when = when or datetime.now(VIENNA)
    label, _, _ = _status(result)
    summary = result.get("summary") or {}
    xttv = summary.get("xttv") or {}
    imported = int(xttv.get("imported") or 0)
    date_str = when.strftime("%d.%m.%Y")
    if label == "OK":
        detail = f"{imported} Spielberichte" if imported else "keine neuen Daten"
        return f"[TT-Aufstellung] Daten-Update OK ({date_str}) — {detail}"
    err = result.get("error") or "Fehler im Lauf"
    return f"[TT-Aufstellung] Daten-Update FEHLER ({date_str}) — {err}"


def _row(label: str, value: Any) -> str:
    if value is None or value == "":
        value = "—"
    if isinstance(value, bool):
        value = "ja" if value else "nein"
    return f"{label}: {value}"


def _section_lines(title: str, data: dict[str, Any] | None) -> list[str]:
    lines = [title, "-" * len(title)]
    if not data:
        lines.append("(keine Daten)")
        return lines
    if data.get("skipped"):
        reason = data.get("reason") or "übersprungen"
        lines.append(f"übersprungen ({reason})")
        return lines
    for key in sorted(data.keys()):
        if key == "imported_meids":
            continue
        val = data[key]
        if isinstance(val, list):
            lines.append(_row(key, f"{len(val)} Einträge"))
        else:
            lines.append(_row(key, val))
    return lines


def build_plain_text(
    result: dict[str, Any],
    *,
    restart_done: bool = False,
    host_note: str = "",
    when: datetime | None = None,
) -> str:
    when = when or datetime.now(VIENNA)
    label, _, _ = _status(result)
    summary = result.get("summary") or {}
    xttv = summary.get("xttv") or {}
    meids = xttv.get("imported_meids") or []

    lines = [
        f"TT-Aufstellung — Wöchentliches Daten-Update",
        f"Status: {label}",
        f"Zeitpunkt: {when.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "Zusammenfassung",
        "-------------",
        _row("Nachricht", result.get("message") or result.get("error") or result.get("reason")),
        _row("Daten geändert", result.get("data_changed")),
        _row("Dauer (Sekunden)", result.get("elapsed_seconds")),
        _row("App-Neustart", restart_done),
    ]
    if host_note:
        lines.append(_row("Hinweis", host_note))
    lines.append("")

    for title, block in (
        ("XTTV", xttv),
        ("RC", summary.get("rc")),
        ("Analysis-Cache", summary.get("analysis_cache")),
    ):
        lines.extend(_section_lines(title, block if isinstance(block, dict) else None))
        lines.append("")

    if meids:
        lines.append(f"Importierte MEIDs ({len(meids)})")
        lines.append("---------------------")
        preview = ", ".join(str(m) for m in meids[:40])
        lines.append(preview)
        if len(meids) > 40:
            lines.append(f"... und {len(meids) - 40} weitere")
        lines.append("")

    lines.append("Rohdaten (JSON)")
    lines.append("---------------")
    lines.append(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return "\n".join(lines)


def _html_table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(
        f"<tr><th align='left' style='padding:4px 12px 4px 0;'>{label}</th>"
        f"<td style='padding:4px 0;'>{value}</td></tr>"
        for label, value in rows
    )
    return f"<table style='border-collapse:collapse;font-size:14px;'>{body}</table>"


def _fmt_val(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, list):
        return f"{len(value)} Einträge"
    return str(value)


def build_html(
    result: dict[str, Any],
    *,
    restart_done: bool = False,
    host_note: str = "",
    when: datetime | None = None,
) -> str:
    when = when or datetime.now(VIENNA)
    label, color, css = _status(result)
    summary = result.get("summary") or {}
    xttv = summary.get("xttv") or {}
    rc = summary.get("rc") if isinstance(summary.get("rc"), dict) else {}
    cache = summary.get("analysis_cache") if isinstance(summary.get("analysis_cache"), dict) else {}
    meids = xttv.get("imported_meids") or []

    def block_rows(data: dict[str, Any] | None) -> list[tuple[str, Any]]:
        if not data:
            return [("Status", "keine Daten")]
        if data.get("skipped"):
            return [("Status", f"übersprungen ({data.get('reason') or '—'})")]
        return [(k, _fmt_val(v)) for k, v in sorted(data.items()) if k != "imported_meids"]

    meid_block = ""
    if meids:
        preview = ", ".join(str(m) for m in meids[:40])
        extra = f"<p>… und {len(meids) - 40} weitere</p>" if len(meids) > 40 else ""
        meid_block = f"""
        <h3 style='margin:20px 0 8px;font-size:15px;'>Importierte MEIDs ({len(meids)})</h3>
        <p style='font-family:monospace;font-size:13px;'>{preview}</p>
        {extra}
        """

    raw_json = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    host_line = f"<p><strong>Hinweis:</strong> {host_note}</p>" if host_note else ""

    return f"""<!DOCTYPE html>
<html lang="de">
<body style="font-family:Segoe UI,Arial,sans-serif;color:#222;max-width:720px;">
  <div style="background:{color};color:#fff;padding:14px 18px;border-radius:6px;margin-bottom:18px;">
    <div style="font-size:18px;font-weight:600;">Daten-Update: {label}</div>
    <div style="font-size:13px;opacity:0.95;margin-top:4px;">
      {when.strftime("%A, %d.%m.%Y %H:%M")} (Europe/Vienna)
    </div>
  </div>
  <p style="font-size:15px;">{result.get("message") or result.get("error") or result.get("reason") or ""}</p>
  {host_line}
  <h3 style="margin:20px 0 8px;font-size:15px;">Zusammenfassung</h3>
  {_html_table([
    ("Daten geändert", _fmt_val(result.get("data_changed"))),
    ("Dauer (Sekunden)", _fmt_val(result.get("elapsed_seconds"))),
    ("App-Neustart", _fmt_val(restart_done)),
  ])}
  <h3 style="margin:20px 0 8px;font-size:15px;">XTTV</h3>
  {_html_table(block_rows(xttv))}
  <h3 style="margin:20px 0 8px;font-size:15px;">RC</h3>
  {_html_table(block_rows(rc))}
  <h3 style="margin:20px 0 8px;font-size:15px;">Analysis-Cache</h3>
  {_html_table(block_rows(cache))}
  {meid_block}
  <h3 style="margin:20px 0 8px;font-size:15px;">Rohdaten (JSON)</h3>
  <pre style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:12px;overflow:auto;">{raw_json}</pre>
</body>
</html>"""


def send_weekly_refresh_report(
    result: dict[str, Any],
    *,
    restart_done: bool = False,
    host_note: str = "",
    when: datetime | None = None,
) -> dict[str, Any]:
    """Send one report e-mail; returns status dict (does not raise on SMTP failure)."""
    when = when or datetime.now(VIENNA)
    if not smtp_configured():
        return {"sent": False, "reason": "smtp_not_configured"}

    recipients = report_recipients()
    if not recipients:
        return {"sent": False, "reason": "no_recipients"}

    subject = _subject(result, when=when)
    plain = build_plain_text(result, restart_done=restart_done, host_note=host_note, when=when)
    html = build_html(result, restart_done=restart_done, host_note=host_note, when=when)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_addr = _env("SMTP_FROM") or _env("SMTP_USER") or DEFAULT_FROM
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    domain = from_addr.split("@", 1)[-1] if "@" in from_addr else "tt-aufstellung.at"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "25") or "25")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    use_tls = _env("SMTP_USE_TLS", "0").lower() not in {"0", "false", "no"}

    try:
        with smtplib.SMTP(host, port, timeout=60) as server:
            if use_tls:
                server.starttls(context=ssl.create_default_context())
            if smtp_use_auth():
                server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
        return {"sent": True, "to": recipients, "subject": subject}
    except Exception as exc:
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}", "to": recipients}


def send_shell_failure_report(
    error_message: str,
    *,
    log_excerpt: str = "",
    when: datetime | None = None,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "data_changed": False,
        "error": error_message,
        "message": error_message,
        "summary": {},
    }
    note = f"Shell/Host-Fehler vor Abschluss des Refresh-Laufs."
    if log_excerpt:
        note = f"{note}\n\nLog-Auszug:\n{log_excerpt[-4000:]}"
    return send_weekly_refresh_report(result, restart_done=False, host_note=note, when=when)
