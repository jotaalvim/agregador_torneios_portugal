import enum

from sqlalchemy import Column, Date, DateTime, Enum, Integer, String, Text
from sqlalchemy.sql import func

from .database import Base


class Status(str, enum.Enum):
    pending = "pending"
    approved = "approved"
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
