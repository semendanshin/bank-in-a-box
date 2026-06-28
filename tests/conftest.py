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

import json

import pytest
import pytest_asyncio
from sqlalchemy import ARRAY, String, TypeDecorator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

import models
import main
import middleware as mw
from database import get_db


class _JSONList(TypeDecorator):
    """ARRAY(String) -> JSON-строка для SQLite (postgres-ARRAY там не работает)."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return json.loads(value) if value else None


# Подменяем ARRAY-колонки на JSON только в тестовой схеме (in-memory SQLite),
# чтобы можно было писать/читать списки (permissions) без postgres.
for _table in models.Base.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, ARRAY):
            _col.type = _JSONList()


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
