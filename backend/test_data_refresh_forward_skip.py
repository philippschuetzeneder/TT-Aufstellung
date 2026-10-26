def test_run_data_refresh_skips_forward_while_schedule_backlog(monkeypatch):
    from app.data_refresh_service import run_data_refresh

    forward_called = {"n": 0}

    def fake_drain(**kwargs):
        return {
            "imported": 1,
            "imported_meids": [462244],
            "rounds": 1,
            "checked": 5,
            "errors": 0,
            "discovery": {
                "missing_remaining": 3,
                "missing_past_remaining": 1,
            },
        }

    def fake_forward(**kwargs):
        forward_called["n"] += 1
        return {"imported": 99, "imported_meids": [999999]}

    monkeypatch.setattr("app.data_refresh_service.drain_schedule_backlog", fake_drain)
    monkeypatch.setattr("app.data_refresh_service.import_new_reports", fake_forward)
    monkeypatch.setattr("app.data_refresh_service.check_rc_connectivity", lambda **k: (True, "ok"))
    monkeypatch.setattr("app.data_refresh_service.apply_matches_all", lambda **k: {"applied": 0})
    monkeypatch.setattr(
        "app.data_refresh_service.external_ids_needing_rc_after_match",
        lambda **k: [],
    )
    monkeypatch.setattr(
        "app.data_refresh_service.external_ids_from_imported_meids",
        lambda meids: ["17885"],
    )
    monkeypatch.setattr("app.data_refresh_service._import_rc_for_players", lambda ids: {
        "imported": len(ids), "errors": 0, "targets": len(ids),
    })
    monkeypatch.setattr("app.data_refresh_service.clear_analysis_runtime_caches", lambda: None)
    monkeypatch.setattr(
        "app.data_refresh_service.refresh_analysis_cache",
        lambda: {"ok": True},
    )

    result = run_data_refresh()
    assert forward_called["n"] == 0
    assert result["summary"]["xttv"]["forward_skipped"] is True
    assert result["ok"] is False
    assert result["schedule_complete"] is False


def test_run_data_refresh_runs_forward_when_only_future_schedule_gaps(monkeypatch):
    from app.data_refresh_service import run_data_refresh

    forward_called = {"n": 0}

    def fake_drain(**kwargs):
        return {
            "imported": 0,
            "imported_meids": [],
            "rounds": 1,
            "checked": 2,
            "errors": 0,
            "discovery": {
                "missing_remaining": 5,
                "missing_past_remaining": 0,
                "missing_future_on_schedule": 5,
            },
        }

    def fake_forward(**kwargs):
        forward_called["n"] += 1
        return {"imported": 0, "imported_meids": [], "checked": 0, "errors": 0}

    monkeypatch.setattr("app.data_refresh_service.drain_schedule_backlog", fake_drain)
    monkeypatch.setattr("app.data_refresh_service.import_new_reports", fake_forward)
    monkeypatch.setattr("app.data_refresh_service.check_rc_connectivity", lambda **k: (True, "ok"))
    monkeypatch.setattr("app.data_refresh_service.apply_matches_all", lambda **k: {"applied": 0})
    monkeypatch.setattr(
        "app.data_refresh_service.external_ids_needing_rc_after_match",
        lambda **k: [],
    )
    monkeypatch.setattr("app.data_refresh_service.external_ids_from_imported_meids", lambda meids: [])
    monkeypatch.setattr("app.data_refresh_service.clear_analysis_runtime_caches", lambda: None)
    monkeypatch.setattr(
        "app.data_refresh_service.refresh_analysis_cache",
        lambda: {"ok": True},
    )

    result = run_data_refresh()
    assert forward_called["n"] == 1
    assert result["ok"] is True
    assert result["schedule_complete"] is True
