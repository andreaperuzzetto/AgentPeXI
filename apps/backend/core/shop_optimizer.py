"""ShopProfileOptimizer — SEO automatizzato del profilo shop Etsy.

Blocco 5 — step 5.1

Motivazione (video Alfie 2026):
  Il profilo shop (titolo, about/announcement) è letto dall'algoritmo Etsy
  per assegnare contesto SEO a tutti i listing del shop. Un profilo ottimizzato
  con le keyword delle top niches aumenta le impressioni organiche.

Logica:
  1. Legge top-performing niches da niche_intelligence (LearningLoop).
  2. Genera titolo shop (max 55 char, keyword-focused).
  3. Genera About text via Haiku (max ~300 parole, friendly + keyword).
  4. Confronta con l'ultima applicazione (cache in config DB).
  5. Se le niches sono cambiate → applica via Etsy API.
  6. Scheduling lunedì 07:00 — aggiorna solo se necessario.

Invarianti:
  - apply_shop_profile() aggiorna solo se top niches sono cambiate dall'ultima
    applicazione — nessun update ridondante.
  - Mock mode: nessuna chiamata Etsy API, ritorna {status: "mock"}.
  - LLM: Haiku (testo breve, costo minimo).
  - Titolo shop: max 55 caratteri (limite Etsy) — troncato con "…" se necessario.
  - About: applicato come shop announcement (campo disponibile via Etsy API v3).
  - Fail-safe totale: qualsiasi eccezione loggata, non propagata dal job scheduler.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from apps.backend.core.config import MODEL_HAIKU, settings
from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.shop_optimizer")

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------
_SHOP_TITLE_MAX_CHARS = 55          # limite Etsy per shop title
_ABOUT_MAX_WORDS      = 300         # target testo about (Haiku prompt)
_CONFIG_KEY_NICHES    = "shop_optimizer.last_applied_niches"  # JSON array in config
_CONFIG_KEY_TITLE     = "shop_optimizer.last_applied_title"
_FALLBACK_NICHES      = ["digital printables", "wall art", "planner printables"]
_DEFAULT_TAGLINE      = "Instant-download digital products"


class ShopProfileOptimizer:
    """
    Ottimizza il profilo shop Etsy con keyword derivate dalle top niches.

    Dipendenze:
      - memory:        MemoryManager  (config DB + get_db)
      - etsy_client:   EtsyAPI | None (opzionale — None → solo preview)
      - learning_loop: LearningLoop | None (opzionale — fallback niches statiche)
      - mock_mode:     bool           (se True, nessuna chiamata API)
    """

    def __init__(
        self,
        memory: MemoryManager,
        etsy_client: Any | None = None,
        learning_loop: Any | None = None,
        mock_mode: bool = False,
    ) -> None:
        self._memory        = memory
        self._etsy          = etsy_client
        self._learning_loop = learning_loop
        self._mock_mode     = mock_mode
        self._llm           = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY
        )

    # ------------------------------------------------------------------
    # Metodi pubblici principali
    # ------------------------------------------------------------------

    async def generate_shop_title(self, top_niches: list[str]) -> str:
        """
        Costruisce il titolo shop (max 55 char) con le keyword delle top niches.

        Pattern: "{Tagline} — {kw1}, {kw2}, {kw3}"
        Troncato con "…" se supera il limite.
        """
        niches  = top_niches[:3] if top_niches else _FALLBACK_NICHES[:3]
        kw_part = ", ".join(n.title() for n in niches)
        title   = f"{_DEFAULT_TAGLINE} — {kw_part}"

        if len(title) > _SHOP_TITLE_MAX_CHARS:
            title = title[: _SHOP_TITLE_MAX_CHARS - 1].rstrip(", ") + "…"

        return title

    async def generate_shop_about(self, top_niches: list[str]) -> str:
        """
        Genera l'About section via Haiku (~300 parole).

        Struttura del testo generato:
          - Frase apertura con prodotti e keyword principali
          - 2-3 frasi benefits (instant download, professional quality, personalizzazione)
          - Call to action finale

        Fail-safe: se la chiamata LLM fallisce, ritorna testo statico di fallback.
        """
        niches   = top_niches[:5] if top_niches else _FALLBACK_NICHES
        kw_list  = ", ".join(niches)

        prompt = (
            f"Write a professional Etsy shop About section (max {_ABOUT_MAX_WORDS} words) "
            f"for a shop selling digital printables. "
            f"Top product categories: {kw_list}. "
            f"Include keywords naturally. "
            f"Highlight: instant download, high-quality designs, "
            f"personal and commercial use friendly. "
            f"End with a simple call-to-action like 'Browse the shop and find your perfect printable!'. "
            f"Tone: friendly and professional. No emojis. Plain text only."
        )

        try:
            response = await self._llm.messages.create(
                model=MODEL_HAIKU,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()

        except Exception as exc:
            logger.warning(
                "generate_shop_about LLM fallita (%s) — uso fallback statico", exc
            )
            return self._static_about(niches)

    async def apply_shop_profile(
        self,
        focus_niche: str | None = None,
        force: bool = False,
    ) -> dict:
        """
        Pipeline completa: legge niches → genera titolo + about → applica via Etsy API.

        Args:
            focus_niche: se fornita, viene messa in prima posizione nelle niches.
            force:       se True, applica anche se le niches non sono cambiate.

        Ritorna:
            {
              title:   str,
              about:   str,
              status:  "applied" | "mock" | "skipped" | "no_api",
              niches:  list[str],
              changed: bool,
            }
        """
        top_niches = await self._get_top_niches(focus_niche)
        changed    = force or await self._has_niches_changed(top_niches)

        title = await self.generate_shop_title(top_niches)
        about = await self.generate_shop_about(top_niches)

        result = {
            "title":   title,
            "about":   about,
            "niches":  top_niches,
            "changed": changed,
        }

        if not changed:
            result["status"] = "skipped"
            logger.info(
                "ShopProfileOptimizer: niches invariate, nessun aggiornamento necessario"
            )
            return result

        if self._mock_mode:
            await self._save_applied_niches(top_niches, title)
            result["status"] = "mock"
            logger.info(
                "ShopProfileOptimizer [MOCK]: titolo='%s' niches=%s", title, top_niches
            )
            return result

        if self._etsy is None:
            result["status"] = "no_api"
            logger.warning("ShopProfileOptimizer: etsy_client non disponibile — solo preview")
            return result

        try:
            await self._etsy.update_shop(title=title, announcement=about)
            await self._save_applied_niches(top_niches, title)
            result["status"] = "applied"
            logger.info(
                "ShopProfileOptimizer: profilo aggiornato — titolo='%s'", title
            )
        except Exception as exc:
            logger.error("ShopProfileOptimizer: update_shop fallito: %s", exc)
            result["status"] = "error"
            result["error"]  = str(exc)

        return result

    async def preview(self, focus_niche: str | None = None) -> dict:
        """
        Genera title + about senza applicare. Utile per /shopsetup senza confirm.

        Ritorna {title, about, niches, last_applied_title, changed}.
        """
        top_niches   = await self._get_top_niches(focus_niche)
        title        = await self.generate_shop_title(top_niches)
        about        = await self.generate_shop_about(top_niches)
        changed      = await self._has_niches_changed(top_niches)
        last_title   = await self._get_config(_CONFIG_KEY_TITLE) or "—"

        return {
            "title":             title,
            "about":             about,
            "niches":            top_niches,
            "changed":           changed,
            "last_applied_title": last_title,
        }

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    async def _get_top_niches(self, focus_niche: str | None = None) -> list[str]:
        """Legge top niches da LearningLoop (o usa fallback statico)."""
        niches: list[str] = []

        if self._learning_loop is not None:
            try:
                rows = await self._learning_loop.get_top_niches(limit=5)
                niches = [r["niche"] if isinstance(r, dict) else r for r in rows]
            except Exception as exc:
                logger.warning(
                    "_get_top_niches via LearningLoop fallito: %s", exc
                )

        if not niches:
            niches = list(_FALLBACK_NICHES)

        if focus_niche:
            niches = [focus_niche] + [n for n in niches if n != focus_niche]

        return niches[:5]

    async def _has_niches_changed(self, current_niches: list[str]) -> bool:
        """
        Confronta le niches correnti con l'ultima lista applicata (in config DB).
        Ritorna True se diverse o se non è mai stato applicato.
        """
        raw = await self._get_config(_CONFIG_KEY_NICHES)
        if not raw:
            return True  # mai applicato
        try:
            last = json.loads(raw)
            return list(last) != list(current_niches)
        except (json.JSONDecodeError, TypeError):
            return True

    async def _save_applied_niches(
        self, niches: list[str], title: str
    ) -> None:
        """Persiste le niches applicate e il titolo in config DB."""
        db = await self._memory.get_db()
        try:
            await db.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, unixepoch())
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = unixepoch()
                """,
                (_CONFIG_KEY_NICHES, json.dumps(niches)),
            )
            await db.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, unixepoch())
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = unixepoch()
                """,
                (_CONFIG_KEY_TITLE, title),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("_save_applied_niches fallito: %s", exc)

    async def _get_config(self, key: str) -> str | None:
        """Legge una chiave dalla tabella config."""
        try:
            db     = await self._memory.get_db()
            cursor = await db.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row["value"] if row else None
        except Exception:
            return None

    @staticmethod
    def _static_about(niches: list[str]) -> str:
        """Fallback statico per generate_shop_about quando LLM non è disponibile."""
        kw = ", ".join(niches[:4])
        return (
            f"Welcome to our digital printables shop! "
            f"We create high-quality, instant-download designs for {kw} and more. "
            f"All files are ready to print at home or at your local print shop. "
            f"Perfect for home decor, gifts, planners, and special occasions. "
            f"Every purchase includes instant access — no waiting, no shipping. "
            f"Browse the shop and find your perfect printable!"
        )
