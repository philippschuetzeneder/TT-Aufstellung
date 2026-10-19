"""RC refresh targets: stale-after-match, not only players from newly imported MEIDs."""


def test_stale_after_match_is_independent_of_new_meids(monkeypatch):
    """Players in old reports (e.g. Hahn / MEID already in DB) still need RC when snapshot lags."""
    from app.data_refresh_service import external_ids_needing_rc_after_match

    class FakeSession:
        def execute(self, *args, **kwargs):
            class R:
                def scalars(self):
                    return self

                def all(self):
                    return ["23754"]

            return R()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("app.data_refresh_service.SessionLocal", lambda: FakeSession())
    assert external_ids_needing_rc_after_match(seasons=frozenset({"2026/2027"})) == ["23754"]
