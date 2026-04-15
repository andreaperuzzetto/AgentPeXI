"""StorageManager — gestione file su SSD per AgentPeXI."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.storage")


class StorageManager:
    """Gestione directory pending/uploaded/archived su STORAGE_PATH."""

    def __init__(self, base_path: str | None = None) -> None:
        self._base = Path(base_path or settings.STORAGE_PATH)
        self._pending = self._base / "pending"
        self._uploaded = self._base / "uploaded"
        self._archived = self._base / "archived"

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Crea subdirectory se non esistono."""
        for d in (self._pending, self._uploaded, self._archived):
            d.mkdir(parents=True, exist_ok=True)
        logger.info("Directory storage verificate: %s", self._base)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def get_pending_path(self, filename: str) -> Path:
        return self._pending / filename

    def move_to_uploaded(self, file_path: Path) -> Path:
        dest = self._uploaded / file_path.name
        shutil.move(str(file_path), str(dest))
        logger.info("File spostato in uploaded: %s", dest.name)
        return dest

    def move_to_archived(self, file_path: Path) -> Path:
        dest = self._archived / file_path.name
        shutil.move(str(file_path), str(dest))
        logger.info("File spostato in archived: %s", dest.name)
        return dest

    def archive_old_files(self, days: int = 30) -> int:
        """Archivia file in uploaded/ più vecchi di N giorni."""
        cutoff = datetime.now() - timedelta(days=days)
        count = 0
        for f in self._uploaded.iterdir():
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                self.move_to_archived(f)
                count += 1
        if count:
            logger.info("Archiviati %d file più vecchi di %d giorni", count, days)
        return count

    # ------------------------------------------------------------------
    # Status / health
    # ------------------------------------------------------------------

    @property
    def base_path(self) -> Path:
        """Percorso base pubblico per il Design Agent."""
        return self._base

    def is_available(self) -> bool:
        """True se STORAGE_PATH è montato e scrivibile."""
        return self._base.is_dir() and os.access(str(self._base), os.W_OK)

    def get_disk_usage(self) -> dict:
        """{total, used, free} in bytes."""
        usage = shutil.disk_usage(str(self._base))
        return {"total": usage.total, "used": usage.used, "free": usage.free}

    def list_pending(self) -> list[Path]:
        if not self._pending.is_dir():
            return []
        return [f for f in self._pending.iterdir() if f.is_file()]

    def list_uploaded(self) -> list[Path]:
        if not self._uploaded.is_dir():
            return []
        return [f for f in self._uploaded.iterdir() if f.is_file()]

    def health_check(self) -> dict:
        """Health check completo per scheduler."""
        available = self.is_available()
        free_gb = 0.0
        pending_count = 0

        if available:
            usage = self.get_disk_usage()
            free_gb = usage["free"] / (1024**3)
            pending_count = len(self.list_pending())

        return {
            "available": available,
            "free_gb": round(free_gb, 2),
            "pending_count": pending_count,
        }
