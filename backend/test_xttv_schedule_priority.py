from datetime import date

from app.xttv_schedule_import import (
    count_missing_past_meids,
    parse_schedule_entries,
    prioritize_missing_meids,
    schedule_import_candidates,
)


def test_parse_schedule_entries_extracts_date_from_row():
    html = """
    <tr>
      <td>Fr. 23.10.2026 19:30</td>
      <td><a href="index.php?meid=462244">SPWD2 - TRAK2</a></td>
    </tr>
    """
    entries = parse_schedule_entries(html)
    assert entries == [(462244, date(2026, 10, 23))]


def test_parse_schedule_entries_xttv_game_summary_li():
    html = """
    <li class="gameSummary">
      <span class="spieldatum_datum"> 23.10.2026 19:30 </span>
      <span class="ergebnis">
        <a href="/ed/index.php?meid=462244">3:8</a>
      </span>
      <div class="gamedetail">
        <table><tr class="resultDetailLine">
          <td><a href="/ed/index.php?meid=462244">Spielbericht</a></td>
        </tr></table>
      </div>
    </li>
    """
    entries = parse_schedule_entries(html)
    assert entries == [(462244, date(2026, 10, 23))]


def test_prioritize_missing_meids_newest_past_first_excludes_future():
    today = date(2026, 10, 26)
    meid_dates = {
        462244: date(2026, 10, 23),
        462248: date(2026, 10, 16),
        470000: date(2026, 11, 2),
        460000: date(2026, 9, 1),
    }
    missing = [470000, 460000, 462244, 462248]
    assert schedule_import_candidates(missing, meid_dates, today=today) == [
        460000,
        462244,
        462248,
    ]
    ordered = prioritize_missing_meids(missing, meid_dates, today=today)
    assert ordered == [462244, 462248, 460000]


def test_count_missing_past_meids():
    meid_dates = {1: date(2026, 10, 1), 2: date(2026, 11, 1)}
    assert count_missing_past_meids([1, 2], meid_dates, today=date(2026, 10, 26)) == 1


def test_import_missing_schedule_reports_orders_by_match_date(monkeypatch):
    from app import xttv_schedule_import as mod

    order: list[int] = []

    def classify(meid, check_db=True):
        order.append(meid)
        return {"importable": False, "status": "skip", "miss": False}

    discovery = {
        "meids": [100, 500, 200],
        "meid_dates": {
            100: date(2026, 10, 1),
            200: date(2026, 10, 23),
            500: date(2026, 12, 1),
        },
        "leagues_checked": 1,
        "meids_total": 3,
        "by_season": {},
        "sjids": [26],
    }

    monkeypatch.setattr(mod, "collect_schedule_meids", lambda **kwargs: discovery)
    monkeypatch.setattr(mod, "_is_imported", lambda meid: meid == 500)
    monkeypatch.setattr(mod, "create_all", lambda: None)
    monkeypatch.setattr(mod, "_classify_meid", classify)
    monkeypatch.setattr(mod, "_try_import_classified", lambda *a, **k: False)

    result = mod.import_missing_schedule_reports(limit=10)
    assert result["discovery"]["missing_in_db"] == 2
    assert order == [200, 100]
