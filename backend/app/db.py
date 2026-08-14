import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung",
    )
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    elif url.startswith("postgresql+psycopg2://"):
        url = "postgresql+psycopg://" + url[len("postgresql+psycopg2://") :]
    return url


DATABASE_URL = _database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def create_all() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE match_players DROP CONSTRAINT IF EXISTS uq_match_player"))
            exists = connection.execute(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'uq_match_player' "
                    "AND conrelid = 'match_players'::regclass"
                )
            ).scalar()
            if not exists:
                connection.execute(
                    text(
                        "ALTER TABLE match_players "
                        "ADD CONSTRAINT uq_match_player "
                        "UNIQUE (match_id, external_player_id, side)"
                    )
                )

            # Analysis queries are filtered primarily by team, player ID and
            # match/side/position. Existing databases need these indexes too;
            # CREATE INDEX IF NOT EXISTS makes this safe on every startup.
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_xttv_matches_home_team "
                "ON xttv_matches (home_team)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_xttv_matches_away_team "
                "ON xttv_matches (away_team)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_match_players_external_id "
                "ON match_players (external_player_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_match_players_match_side_position "
                "ON match_players (match_id, side, position)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_match_games_match_id "
                "ON match_games (match_id)"
            ))


def database_health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "database": "connected"}
    except Exception as exc:
        return {"ok": False, "database": "error", "error": f"{type(exc).__name__}: {exc}"}
