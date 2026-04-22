"""KnowledgeBridge — identifica pattern cross-domain e li scrive in shared_memory.

Attivato via callback ogni volta che un nuovo insight viene salvato in
pepe_memory (dominio Etsy) o personal_memory (dominio Personal).

Pipeline:
1. Query il dominio opposto con il testo appena inserito (top 2 risultati)
2. Se esistono risultati, chiede a Claude Haiku (gate check caveman) se i due
   contenuti condividono un topic rilevante: YES | NO
3. Se YES: sintetizza un insight cross-domain con Haiku (max 80 parole)
4. Scrive la sintesi in shared_memory con metadati di tracciabilità

Usa Claude Haiku per coerenza con il resto del sistema (tutti gli agenti
usano Haiku — _call_llm_ollama in base.py è solo un wrapper Anthropic).
Nessuna dipendenza da Ollama locale.

Fail-safe totale: qualsiasi errore viene loggato silenziosamente.
La pipeline principale non viene mai bloccata — questo modulo è sempre
in background, mai nel critical path.

Deduplicazione semplice: tiene in memoria (processo) gli ultimi
_DEDUP_CACHE_SIZE hash (text_a[:32] + text_b[:32]) per evitare
bridge insight identici in sessione.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import anthropic

from apps.backend.core.config import MODEL_HAIKU, settings

if TYPE_CHECKING:
    from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.knowledge_bridge")

# ------------------------------------------------------------------
# Prompt caveman per step interni (Claude Haiku)
# ------------------------------------------------------------------

_GATE_SYSTEM = (
    "Do these two texts share a relevant topic? Answer ONLY: YES or NO.\n"
    "YES only if they cover the same subject from different angles.\n"
    "NO if they are unrelated or one is too generic."
)

_SYNTH_SYSTEM = (
    "Sei Pepe, assistente di Andrea. "
    "Hai due informazioni da domini diversi (Etsy e Personale) che parlano dello stesso argomento. "
    "Scrivi UN insight cross-domain in italiano, max 80 parole. "
    "Formato: 'Connessione rilevata: [argomento]. [insight concreto].' "
    "Niente intro. Solo il fatto utile."
)

# Testo minimo perché valga la pena fare il gate check
_MIN_TEXT_LEN = 80

# Massimo testo inviato a Haiku per contenere i token
_GATE_PREVIEW_CHARS = 500
_SYNTH_PREVIEW_CHARS = 600

# Cache deduplicazione in-memory (hashes di coppie già processate)
_DEDUP_CACHE_SIZE = 200


class KnowledgeBridge:
    """Bridge asincrono che rileva e sintetizza pattern cross-domain.

    Registrato come callback in MemoryManager:
        memory.set_bridge_callback(bridge.on_new_insight)

    Non eredita da AgentBase — è un servizio, non un agente.
    Usa Claude Haiku (Anthropic) per coerenza con il resto del sistema.
    """

    def __init__(self, memory: "MemoryManager") -> None:
        self.memory = memory
        self._client: anthropic.AsyncAnthropic | None = None   # lazy-init
        self._dedup: deque[str] = deque(maxlen=_DEDUP_CACHE_SIZE)

    # ------------------------------------------------------------------
    # Entry point (callback registrato in MemoryManager)
    # ------------------------------------------------------------------

    async def on_new_insight(self, text: str, source_domain: str) -> None:
        """Chiamato dopo ogni store_insight / store_personal_insight.

        Tutto fail-safe: qualsiasi eccezione viene loggata e inghiottita.
        """
        try:
            await self._process(text, source_domain)
        except Exception as exc:
            logger.debug("KnowledgeBridge.on_new_insight fallito (fail-safe): %s", exc)

    # ------------------------------------------------------------------
    # Pipeline interna
    # ------------------------------------------------------------------

    async def _process(self, text: str, source_domain: str) -> None:
        """Pipeline bridge in 4 step."""
        if len(text) < _MIN_TEXT_LEN:
            return  # testo troppo corto — skip silenzioso

        # ── Step 1: query dominio opposto ─────────────────────────────
        opposite = "personal" if source_domain == "etsy" else "etsy"
        cross_results = await self._query_opposite(text, opposite)
        if not cross_results:
            return  # nessun contenuto nel dominio opposto — normale all'inizio

        best_cross = cross_results[0]
        cross_text = best_cross.get("document", "")
        if len(cross_text) < _MIN_TEXT_LEN:
            return

        # ── Step 2: deduplicazione ────────────────────────────────────
        pair_hash = self._pair_hash(text, cross_text)
        if pair_hash in self._dedup:
            logger.debug("KnowledgeBridge: coppia già processata, skip")
            return
        self._dedup.append(pair_hash)

        # ── Step 3: gate check ────────────────────────────────────────
        gate_ok = await self._gate_check(text, cross_text)
        if not gate_ok:
            return  # argomenti non correlati — skip

        # ── Step 4: sintesi + store ───────────────────────────────────
        etsy_text    = text      if source_domain == "etsy"     else cross_text
        personal_text = cross_text if source_domain == "etsy"   else text

        synthesis = await self._synthesize(etsy_text, personal_text)
        if not synthesis or len(synthesis) < 20:
            return

        now = datetime.now(timezone.utc)
        await self.memory.store_shared_insight(
            synthesis,
            metadata={
                "source_etsy":     etsy_text[:120],
                "source_personal": personal_text[:120],
                "topic":           synthesis[:60],
                "date":            now.strftime("%Y-%m-%d"),
                "created_at":      now.isoformat(),
                "bridge_version":  "1.0",
            },
        )
        logger.info(
            "KnowledgeBridge: insight cross-domain scritto in shared_memory — '%s'",
            synthesis[:80],
        )

    # ------------------------------------------------------------------
    # Step 1 — query dominio opposto
    # ------------------------------------------------------------------

    async def _query_opposite(self, text: str, opposite_domain: str) -> list[dict]:
        """Interroga la collection del dominio opposto con il testo appena inserito."""
        try:
            if opposite_domain == "etsy":
                return await self.memory.query_chromadb(
                    query=text[:_GATE_PREVIEW_CHARS],
                    n_results=2,
                    agent="knowledge_bridge",
                )
            else:
                return await self.memory.query_personal_memory(
                    query=text[:_GATE_PREVIEW_CHARS],
                    n_results=2,
                    agent="knowledge_bridge",
                )
        except Exception as exc:
            logger.debug("_query_opposite fallito: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Step 3 — gate check (Haiku caveman)
    # ------------------------------------------------------------------

    async def _gate_check(self, text_a: str, text_b: str) -> bool:
        """Chiede a Haiku se i due testi condividono un topic rilevante.

        Ritorna True se YES, False se NO o in caso di errore (fail-closed).
        """
        user = (
            f"TEXT A:\n{text_a[:_GATE_PREVIEW_CHARS]}\n\n"
            f"TEXT B:\n{text_b[:_GATE_PREVIEW_CHARS]}"
        )
        try:
            result = await self._haiku_call(
                system=_GATE_SYSTEM,
                user=user,
                max_tokens=5,
            )
            return (result or "").strip().upper().startswith("YES")
        except Exception as exc:
            logger.debug("_gate_check fallito (fail-closed): %s", exc)
            return False  # fail-closed: preferibile non creare bridge inutili

    # ------------------------------------------------------------------
    # Step 4a — sintesi cross-domain (Haiku)
    # ------------------------------------------------------------------

    async def _synthesize(self, etsy_text: str, personal_text: str) -> str:
        """Genera l'insight cross-domain da scrivere in shared_memory."""
        user = (
            f"Dominio Etsy:\n{etsy_text[:_SYNTH_PREVIEW_CHARS]}\n\n"
            f"Dominio Personale:\n{personal_text[:_SYNTH_PREVIEW_CHARS]}"
        )
        try:
            return await self._haiku_call(
                system=_SYNTH_SYSTEM,
                user=user,
                max_tokens=120,
            )
        except Exception as exc:
            logger.debug("_synthesize fallito: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Haiku helper (lazy-init client, stessa convenzione di AgentBase)
    # ------------------------------------------------------------------

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    async def _haiku_call(
        self,
        system: str,
        user: str,
        max_tokens: int = 120,
    ) -> str:
        """Chiamata Claude Haiku — coerente con _call_llm_ollama in AgentBase."""
        client = self._get_client()
        response = await client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return (response.content[0].text if response.content else "").strip()

    # ------------------------------------------------------------------
    # Deduplicazione
    # ------------------------------------------------------------------

    @staticmethod
    def _pair_hash(text_a: str, text_b: str) -> str:
        """Hash deterministico e ordinato della coppia — indipendente dall'ordine."""
        parts = sorted([text_a[:64], text_b[:64]])
        combined = "||".join(parts).encode()
        return hashlib.sha256(combined).hexdigest()[:24]
