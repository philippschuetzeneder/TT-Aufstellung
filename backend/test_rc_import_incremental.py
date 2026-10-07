from datetime import datetime

from app.rc_import import _persist_parsed_history


class _FakeQuery:
    def __init__(self, existing):
        self.existing = existing

    def filter_by(self, **kwargs):
        key = (kwargs.get("player_id"), kwargs.get("observed_at"), kwargs.get("source"))
        return self

    def one_or_none(self):
        return None


class _FakeSession:
    def __init__(self):
        self.added = []

    def query(self, model):
        return _FakeQuery(None)

    def add(self, obj):
        self.added.append(obj)


class _Player:
    id = 1


class _Raw:
    id = 99


def test_persist_parsed_history_only_after_skips_older_rows():
    session = _FakeSession()
    parsed = {
        "history": [
            {"observed_at": "2026-01-01", "rc_rating": 1500.0, "rc_deviation": 80.0},
            {"observed_at": "2026-02-01", "rc_rating": 1510.0, "rc_deviation": 79.0},
        ],
        "current": {"observed_at": "2026-03-01", "rc_rating": 1520.0, "rc_deviation": 78.0},
    }
    saved = _persist_parsed_history(
        session,
        _Player(),
        _Raw(),
        parsed,
        only_after=datetime.fromisoformat("2026-01-15"),
    )
    assert saved == 2
    assert len(session.added) == 2
