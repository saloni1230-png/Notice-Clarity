from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    url = settings().database_url
    opts = (
        {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else {
            "pool_size": 8,
            "max_overflow": 8,
            "pool_timeout": 15,
            "connect_args": {"options": "-c statement_timeout=30000 -c lock_timeout=5000 -c timezone=UTC"},
        }
    )
    return create_engine(url, pool_pre_ping=True, **opts)


@contextmanager
def session_scope():
    with Session(engine(), expire_on_commit=False) as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def get_db():
    with session_scope() as db:
        yield db
