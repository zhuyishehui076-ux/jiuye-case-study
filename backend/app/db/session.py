"""引擎与会话工厂。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

_isolation_level = None
_pool_options = {}
if settings.database_url.startswith("postgresql"):
    # T16 confirm/rollback deliberately relies on statement-level fresh
    # snapshots after waiting for target-row locks.  Pin the contract instead
    # of inheriting a cluster-level default such as REPEATABLE READ.
    _isolation_level = "READ COMMITTED"
    _pool_options = {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_recycle": settings.database_pool_recycle_seconds,
    }

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    isolation_level=_isolation_level,
    pool_pre_ping=True,
    future=True,
    **_pool_options,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
