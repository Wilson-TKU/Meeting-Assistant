from pathlib import Path
from contextlib import contextmanager, asynccontextmanager
from typing import Generator, AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from core.config import settings


class Base(DeclarativeBase):
    pass


def _make_sync_url(url: str) -> str:
    """Ensure the URL uses a sync driver."""
    return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql")


def _make_async_url(url: str) -> str:
    """Convert a sync DB URL to its async driver equivalent."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return url


def _ensure_sqlite_dir(url: str) -> None:
    if "sqlite" in url:
        path = url.split("sqlite")[1].lstrip("+aiosqlite").lstrip("://").lstrip("/")
        Path(path).parent.mkdir(parents=True, exist_ok=True)


# ── Sync engine (used by Celery task workers) ──────────────────────────────
_sync_url = _make_sync_url(settings.database_url)
_ensure_sqlite_dir(_sync_url)

_sync_connect_args = {"check_same_thread": False} if "sqlite" in _sync_url else {}
engine = create_engine(_sync_url, connect_args=_sync_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Async engine (used by FastAPI gateway) ─────────────────────────────────
_async_url = _make_async_url(settings.database_url)
_async_connect_args = {"check_same_thread": False} if "sqlite" in _async_url else {}
async_engine = create_async_engine(_async_url, connect_args=_async_connect_args)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables (sync). Call once at startup from Celery worker or CLI."""
    from core.models import meeting, task, transcript, summary, prompt  # noqa: F401
    Base.metadata.create_all(bind=engine)


async def init_db_async() -> None:
    """Create all tables (async). Call once at FastAPI startup."""
    from core.models import meeting, task, transcript, summary, prompt  # noqa: F401
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Schema migrations (additive only) ─────────────────────────────────────

_MIGRATIONS = [
    "ALTER TABLE summaries ADD COLUMN scene VARCHAR(64)",
]


def run_migrations() -> None:
    """Apply additive schema migrations (sync). Safe to run on every startup."""
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(__import__("sqlalchemy").text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()  # column already exists or other benign error


async def run_migrations_async() -> None:
    """Apply additive schema migrations (async). Safe to run on every startup."""
    async with async_engine.begin() as conn:
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(__import__("sqlalchemy").text(stmt))
            except Exception:
                pass  # column already exists or other benign error


# ── Session context managers ───────────────────────────────────────────────

@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Sync session for Celery task workers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async session context manager (alternative to FastAPI dependency)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
