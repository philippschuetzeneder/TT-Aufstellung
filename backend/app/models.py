from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Float, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class RawSourceDocument(Base):
    __tablename__ = "raw_source_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="xttv")
    external_id: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_raw_source_external"),)


class XttvPlayer(Base):
    __tablename__ = "xttv_players"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_player_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    rc_player_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    club: Mapped[str | None] = mapped_column(String(200))
    birth_year: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(50), default="xttv")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    rating_snapshots: Mapped[list[PlayerRatingSnapshot]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class RcPlayerIndex(Base):
    """Persistent cache of RC PlayerSearch results.

    Search results are keyed by a surname prefix so the same RC page is not
    requested repeatedly for every XTTV player. The cache is refreshed on
    demand and is safe to rebuild idempotently.
    """
    __tablename__ = "rc_player_index"
    id: Mapped[int] = mapped_column(primary_key=True)
    search_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    player_count: Mapped[int] = mapped_column(Integer, default=0)
    players_json: Mapped[list] = mapped_column(JSON, default=list)


class PlayerRatingSnapshot(Base):
    """Point-in-time Ratings Central observation.

    The current matchup model deliberately reads only rc_rating. rc_deviation
    and the historical series are persisted now so they can be evaluated and
    incorporated later without changing the data model.
    """
    __tablename__ = "player_rating_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("xttv_players.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    rc_rating: Mapped[float] = mapped_column(Float)
    rc_deviation: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="ratingscentral")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("raw_source_documents.id", ondelete="SET NULL"))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    player: Mapped[XttvPlayer] = relationship(back_populates="rating_snapshots")
    __table_args__ = (
        UniqueConstraint("player_id", "observed_at", "source", name="uq_player_rating_observation"),
        Index("ix_player_rating_player_observed", "player_id", "observed_at"),
    )


class XttvMatch(Base):
    __tablename__ = "xttv_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="xttv")
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    league: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(String(100))
    match_date: Mapped[str | None] = mapped_column(String(30))
    home_team: Mapped[str | None] = mapped_column(String(200))
    away_team: Mapped[str | None] = mapped_column(String(200))
    home_scheme: Mapped[str | None] = mapped_column(String(10))
    away_scheme: Mapped[str | None] = mapped_column(String(10))
    team_result: Mapped[str | None] = mapped_column(String(30))
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    players: Mapped[list[MatchPlayer]] = relationship(back_populates="match", cascade="all, delete-orphan")
    games: Mapped[list[MatchGame]] = relationship(back_populates="match", cascade="all, delete-orphan")


class MatchPlayer(Base):
    __tablename__ = "match_players"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("xttv_matches.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    external_player_id: Mapped[str | None] = mapped_column(String(100))
    side: Mapped[str] = mapped_column(String(10))
    position: Mapped[str | None] = mapped_column(String(2))
    match: Mapped[XttvMatch] = relationship(back_populates="players")
    __table_args__ = (
        UniqueConstraint("match_id", "external_player_id", "side", name="uq_match_player"),
    )


class MatchGame(Base):
    __tablename__ = "match_games"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("xttv_matches.id", ondelete="CASCADE"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    game_type: Mapped[str | None] = mapped_column(String(30))
    home_position: Mapped[str | None] = mapped_column(String(2))
    away_position: Mapped[str | None] = mapped_column(String(2))
    home_player: Mapped[str | None] = mapped_column(String(200))
    away_player: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[str | None] = mapped_column(String(100))
    sets: Mapped[str | None] = mapped_column(Text)
    raw_row: Mapped[str | None] = mapped_column(Text)
    match: Mapped[XttvMatch] = relationship(back_populates="games")
