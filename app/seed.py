"""One-time seed of the initial tournaments (migrated from the old Hakyll
markdown files). Runs automatically on startup only if the table is empty,
so it never overwrites admin edits or new submissions."""

import json
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from . import crud
from .models import Status, Tournament

SEED_FILE = Path(__file__).parent / "seed_data.json"


def seed_if_empty(db: Session) -> None:
    if db.query(Tournament).count() > 0:
        return
    raw = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for entry in raw:
        entry = dict(entry)
        entry["date"] = date.fromisoformat(entry["date"])
        crud.create_tournament(db, entry, status=Status.approved)
