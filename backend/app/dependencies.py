"""Shared database dependency; importing it does not open a connection."""
from app.database import SessionLocal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
