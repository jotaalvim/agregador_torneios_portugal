import enum

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class Status(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class CorrectionStatus(str, enum.Enum):
    pending = "pending"
    resolved = "resolved"  # applied through the edit form, or handled without changes
    rejected = "rejected"


class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String(200), nullable=False)
    district = Column(String(100), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    location = Column(String(200), nullable=False)
    time_control = Column(String(150), nullable=False)
    organizer = Column(String(200), nullable=True)
    chess_results_url = Column(String(500), nullable=True)
    regulation_url = Column(String(500), nullable=True)
    registration_url = Column(String(500), nullable=True)
    link = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)

    status = Column(Enum(Status), nullable=False, default=Status.pending, index=True)
    submitted_by_name = Column(String(200), nullable=True)
    submitted_by_email = Column(String(200), nullable=True)
    admin_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # SQLite does not enforce foreign keys here, so deleting a tournament
    # through the app removes its corrections via the ORM instead.
    corrections = relationship("Correction", back_populates="tournament", cascade="all, delete-orphan")


class Correction(Base):
    """A visitor's suggested fix or note for a published tournament.

    Never applied on its own: an admin reviews it in the normal edit form.
    `changes` holds only the fields the visitor actually changed, as
    {field: {"old": value when suggested, "new": suggested value}}, so the
    review can tell when the tournament was edited after the suggestion.
    """

    __tablename__ = "corrections"
    # SQLite would otherwise hand a deleted correction's id to the next one,
    # and a stale reject button for the old one would close the new one.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False, index=True)

    changes = Column(JSON, nullable=False, default=dict)
    cancelled = Column(Boolean, nullable=False, default=False)
    note = Column(Text, nullable=True)
    source_url = Column(String(500), nullable=True)
    submitted_by_name = Column(String(200), nullable=True)
    submitted_by_email = Column(String(200), nullable=True)

    status = Column(Enum(CorrectionStatus), nullable=False, default=CorrectionStatus.pending, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    tournament = relationship("Tournament", back_populates="corrections")
