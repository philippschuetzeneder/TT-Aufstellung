from app.analytics_service import _form_stats_rows, _profile_scope_bundle
from app.player_analysis_service import _club_name_prefix, _current_season_roster_ids


def test_club_name_prefix():
    assert _club_name_prefix("Tragwein/Kamig 2") == "Tragwein/Kamig"
    assert _club_name_prefix("SV Foo") is None


def test_form_stats_rows_counts_wins():
    rows = [
        {"win": True, "draw": False},
        {"win": False, "draw": True},
        {"win": True, "draw": False},
    ]
    stats = _form_stats_rows(rows)
    assert stats["games"] == 3
    assert stats["wins"] == 2
    assert stats["draws"] == 1
    assert stats["losses"] == 0


def test_current_season_roster_ids_empty_without_db_match(monkeypatch):
    class FakeSession:
        def execute(self, *args, **kwargs):
            class R:
                def scalars(self):
                    class S:
                        def all(self):
                            return []

                    return S()

            return R()

    ids = _current_season_roster_ids(FakeSession(), "Tragwein/Kamig 2", "421 RK 2026/2027")
    assert ids == set()


def test_profile_scope_bundle_empty_singles():
    bundle = _profile_scope_bundle([], [])
    assert bundle["matches"] == 0
    assert bundle["form"]["last_5"]["games"] == 0
