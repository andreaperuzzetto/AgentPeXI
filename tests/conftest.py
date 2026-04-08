"""
conftest.py — Fixture globali AgentPeXI

Strategia DB:
- I modelli ORM usano tipi PostgreSQL-specifici (JSONB, ARRAY, pgvector) non supportati
  da SQLite in-memory. Per i test unit e integration, il DB è sostituito con AsyncMock.
- Se TEST_DATABASE_URL è impostato (PostgreSQL reale, es. in CI), le fixture
  `real_db_engine` / `real_db_session` creano lo schema fisico per test e2e.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Event loop — session-scoped per supportare fixture async di sessione
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Loop asyncio riutilizzato per tutta la sessione di test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# DB mock (unit + integration — nessuna infrastruttura reale)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session() -> AsyncMock:
    """
    AsyncMock di AsyncSession per test unit e integration.
    Non richiede PostgreSQL. I singoli test mockano le funzioni tools.db_tools.*
    a livello di modulo, quindi il db passato all'agente è puramente nominale.
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# DB reale — richiede TEST_DATABASE_URL in env (opzionale, usato per e2e)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def real_db_engine():
    """
    Engine PostgreSQL di test. Richiede TEST_DATABASE_URL.
    Skippato se la variabile non è impostata.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL non impostato — skip test con DB reale")

    from sqlalchemy.ext.asyncio import create_async_engine
    from db.base import Base
    # Importa tutti i modelli per registrarli nel metadata
    import db.models  # noqa: F401

    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def real_db_session(real_db_engine) -> AsyncGenerator:
    """Session con rollback automatico al termine del test."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(real_db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------

@pytest.fixture
def redis_client():
    """FakeRedis sincrono per unit test."""
    return fakeredis.FakeRedis()


@pytest_asyncio.fixture
async def async_redis_client():
    """FakeRedis asincrono per test che usano asyncio.Redis."""
    return fakeredis.aioredis.FakeRedis()


# ---------------------------------------------------------------------------
# Celery eager mode (autouse — sempre attivo)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def celery_eager():
    """Forza Celery a eseguire i task in-process, in modo sincrono."""
    try:
        from agents.worker import celery_app
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        yield
        celery_app.conf.task_always_eager = False
        celery_app.conf.task_eager_propagates = False
    except Exception:
        # Se il worker non è ancora disponibile, salta silenziosamente
        yield


# ---------------------------------------------------------------------------
# Env vars di default per i test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_env(monkeypatch):
    """
    Imposta variabili d'ambiente minime per evitare KeyError nei moduli che
    leggono os.environ al momento dell'import o dell'esecuzione.
    """
    defaults = {
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "GOOGLE_MAPS_API_KEY": "test-maps-key",
        "MINIO_ENDPOINT": "http://localhost:9000",
        "MINIO_BUCKET": "agentpexi-test",
        "MINIO_ACCESS_KEY": "minioadmin",
        "MINIO_SECRET_KEY": "minioadmin",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "OPERATOR_EMAIL": "operator@test.local",
        "OPERATOR_NAME": "Test Operator",
        "OPERATOR_PHONE": "+39 000 0000000",
        "SECRET_KEY": "test-secret-key-32-chars-minimum!",
        "PORTAL_SECRET_KEY": "test-portal-secret-key",
        "BASE_URL": "http://localhost:3000",
        "CLIENT_WORKSPACE_ROOT": "/tmp/agentpexi_test_workspaces",
        "OPERATOR_PASSWORD_HASH": "$2b$12$tnmVT7MLRC3bfRcNsEWbxuiTaNsFJKT.MbQ6DfUhv8h9cDCvPl8zK",
    }
    for key, val in defaults.items():
        monkeypatch.setenv(key, os.environ.get(key, val))
    yield
