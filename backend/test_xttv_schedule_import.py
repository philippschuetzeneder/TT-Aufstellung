from app.xttv_schedule_import import parse_meids_from_html


def test_missing_meids_sorted_newest_first(monkeypatch):
    from app import xttv_schedule_import as mod

    order: list[int] = []

    def classify(meid, check_db=True):
        order.append(meid)
        return {"importable": False, "status": "skip", "miss": False}

    monkeypatch.setattr(mod, "collect_schedule_meids", lambda: {"meids": [100, 500, 200]})
    monkeypatch.setattr(mod, "_is_imported", lambda meid: meid == 500)
    monkeypatch.setattr(mod, "create_all", lambda: None)
    monkeypatch.setattr(mod, "_classify_meid", classify)
    monkeypatch.setattr(mod, "_try_import_classified", lambda *a, **k: False)

    result = mod.import_missing_schedule_reports(limit=10)
    assert result["discovery"]["missing_in_db"] == 2
    assert order == [200, 100]


def test_parse_meids_from_html_deduplicates_and_preserves_order():
    html = """
    <a href="index.php?meid=462234">A</a>
    <a href="index.php?meid=462235">B</a>
    <a href="index.php?meid=462234">dup</a>
    """
    assert parse_meids_from_html(html) == [462234, 462235]
