from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

import db.engine as _engine_module


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with _engine_module.AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
