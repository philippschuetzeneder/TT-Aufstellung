import time
from datetime import date
from itertools import permutations

from app.analysis_service import (
    MAX_ANALYSIS_SECONDS,
    _cap_opponent_scenarios,
    _empty_profile,
    analyze_lineup,
)


def test_cap_opponent_scenarios_renormalizes():
    scenarios = [(0.5 - i * 0.01, tuple(f"p{i}" for i in range(4))) for i in range(30)]
    capped = _cap_opponent_scenarios(scenarios, limit=8)
    assert len(capped) == 8
    assert abs(sum(p for p, _ in capped) - 1.0) < 1e-9


def test_analyze_lineup_completes_under_budget_with_many_uniform_scenarios(monkeypatch):
    """Regression: partial-opponent path must not hang when many filler combos exist."""
    own = ["o1", "o2", "o3", "o4"]
    opp_team = "Test Team"
    heavy = [(1.0 / 24.0, order) for order in permutations(["a", "b", "c", "d"])]

    def fake_load(own_ids, opponent_team, actual, use_spieltyp=False):
        names = {pid: pid for pid in (*own_ids, "a", "b", "c", "d", "k1")}
        profiles = {}
        for pid in names:
            p = _empty_profile()
            p["rc_rating"] = 1400.0
            p["games"] = 10
            p["wins"] = 5
            profiles[pid] = p
        return names, profiles, {}, heavy, "test-heavy", date.today(), set(names), []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("app.analysis_service._load_analysis_data", fake_load)
    monkeypatch.setattr("app.analysis_service.SessionLocal", lambda: FakeSession())
    monkeypatch.setattr("app.analysis_service._load_doubles_stats", lambda *args, **kwargs: {})
    monkeypatch.setattr("app.analysis_service._build_info_summary", lambda *args, **kwargs: {})
    started = time.monotonic()
    result = analyze_lineup(
        own, opp_team, actual_opponent_ids=["k1"], own_is_home=True,
    )
    elapsed = time.monotonic() - started
    assert result["ok"] is True
    assert result.get("recommendations")
    assert result.get("recommendation")
    assert elapsed < MAX_ANALYSIS_SECONDS + 0.25
