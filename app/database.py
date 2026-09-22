import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATA_DIR, DATABASE_URL

os.makedirs(DATA_DIR, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    # FastAPI runs sync routes in a thread pool, so several requests can hit
    # SQLite concurrently. WAL + a busy timeout lets writers/readers overlap
    # instead of raising "database is locked".
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Base.metadata.create_all only creates missing tables, it never alters an
# existing one — so a column added to models.py needs a manual ADD COLUMN
# for databases created before that change.
def run_migrations() -> None:
    inspector = inspect(engine)
    if "tournaments" not in inspector.get_table_names():
        return
    existing_columns = {col["name"] for col in inspector.get_columns("tournaments")}
    if "registration_url" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tournaments ADD COLUMN registration_url VARCHAR(500)"))
