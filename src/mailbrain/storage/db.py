"""SQLite engine, schema creation, and session factory."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.storage.models import Base


def make_engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)


def session_factory(db_path: Path) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(db_path), future=True)
