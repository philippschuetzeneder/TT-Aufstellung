from datetime import date

from app.data_refresh_service import _refresh_paused, run_data_refresh
from app.xttv_db_import import get_target_seasons


def test_target_seasons_before_rollover():
    assert get_target_seasons(today=date(2026, 10, 4)) == frozenset(
        {"2025/2026", "2024/2025", "2023/2024"}
    )


def test_target_seasons_from_rollover():
    assert get_target_seasons(today=date(2026, 10, 5)) == frozenset(
        {"2026/2027", "2025/2026", "2024/2025"}
    )


def test_refresh_paused_when_env_set(monkeypatch):
    monkeypatch.setenv("DATA_REFRESH_NOT_BEFORE", "2026-10-05")
    paused, reason = _refresh_paused()
    assert paused is True
    assert "2026-10-05" in (reason or "")


def test_run_data_refresh_skips_during_pause(monkeypatch):
    monkeypatch.setenv("DATA_REFRESH_NOT_BEFORE", "2099-01-01")
    result = run_data_refresh()
    assert result["skipped"] is True
    assert result["data_changed"] is False
