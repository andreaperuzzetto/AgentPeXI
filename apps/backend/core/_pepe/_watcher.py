"""Watcher and urgency mixin for Pepe."""
from __future__ import annotations

import logging
import re

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.pepe")

# ------------------------------------------------------------------
# Urgency system — costanti
# ------------------------------------------------------------------

_NOISE_APPS: frozenset[str] = frozenset({
    "Spotify", "Music", "Apple Music", "Netflix", "YouTube", "Prime Video",
    "Steam", "Minecraft", "IINA", "VLC", "Podcasts", "Audible",
    "Disney+", "Twitch", "Discord",
})

_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*[\d\s\W]{0,15}\s*$"),   # solo numeri/simboli
    re.compile(r"^.{0,9}$"),                   # meno di 10 caratteri
]

# Prompt Ollama caveman per classificazione urgenza
_URGENCY_SYSTEM = (
    "Rate urgency. Output ONLY this format:\n"
    "LEVEL: HIGH|MEDIUM|LOW\n"
    "REASON: max 8 words, italian\n"
    "---\n"
    "HIGH = azione richiesta oggi, scadenza, finanziario, medico\n"
    "MEDIUM = info utile, nessuna azione immediata\n"
    "LOW = intrattenimento, navigazione generica, rumore"
)


