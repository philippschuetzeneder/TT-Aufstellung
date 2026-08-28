from unittest.mock import patch

import pytest

from app.xttv_db_import import scan_import_reports


def test_scan_import_reports_validates_direction():
    with pytest.raises(ValueError, match='forward scan'):
        scan_import_reports(start=10, end=5, direction='forward')


@patch('app.xttv_db_import._is_imported', return_value=False)
@patch('app.xttv_db_import._classify_meid', return_value={'importable': False, 'status': 'skip'})
@patch('app.xttv_db_import._try_import_classified', return_value=False)
@patch('app.xttv_db_import.create_all')
def test_scan_import_reports_honors_range(_create, _try, _cls, _imp):
    result = scan_import_reports(start=100, end=102, limit=5, direction='forward')
    assert result['mode'] == 'range'
    assert result['checked'] == 3
    assert result['range'] == {'start': 100, 'end': 102}
