"""
Тестовая инфраструктура: поднимаем приложение на in-memory SQLite, без postgres.

ARRAY-колонки (permissions) на SQLite не поддерживаются нативно — рендерим их
как JSON только для DDL (тесты в эти колонки не пишут). Зависимость get_db
переопределяется на тестовую сессию; логирующий middleware тоже перенаправляем
на ту же БД, чтобы он не лез в реальный postgres.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/x")
os.environ.setdefault("BANK_CODE", "vbank")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy import ARRAY
from sqlalchemy.ext.compiler import compiles


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

import models
import main
import middleware as mw
from database import get_db


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_maker(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_maker, monkeypatch):
    async def override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    main.app.dependency_overrides[get_db] = override_get_db
    # middleware логирует через собственный get_db -> направим в тестовую БД
    monkeypatch.setattr(mw, "get_db", override_get_db)

    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        yield c

    main.app.dependency_overrides.clear()
