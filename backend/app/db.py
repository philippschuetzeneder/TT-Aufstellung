import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung",
    )
    # Render may provide a plain PostgreSQL URL. We use psycopg 3 explicitly,
    # which is the driver installed by the application requirements.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


DATABASE_URL = _database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def create_all() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def database_health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "database": "connected"}
    except Exception as exc:
        return {"ok": False, "database": "error", "error": f"{type(exc).__name__}: {exc}"}
