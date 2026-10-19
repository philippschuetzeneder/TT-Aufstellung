import json
from app.refresh_report_email import send_weekly_refresh_report

result = {
    "ok": True,
    "data_changed": True,
    "elapsed_seconds": 490.8,
    "summary": {
        "xttv": {
            "imported": 30,
            "schedule_imported": 30,
            "forward_imported": 0,
            "checked": 73,
            "errors": 15,
        },
        "rc": {"imported": 235, "errors": 0, "incremental_updates": 235},
        "analysis_cache": {"ok": True},
    },
    "message": "30 neue Spielberichte importiert (Schedule: 30, Forward-Scan: 0).",
}
out = send_weekly_refresh_report(
    result,
    restart_done=True,
    host_note="Nachgezogen nach Shell-Fix. Original: logs/weekly-data-refresh-20261019-040001.log",
)
print(json.dumps(out, ensure_ascii=False))
