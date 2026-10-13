"""Tests for match-linked RC catch-up player selection."""

from app.data_refresh_service import match_linked_player_external_ids


def test_match_linked_player_external_ids_empty_without_db(monkeypatch):
    class FakeSession:
        def execute(self, *args, **kwargs):
            class R:
                def scalars(self):
                    class S:
                        def all(self):
                            return []

                    return S()

            return R()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("app.data_refresh_service.SessionLocal", lambda: FakeSession())
    assert match_linked_player_external_ids(seasons=frozenset({"2026/2027"})) == []


def test_persist_always_updates_current_on_incremental():
    from datetime import datetime

    from app.rc_import import _persist_parsed_history

    class Snap:
        def __init__(self):
            self.rc_rating = None
            self.rc_deviation = None
            self.source_document_id = None
            self.imported_at = None

    added = []

    class Q:
        def filter_by(self, **kwargs):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, model):
            return Q()

        def add(self, obj):
            added.append(obj)

    session = Session()
    parsed = {
        "history": [{"observed_at": "2026-10-07", "rc_rating": 1379.0, "rc_deviation": 58.0}],
        "current": {"observed_at": "2026-10-13", "rc_rating": 1393.0, "rc_deviation": 54.0},
    }

    class Player:
        id = 1

    class Raw:
        id = 2

    saved = _persist_parsed_history(
        session,
        Player(),
        Raw(),
        parsed,
        only_after=datetime.fromisoformat("2026-10-07"),
    )
    assert saved >= 1
    assert any(getattr(o, "rc_rating", None) == 1393.0 for o in added)
