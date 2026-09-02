from datetime import date
from unittest.mock import MagicMock, patch

from app.analysis_service import (
    DEFAULT_RC_RATING,
    _load_player_position_priors,
    _resolve_rc_rating,
    _scenarios_from_strength_prior,
    _team_average_rc,
)


def test_team_average_rc_uses_teammates_and_fallback():
    rc = {'a': 1600.0, 'b': 1400.0}
    assert _team_average_rc(rc, ['a', 'b', 'c']) == 1500.0
    assert _resolve_rc_rating('c', rc, ['a', 'b', 'c']) == 1500.0
    assert _team_average_rc({}, ['a', 'b']) == DEFAULT_RC_RATING
    assert DEFAULT_RC_RATING == 1200.0


def test_resolve_rc_rating_returns_own_value_when_present():
    rc = {'a': 1700.0, 'b': 1500.0, 'c': 1300.0, 'd': 1100.0}
    assert _resolve_rc_rating('a', rc, list(rc)) == 1700.0


def test_strength_prior_uses_team_average_for_missing_rc():
    players = ['strong', 'mid', 'mid2', 'weak']
    rc = {'strong': 2000.0, 'mid': 1600.0, 'mid2': 1550.0}
    scenarios = _scenarios_from_strength_prior(players, rc)
    assert scenarios[0][1][0] == 'strong'
    assert _resolve_rc_rating('weak', rc, players) == (2000.0 + 1600.0 + 1550.0) / 3.0


def test_position_priors_weight_recent_positions_higher():
    ref_date = date(2026, 3, 1)
    rows = [
        {'player_id': '1', 'position': 'A', 'match_date': '01.01.2024 19:00', 'match_id': 10},
        {'player_id': '1', 'position': 'D', 'match_date': '01.02.2026 19:00', 'match_id': 20},
    ]
    db = MagicMock()
    db.execute.return_value.mappings.return_value = rows
    with patch('app.analysis_service._build_match_rounds_ago', return_value={10: 12, 20: 0}):
        priors = _load_player_position_priors(db, ['1'], ref_date=ref_date)
    assert priors['1'][0] < priors['1'][3]
