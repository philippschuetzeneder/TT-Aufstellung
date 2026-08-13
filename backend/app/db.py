import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung",
    )
    # Render may provide a plain PostgreSQL URL. SQLAlchemy 2.x must be told
    # explicitly to use the psycopg 3 driver; otherwise it defaults to psycopg2.
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

    # The original constraint used the player name as part of the identity.
    # XTTV names are not unique, so two different players with the same name
    # can occur in one match. Migrate existing PostgreSQL databases to use the
    # stable XTTV external_player_id instead.
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE match_players DROP CONSTRAINT IF EXISTS uq_match_player"))
            connection.execute(
                text(
                    "ALTER TABLE match_players "
                    "ADD CONSTRAINT uq_match_player "
                    "UNIQUE (match_id, external_player_id, side)"
                )
            )


def database_health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "database": "connected"}
    except Exception as exc:
        return {"ok": False, "database": "error", "error": f"{type(exc).__name__}: {exc}"}
