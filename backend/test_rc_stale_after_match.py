from app.data_refresh_service import external_ids_needing_rc_after_match


def test_external_ids_needing_rc_after_match_empty_without_db(monkeypatch):
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
    assert external_ids_needing_rc_after_match(seasons=frozenset({"2026/2027"})) == []
