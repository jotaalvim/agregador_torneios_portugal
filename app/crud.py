from datetime import datetime, timezone

from sqlalchemy import distinct, or_, select
from sqlalchemy.orm import Session

from .models import Correction, CorrectionStatus, Status, Tournament
from .utils import slugify


def list_approved(db: Session) -> list[Tournament]:
    return list(db.scalars(select(Tournament).where(Tournament.status == Status.approved)))


def list_approved_by_district_slug(db: Session, slug: str) -> tuple[str | None, list[Tournament]]:
    tournaments = list_approved(db)
    for t in tournaments:
        if slugify(t.district) == slug:
            district_name = t.district
            break
    else:
        return None, []
    return district_name, [t for t in tournaments if slugify(t.district) == slug]


def get_district_names(db: Session) -> list[str]:
    rows = db.scalars(select(distinct(Tournament.district)).where(Tournament.status == Status.approved))
    return list(rows)


def get_tournament(db: Session, tournament_id: int) -> Tournament | None:
    return db.get(Tournament, tournament_id)


def list_pending(db: Session) -> list[Tournament]:
    return list(
        db.scalars(
            select(Tournament).where(Tournament.status == Status.pending).order_by(Tournament.created_at)
        )
    )


def list_all(db: Session) -> list[Tournament]:
    return list(db.scalars(select(Tournament).order_by(Tournament.date.desc())))


def create_tournament(db: Session, data: dict, status: Status) -> Tournament:
    t = Tournament(status=status, **data)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def update_tournament(db: Session, t: Tournament, data: dict) -> Tournament:
    for key, value in data.items():
        setattr(t, key, value)
    db.commit()
    db.refresh(t)
    return t


def set_status(db: Session, t: Tournament, status: Status) -> Tournament:
    t.status = status
    db.commit()
    db.refresh(t)
    return t


def delete_tournament(db: Session, t: Tournament) -> None:
    db.delete(t)
    db.commit()


def create_correction(db: Session, data: dict) -> Correction:
    c = Correction(**data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def get_correction(db: Session, correction_id: int) -> Correction | None:
    return db.get(Correction, correction_id)


def correction_belongs(c: Correction) -> bool:
    """False for a correction left behind by a tournament deleted by hand in
    SQL (which skips the ORM cascade) once SQLite gives that tournament's id
    to a newer one: a correction is never older than its tournament."""
    t = c.tournament
    if t is None:
        return False
    return t.created_at is None or c.created_at is None or t.created_at <= c.created_at


def list_pending_corrections(db: Session) -> list[Correction]:
    # Same rule as correction_belongs, in SQL: the join drops corrections
    # whose tournament is gone, the date check those whose id was reused.
    return list(
        db.scalars(
            select(Correction)
            .join(Correction.tournament)
            .where(Correction.status == CorrectionStatus.pending)
            .where(or_(
                Tournament.created_at.is_(None),
                Correction.created_at.is_(None),
                Tournament.created_at <= Correction.created_at,
            ))
            .order_by(Correction.created_at, Correction.id)
        )
    )


def set_correction_status(db: Session, c: Correction, status: CorrectionStatus) -> Correction:
    c.status = status
    c.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return c
