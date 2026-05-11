import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _make_async_url(url: str) -> str:
    """Converte qualquer variante de URL postgres para postgresql+asyncpg://.

    O Railway fornece 'postgres://' (forma curta). O SQLAlchemy + asyncpg
    exige 'postgresql+asyncpg://'. Tratamos os três casos possíveis.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url  # já está correto
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def build_engine():
    settings = get_settings()
    url = _make_async_url(settings.database_url)
    return create_async_engine(url, echo=False, pool_pre_ping=True)


engine = build_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Colunas adicionadas em versões posteriores — IF NOT EXISTS é idempotente
        # asyncpg não aceita múltiplos comandos num único prepared statement
        await conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS pinnacle_closing_odd FLOAT"
        ))
        await conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS clv_percent FLOAT"
        ))
        await conn.execute(text(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS clv_recorded_at TIMESTAMPTZ"
        ))
    logger.info("Tabelas criadas/verificadas com sucesso")