class WatcherMixin:

    def _is_obvious_noise(self, text: str, source: str) -> bool:
        """Pre-filter rapido: True se sicuramente rumore, senza chiamate LLM.

        Quando source == 'watcher' controlla _last_watcher_app (l'app attiva al
        momento della cattura) invece di source stesso, perché source indica
        l'origine della cattura, non l'applicazione.
        """
        app_to_check = self._last_watcher_app if source == "watcher" else source
        if app_to_check in _NOISE_APPS:
            return True
        for pattern in _NOISE_PATTERNS:
            if pattern.match(text):
                return True
        return False

    async def _ollama_urgency_classify(
        self, text: str, source: str = "", context: str = ""
    ) -> tuple[str, str]:
        """Classifica urgenza via Ollama qwen3:8b con caveman prompt.

        Ritorna (level, reason) — level in {"HIGH", "MEDIUM", "LOW"}.
        Timeout 8 s, fallback LOW su qualsiasi errore.
        """
        import aiohttp
        from datetime import datetime as _dt

        now = _dt.now()
        _WEEKDAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        weekday_it = _WEEKDAYS_IT[now.weekday()]

        parts = [f"TEXT: {text[:500]}"]
        if source:
            parts.append(f"Source: {source}")
        parts.append(f"Context: {now.hour}:00 {weekday_it}")
        if context:
            parts.append(f"Hint: {context[:100]}")
        user_msg = "\n".join(parts)

        try:
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(settings.OLLAMA_BASE_URL)
            _ollama_chat_url = f"{_parsed.scheme}://{_parsed.netloc}/api/chat"

            timeout = aiohttp.ClientTimeout(total=settings.URGENCY_OLLAMA_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    _ollama_chat_url,
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "messages": [
                            {"role": "system", "content": _URGENCY_SYSTEM},
                            {"role": "user",   "content": user_msg},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 40},
                    },
                ) as resp:
                    data = await resp.json()

            raw = data.get("message", {}).get("content", "").strip()
            level = "LOW"
            reason = "non classificato"
            for line in raw.splitlines():
                if line.startswith("LEVEL:"):
                    val = line.split(":", 1)[1].strip().upper()
                    if val in {"HIGH", "MEDIUM", "LOW"}:
                        level = val
                elif line.startswith("REASON:"):
                    reason = line.split(":", 1)[1].strip()
            return level, reason

        except Exception as exc:
            logger.warning("Ollama urgency classify fallito: %s", exc)
            return "LOW", "timeout o errore classificatore"

    async def _apply_user_rules(self, level: str, text: str) -> str:
        """Sovrascrive il livello in base alle regole apprese dall'utente.

        Legge personal_learning per agent="urgency", pattern_type="keyword".
        weight > 0.7 → promuove a HIGH; weight < 0.3 → degrada a MEDIUM.
        """
        try:
            patterns = await self.memory.get_learning_patterns(
                agent="urgency", pattern_type="keyword"
            )
            text_lower = text.lower()
            for p in patterns:
                kw = p.get("pattern_value", "").lower()
                if not kw or kw not in text_lower:
                    continue
                w = p.get("weight", 0.5)
                # Verifica acceptance rate: applica solo pattern con signal history utile
                acceptance_rate = await self.memory.get_pattern_acceptance_rate(kw, last_n=20)
                if acceptance_rate < 0.5:
                    logger.debug(
                        "Pattern signal '%s' ha acceptance rate bassa (%.2f), skip", kw, acceptance_rate
                    )
                    continue
                if w > 0.7 and level in ("MEDIUM", "LOW"):
                    return "HIGH"
                if w < 0.3 and level == "HIGH":
                    return "MEDIUM"
        except Exception as exc:
            logger.debug("_apply_user_rules fallito: %s", exc)
        return level

    async def score_urgency(
        self, text: str, source: str = "", context: str = ""
    ) -> tuple[str, str]:
        """Pipeline completa: pre-filter → Ollama classify → user rules.

        Ritorna (level, reason).
        """
        if self._is_obvious_noise(text, source):
            return "LOW", "filtro rumore"
        level, reason = await self._ollama_urgency_classify(text, source=source, context=context)
        level = await self._apply_user_rules(level, text)
        return level, reason

    async def _propose_action(self, text: str, reason: str, source: str) -> None:
        """Invia a Telegram una proposta di azione su cattura HIGH.

        Salva come pending_action per l'handler sì/no in handle_user_message.
        """
        msg = (
            f"⚠️ Rilevato qualcosa da gestire:\n"
            f"«{text[:200]}»\n"
            f"Motivo: {reason}\n\n"
            f"Vuoi che lo gestisca? (rispondi sì/no)"
        )
        await self.notify_telegram(msg, priority=True)
        await self.memory.save_pending_action(
            action_type="urgency_proposal",
            payload={"text": text, "source": source, "reason": reason},
        )

    @staticmethod
    def _sanitize_ocr_input(text: str, max_len: int = 500) -> str:
        """Sanifica testo OCR prima dell'inserimento in un prompt LLM.

        Tronca alla lunghezza massima e rimuove sequenze tipiche di prompt injection.
        """
        text = text.strip()[:max_len]
        text = re.sub(
            r"(?i)(ignore\s+(previous|all|above|prior)\s+instructions?"
            r"|system\s*:|<\s*/?system\s*>|\[\s*system\s*\]"
            r"|assistant\s*:|<\s*/?assistant\s*>"
            r"|\\n---\\n|---END---|<\|im_end\|>|<\|im_start\|>)",
            "",
            text,
        )
        return text.strip()

    async def process_watcher_capture(self, text: str, app_name: str) -> None:
        """Punto di ingresso per ogni cattura dello ScreenWatcher.

        Aggiorna _last_watcher_app, valuta urgenza, propone azione se HIGH.
        Buffering MEDIUM: accumula fino a 5 catture poi invia riepilogo.
        """
        # Sanitizza il testo OCR prima di qualsiasi uso nei prompt LLM
        text = self._sanitize_ocr_input(text)
        self._last_watcher_app = app_name

        # source="watcher" → _is_obvious_noise userà _last_watcher_app per il check NOISE_APPS
        level, reason = await self.score_urgency(text, source="watcher")
        logger.info(
            "Watcher capture — app=%s level=%s reason=%s",
            app_name, level, reason[:60],
        )

        if level == "HIGH":
            await self._propose_action(text, reason, source=app_name)

        elif level == "MEDIUM":
            # Accumula nel buffer — il flush avviene via CronTrigger alle 18:00
            async with self._medium_buffer_lock:
                self._urgency_medium_buffer.append(
                    {"text": text, "app": app_name, "reason": reason}
                )
        # LOW: silenzio — nessuna azione

    async def flush_medium_digest(self) -> None:
        """Invia il digest giornaliero dei MEDIUM e svuota il buffer.

        Chiamato dal job CronTrigger alle URGENCY_MEDIUM_DIGEST_HOUR.
        Se il buffer è vuoto non invia nulla.
        """
        async with self._medium_buffer_lock:
            if not self._urgency_medium_buffer:
                return
            snapshot = list(self._urgency_medium_buffer)
            self._urgency_medium_buffer.clear()
        lines = [
            f"• [{e['app']}] {e['text'][:80]} — {e['reason']}"
            for e in snapshot
        ]
        summary = "\n".join(lines)
        count = len(snapshot)
        await self.notify_telegram(
            f"📋 Riepilogo giornaliero ({count} eventi):\n{summary}"
        )
