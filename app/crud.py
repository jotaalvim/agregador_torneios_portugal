from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from .models import Status, Tournament
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
