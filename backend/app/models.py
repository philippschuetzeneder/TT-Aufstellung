from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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

class XttvMatch(Base):
    __tablename__ = "xttv_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="xttv")
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
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
    side: Mapped[str | None] = mapped_column(String(20))
    position: Mapped[int | None] = mapped_column(Integer)
    match: Mapped[XttvMatch] = relationship(back_populates="players")
    __table_args__ = (UniqueConstraint("match_id", "name", "side", name="uq_match_player"),)

class MatchGame(Base):
    __tablename__ = "match_games"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("xttv_matches.id", ondelete="CASCADE"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    game_type: Mapped[str | None] = mapped_column(String(30))
    home_player: Mapped[str | None] = mapped_column(String(200))
    away_player: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[str | None] = mapped_column(String(100))
    raw_row: Mapped[str | None] = mapped_column(Text)
    match: Mapped[XttvMatch] = relationship(back_populates="games")
