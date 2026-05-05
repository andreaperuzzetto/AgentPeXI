"""WikiManager — knowledge base strutturata per AgentPeXI.

Architettura:
    knowledge_base/
    ├── .manifest.json              ← delta tracker: {raw_path: {compiled_at, wiki_files_updated}}
    ├── raw/{domain}/{agent}/       ← output grezzo agenti (immutabile)
    └── wiki/{domain}/              ← conoscenza compilata (markdown + frontmatter YAML)

LLM routing (determinato dal tipo di client passato):
    anthropic.AsyncAnthropic  → Sonnet  (Etsy wiki)
    openai.AsyncOpenAI        → Ollama  (Personal wiki — privacy totale)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apps.backend.core._wiki._helpers import (  # noqa: F401  (re-exported)
    COMPACTION_LIMITS,
    DEFAULT_HARD_LIMIT,
    NICHE_HARD_LIMIT,
    PERSONAL_HARD_LIMIT,
    _estimate_tokens,
    _parse_frontmatter,
    _slugify,
)
from apps.backend.core._wiki._compile_mixin import _CompileMixin
from apps.backend.core._wiki._io_mixin import _IOMixin
from apps.backend.core._wiki._maintenance_mixin import _MaintenanceMixin
from apps.backend.core._wiki._query_mixin import _QueryMixin

logger = logging.getLogger("agentpexi.wiki")


# ── WikiManager ───────────────────────────────────────────────────────────────

class WikiManager(_MaintenanceMixin, _QueryMixin, _CompileMixin, _IOMixin, object):
    """Gestisce lettura/scrittura della knowledge base strutturata di AgentPeXI."""

    def __init__(self, base_path: Path) -> None:
        self.base_path      = base_path
        self.wiki_path      = base_path / "wiki"
        self.raw_path       = base_path / "raw"
        self._manifest_lock = asyncio.Lock()  # serializza R/W su .manifest.json — obbligatorio
                                              # se due agent completano in parallelo (es.
                                              # research + analytics dallo stesso /pipeline)

    async def init(self) -> None:
        """Crea la struttura di directory e .manifest.json vuoto se non esistono."""
        dirs = [
            self.wiki_path / "etsy" / "niches",
            self.wiki_path / "etsy" / "patterns",
            self.wiki_path / "etsy" / "meta",
            self.wiki_path / "personal",
            self.raw_path / "etsy" / "research",
            self.raw_path / "etsy" / "analytics",
            self.raw_path / "etsy" / "publisher",
            self.raw_path / "personal" / "research",
            self.raw_path / "personal" / "summarize",
            self.raw_path / "personal" / "screen",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        manifest_path = self.base_path / ".manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text("{}", encoding="utf-8")

        logger.info("WikiManager inizializzato su %s", self.base_path)
