from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

_engine = None
_session_factory = None

async def init_db(url: str):
    global _engine, _session_factory
    _engine = create_async_engine(url, future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    from app.database.models import ProcessedMessage, DetectedTask  # noqa
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def session_factory():
    if _session_factory is None:
        raise RuntimeError("Database not initialized")
    return _session_factory

async def get_session() -> AsyncSession:
    return session_factory()()
