from unittest.mock import MagicMock, patch

import pytest

from app.xttv_db_import import (
    _can_advance_frontier,
    _classify_meid,
    _is_real_player_id,
    _normalize_parsed_match,
    _resolve_match_player_id,
    _try_import_classified,
    import_one,
    scan_and_import,
)
from app.xttv_parser import _player, normalize_team_name, parse_match


MINIMAL_REPORT_HTML = """
<html><head><title>Testspiel</title></head><body>
<table>
<tr><th></th><th>Heim-Mannschaft: A-D</th><th>Team Alpha (TA)</th><th></th><th>8 : 2</th></tr>
<tr><th></th><th>Gast-Mannschaft: 1-4</th><th>Team Beta (TB)</th><th></th><th></th></tr>
</table>
<table>
<tr><td>Liga 2025/2026</td><td>A: PassNr 111 Name A</td><td>B: PassNr 112 Name B</td><td>C: PassNr 113 Name C</td><td>D: PassNr 114 Name D</td></tr>
<tr><td>1: PassNr 211 G1</td><td>(1) 3:1 TA</td><td>(2) 3:1 TA</td><td>(3) 3:1 TA</td><td>(4) 3:1 TA</td></tr>
<tr><td>2: PassNr 212 G2</td><td>(6) 3:1 TA</td><td>(7) 3:1 TA</td><td>(8) 3:1 TA</td><td>(9) 3:1 TA</td></tr>
<tr><td>3: PassNr 213 G3</td><td></td><td></td><td></td><td></td></tr>
<tr><td>4: PassNr 214 G4</td><td></td><td></td><td></td><td></td></tr>
<tr><td>Doppel (5): 115 / 116 Pair H</td><td></td><td></td><td></td><td></td></tr>
<tr><td></td><td></td><td></td><td></td><td>(5) 3:1 TA (10) 3:1 TA</td></tr>
</table>
</body></html>
"""


def test_normalize_team_name_collapses_whitespace_and_dashes():
    assert normalize_team_name('  SV  Foo  ') == 'SV Foo'
    assert normalize_team_name('SV Foo\u2013Bar') == 'SV Foo-Bar'


def test_player_parsing_without_pass_number():
    position, name, player_id = _player('A: Max Mustermann')
    assert position == 'A'
    assert name == 'Max Mustermann'
    assert player_id is None


def test_resolve_match_player_id_uses_stable_placeholder_without_passnr():
    player_id = _resolve_match_player_id({'side': 'home', 'position': 'A'})
    assert player_id == '__nopass_home_A'
    assert not _is_real_player_id(player_id)


def test_normalize_parsed_match_normalizes_team_names():
    parsed = _normalize_parsed_match({'home_team': '  Alpha  ', 'away_team': 'Beta\u2013Team'})
    assert parsed['home_team'] == 'Alpha'
    assert parsed['away_team'] == 'Beta-Team'


@patch('app.xttv_db_import.fetch_match')
def test_classify_meid_reports_parse_errors(mock_fetch):
    mock_fetch.return_value = ('<html><body>2025/2026 no tables</body></html>', 200, 'text/html', 'url')
    result = _classify_meid(999001, check_db=False)
    assert result['status'] == 'parse_error'
    assert 'parse_error' in result


@patch('app.xttv_db_import.fetch_match')
def test_classify_meid_marks_outside_season(mock_fetch):
    mock_fetch.return_value = (MINIMAL_REPORT_HTML.replace('2025/2026', '2010/2011'), 200, 'text/html', 'url')
    result = _classify_meid(999002, check_db=False)
    assert result['status'] == 'valid_outside_filter'
    assert result['filter_reason'] == 'season'


def test_can_advance_frontier_rules():
    assert _can_advance_frontier({'miss': True}, False) is True
    assert _can_advance_frontier({'status': 'valid_outside_filter'}, False) is True
    assert _can_advance_frontier({'status': 'importable'}, True) is True
    assert _can_advance_frontier({'status': 'importable'}, False) is False
    assert _can_advance_frontier({'status': 'parse_error'}, False) is False


@patch('app.xttv_db_import._is_imported', return_value=False)
@patch('app.xttv_db_import.import_one', side_effect=ValueError('boom'))
def test_try_import_classified_catches_errors(_import, _is_imported):
    imported_ids: list[int] = []
    imported_details: list[dict] = []
    import_failures: list[dict] = []
    cls = {'importable': True, 'status': 'importable', 'meid': 123}
    ok = _try_import_classified(cls, imported_ids, imported_details, import_failures, 10)
    assert ok is False
    assert imported_ids == []
    assert len(import_failures) == 1
    assert 'boom' in import_failures[0]['error']


@patch('app.xttv_db_import.scan_import_reports')
def test_scan_and_import_delegates_to_range_scan(mock_scan):
    mock_scan.return_value = {'ok': True, 'mode': 'range'}
    result = scan_and_import(start=100, end=120, limit=5)
    mock_scan.assert_called_once_with(start=100, end=120, limit=5)
    assert result['mode'] == 'range'


@patch('app.xttv_db_import._persist_import')
@patch('app.xttv_db_import._is_valid_importable_report', return_value=True)
@patch('app.xttv_db_import.parse_match')
@patch('app.xttv_db_import.fetch_match')
def test_import_one_is_idempotent_on_integrity_error(mock_fetch, mock_parse, _valid, mock_persist):
    mock_fetch.return_value = ('html', 200, 'text/html', 'url')
    mock_parse.return_value = {
        'player_count': 8,
        'singles_count': 8,
        'doubles_count': 2,
        'has_walkover': False,
        'players': [],
        'games': [],
        'home_team': 'A',
        'away_team': 'B',
        'team_result': '8:2',
    }
    from sqlalchemy.exc import IntegrityError

    mock_persist.side_effect = [IntegrityError('stmt', {}, Exception('dup')), {'ok': True, 'meid': 1}]
    result = import_one(1)
    assert result['ok'] is True
    assert mock_persist.call_count == 2


def test_parse_match_rejects_three_player_format():
    html = MINIMAL_REPORT_HTML.replace('A-D', 'A-C').replace('1-4', '1-3')
    with pytest.raises(ValueError, match='3-player'):
        parse_match(html, 1)
