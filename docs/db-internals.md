# DB internals — struttura `db/`

Modulo `db/` vive in **`backend/src/db/`** (src layout — vedi `docs/project-structure.md`).
Grazie al src layout (`cd backend && pip install -e .` o `PYTHONPATH=backend/src`), tutti gli import usano
`from db.* import ...` senza prefisso `backend.`.

Gestisce sessione SQLAlchemy async, configurazione engine, metadati modelli.

---

## Struttura directory

```
backend/src/db/
├── __init__.py
├── session.py          ← get_db_session() context manager (import qui)
├── engine.py           ← AsyncEngine + AsyncSessionFactory
├── base.py             ← DeclarativeBase condivisa da tutti i modelli
└── models/
    ├── __init__.py     ← importa tutti i modelli (obbligatorio per Alembic autogenerate)
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

`backend/src/db/models/__init__.py` deve importare tutti i modelli in modo che
Alembic `--autogenerate` li veda:

```python
# backend/src/db/models/__init__.py
from db.models.lead             import Lead
from db.models.deal             import Deal
from db.models.client           import Client
from db.models.proposal         import Proposal
from db.models.task             import Task
from db.models.run              import Run
from db.models.service_delivery import ServiceDelivery
from db.models.delivery_report  import DeliveryReport
from db.models.email_log        import EmailLog
from db.models.ticket           import Ticket
from db.models.invoice          import Invoice
from db.models.nps_record       import NpsRecord

__all__ = [
    "Lead", "Deal", "Client", "Proposal", "Task", "Run",
    "ServiceDelivery", "DeliveryReport", "EmailLog",
    "Ticket", "Invoice", "NpsRecord",
]
```

---

## `db/engine.py`

```python
# backend/src/db/engine.py
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
# backend/src/db/session.py
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
# backend/src/db/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

Tutti i modelli SQLAlchemy ereditano da questa `Base`.
Le Alembic migrations usano `Base.metadata` per generare gli script.

---

## Alembic

```
backend/alembic/
├── env.py          ← aggiunge backend/src/ al sys.path; importa Base e tutti i modelli
├── script.py.mako
└── versions/
    └── *.py        ← ogni migrazione è un file separato
```

```python
# backend/alembic/env.py — head obbligatoria (src layout)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from alembic import context
config = context.config

from db.base import Base
from db.models import *     # importa tutti i modelli per autogenerate
import os

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_SYNC_URL"])
target_metadata = Base.metadata
```

Comandi:
```bash
alembic upgrade head         # applica tutte le migrazioni
alembic revision --autogenerate -m "descrizione"   # genera nuova migrazione
alembic downgrade -1         # rollback ultima migrazione
```

`DATABASE_SYNC_URL` (env) è usata da Alembic — usa driver `psycopg2` (sync),
non `asyncpg`.
