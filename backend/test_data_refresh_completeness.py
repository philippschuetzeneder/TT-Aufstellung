def test_run_data_refresh_not_ok_when_rc_stale_remains(monkeypatch):
    from app.data_refresh_service import run_data_refresh

    monkeypatch.setattr(
        "app.data_refresh_service.drain_schedule_backlog",
        lambda **k: {
            "imported": 0,
            "imported_meids": [],
            "rounds": 0,
            "checked": 0,
            "errors": 0,
            "discovery": {"missing_remaining": 0, "missing_past_remaining": 0},
        },
    )
    monkeypatch.setattr(
        "app.data_refresh_service.import_new_reports",
        lambda **k: {"imported": 0, "imported_meids": [], "checked": 0, "errors": 0},
    )
    monkeypatch.setattr(
        "app.data_refresh_service.measure_schedule_import_gaps",
        lambda **k: {"missing_remaining": 0, "missing_past_remaining": 0},
    )
    monkeypatch.setattr("app.data_refresh_service.check_rc_connectivity", lambda **k: (True, "ok"))
    monkeypatch.setattr("app.data_refresh_service.apply_matches_all", lambda **k: {"applied": 0})
    monkeypatch.setattr(
        "app.data_refresh_service.external_ids_from_imported_meids",
        lambda meids: [],
    )
    monkeypatch.setattr(
        "app.data_refresh_service.external_ids_needing_rc_after_match",
        lambda **k: ["99999"],
    )
    monkeypatch.setattr("app.data_refresh_service.external_ids_without_rc_mapping", lambda ids: [])
    monkeypatch.setattr(
        "app.data_refresh_service._import_rc_for_players",
        lambda ids: {"imported": 0, "errors": 0, "targets": 0},
    )
    monkeypatch.setattr("app.data_refresh_service.clear_analysis_runtime_caches", lambda: None)
    monkeypatch.setattr(
        "app.data_refresh_service.refresh_analysis_cache",
        lambda: {"ok": True},
    )

    result = run_data_refresh()
    assert result["schedule_complete"] is True
    assert result["rc_complete"] is False
    assert result["ok"] is False
