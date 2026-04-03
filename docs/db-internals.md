# DB internals — struttura `db/`

Modulo `db/` in `agents/db/` (o `db/` al root del progetto Python).
Gestisce sessione SQLAlchemy async, configurazione engine, metadati modelli.

---

## Struttura directory

```
db/
├── __init__.py
├── session.py          ← get_db_session() context manager (import qui)
├── engine.py           ← AsyncEngine + AsyncSessionFactory
├── base.py             ← DeclarativeBase condivisa da tutti i modelli
└── models/
    ├── __init__.py
    ├── lead.py
    ├── deal.py
    ├── client.py
    ├── proposal.py
    ├── task.py
    ├── run.py
    ├── service_delivery.py
    ├── delivery_report.py
    ├── email_log.py
    ├── ticket.py
    ├── invoice.py
    └── nps_record.py
```

---

## `db/engine.py`

```python
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
import os

engine: AsyncEngine = create_async_engine(
    os.environ["DATABASE_URL"],      # postgresql+asyncpg://...
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autobegin=True,
)
```

---

## `db/session.py` — `get_db_session()`

Unico punto di ingresso per aprire una sessione DB negli agenti e nei tool.
Gestisce commit e rollback automaticamente.

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from db.engine import AsyncSessionFactory

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

**Import:** `from db.session import get_db_session`

La sessione viene aperta e chiusa dentro il `with` block. Non tenerla aperta tra task
diversi — una sessione per task (già gestito da `BaseAgent.run()`).

---

## `db/base.py`

```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

Tutti i modelli SQLAlchemy ereditano da questa `Base`.
Le Alembic migrations usano `Base.metadata` per generare gli script.

---

## Alembic

```
alembic/
├── env.py          ← importa Base e DATABASE_SYNC_URL da env
├── script.py.mako
└── versions/
    └── *.py        ← ogni migrazione è un file separato
```

Comandi:
```bash
alembic upgrade head         # applica tutte le migrazioni
alembic revision --autogenerate -m "descrizione"   # genera nuova migrazione
alembic downgrade -1         # rollback ultima migrazione
```

`DATABASE_SYNC_URL` (env) è usata da Alembic — usa driver `psycopg2` (sync),
non `asyncpg`.
