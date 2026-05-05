from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from apps.backend.core._wiki._helpers import _slugify

logger = logging.getLogger("agentpexi.wiki")


class _IOMixin:
    """IO primitives: raw storage, manifest R/W, file iteration, stats."""

    base_path: Path
    wiki_path: Path
    raw_path: Path
    _manifest_lock: asyncio.Lock

    async def store_raw(self, domain: str, agent: str, data: dict) -> Path:
        """Salva output grezzo in raw/{domain}/{agent}/{timestamp}.json.

        Aggiorna .manifest.json: {raw_path: {compiled_at: null, wiki_files_updated: []}}.
        compiled_at rimane null finché compile_niche/compile_wiki_file non processa il file.
        Tutte le scritture su .manifest.json passano per self._manifest_lock.
        """
        ts        = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        agent_dir = self.raw_path / domain / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        hint      = data.get("niche") or data.get("query") or ""
        slug_part = _slugify(str(hint))[:30] if hint else "raw"
        file_path = agent_dir / f"{ts}_{slug_part}.json"
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        async with self._manifest_lock:
            manifest = self._read_manifest()
            rel      = str(file_path.relative_to(self.base_path))
            manifest[rel] = {"compiled_at": None, "wiki_files_updated": []}
            self._write_manifest(manifest)

        logger.debug("store_raw: %s", file_path.name)
        return file_path

    def _read_manifest(self) -> dict:
        p = self.base_path / ".manifest.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_manifest(self, data: dict) -> None:
        (self.base_path / ".manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _iter_wiki_files(self, domain: str):
        """Itera i file .md della wiki escludendo _index.md e file con prefisso _."""
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return
        yield from (f for f in sorted(domain_path.rglob("*.md")) if not f.name.startswith("_"))

    async def get_stats(self) -> dict:
        """Statistiche rapide per il report Telegram del health check."""
        manifest     = self._read_manifest()
        pending      = sum(1 for v in manifest.values() if v["compiled_at"] is None)
        niches_dir   = self.wiki_path / "etsy" / "niches"
        patterns_dir = self.wiki_path / "etsy" / "patterns"
        return {
            "etsy_niches":   len(list(niches_dir.glob("*.md")))   if niches_dir.exists()   else 0,
            "etsy_patterns": len(list(patterns_dir.glob("*.md"))) if patterns_dir.exists() else 0,
            "pending_raw":   pending,
            "total_raw":     len(manifest),
        }
