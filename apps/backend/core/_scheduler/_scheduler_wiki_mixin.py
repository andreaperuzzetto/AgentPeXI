"""Scheduler — wiki mixin: wiki health check (compact, lint, update_index)."""

from __future__ import annotations

import logging

logger = logging.getLogger("agentpexi.scheduler")


class _WikiMixin:
    """Wiki health-check job: compact, lint, update_index, orphan cleanup."""

    async def _run_wiki_health_check(self) -> None:
        """Domenicale 04:00 — compact + lint + update_index su entrambi i domini.

        Flusso:
        1. Guard: pepe.wiki deve essere inizializzato (lifespan Step 5.2.5)
        2. Per ciascun dominio ["etsy", "personal"]:
           a. compact_wiki   — distilla file oltre soglia, ritorna {domain, files_compacted}
           b. lint           — wikilinks rotti + raw pending, ritorna report testuale
           c. update_index   — rigenera frontmatter summary: per ogni file wiki
        3. get_stats        — conta file/raw per il report aggregato
        4. Invia report Telegram
        """
        if not self.pepe:
            return
        wiki = getattr(self.pepe, "wiki", None)
        if wiki is None:
            logger.info("wiki_health_check: wiki non inizializzato, skip")
            return

        llm_etsy     = self.pepe.client        # Anthropic Sonnet
        llm_personal = self.pepe._local_client  # Ollama

        domains = [
            ("etsy",     llm_etsy),
            ("personal", llm_personal),
        ]

        compact_totals: dict[str, int]  = {}
        orphan_stats:   dict[str, dict] = {}
        lint_reports:   dict[str, str]  = {}

        for domain, llm in domains:
            # 0. Orphan raw cleanup (Block 5) — prima del lint per avere report aggiornato
            try:
                orphan_stats[domain] = await wiki.cleanup_orphan_raw(domain, llm)
                logger.info(
                    "wiki_health_check orphan_cleanup %s: compiled=%d deleted=%d skipped=%d errors=%d",
                    domain,
                    orphan_stats[domain]["compiled"],
                    orphan_stats[domain]["deleted"],
                    orphan_stats[domain]["skipped"],
                    len(orphan_stats[domain]["errors"]),
                )
            except Exception as exc:
                orphan_stats[domain] = {"compiled": 0, "deleted": 0, "skipped": 0, "errors": [str(exc)]}
                logger.error("wiki_health_check orphan_cleanup %s: %s", domain, exc)

            # 1. compact
            try:
                compact_result = await wiki.compact_wiki(domain, llm)
                compact_totals[domain] = compact_result.get("files_compacted", 0)
                logger.info("wiki compact %s: %d file", domain, compact_totals[domain])
            except Exception as exc:
                compact_totals[domain] = -1
                logger.error("wiki_health_check compact %s: %s", domain, exc)

            # 2. lint
            try:
                lint_reports[domain] = await wiki.lint(domain, llm)
            except Exception as exc:
                lint_reports[domain] = f"[errore lint: {exc}]"
                logger.error("wiki_health_check lint %s: %s", domain, exc)

            # 3. update_index
            try:
                await wiki.update_index(domain, llm)
                logger.info("wiki update_index %s: completato", domain)
            except Exception as exc:
                logger.error("wiki_health_check update_index %s: %s", domain, exc)

        # stats aggregate
        try:
            stats = await wiki.get_stats()
        except Exception:
            stats = {}

        # Telegram report
        lines = ["📚 *Wiki health check* completato\n"]
        for domain in ("etsy", "personal"):
            compacted = compact_totals.get(domain, 0)
            symbol = "✅" if compacted >= 0 else "❌"
            lines.append(f"{symbol} *{domain.capitalize()}* — {compacted} file compattati")

            # Orphan cleanup summary
            ost = orphan_stats.get(domain, {})
            compiled_n = ost.get("compiled", 0)
            deleted_n  = ost.get("deleted",  0)
            errors_n   = len(ost.get("errors", []))
            if compiled_n or deleted_n or errors_n:
                orphan_line = f"  🧹 Orfani: {compiled_n} compilati, {deleted_n} eliminati"
                if errors_n:
                    orphan_line += f", {errors_n} errori"
                lines.append(orphan_line)

            lint = lint_reports.get(domain, "")
            if lint and lint != "OK":
                # Tronca lint report a 300 char per non appesantire il messaggio
                lines.append(f"  ⚠️ Lint: {lint[:300]}")

        etsy_niches    = stats.get("etsy_niches", "?")
        total_raw      = stats.get("total_raw", "?")
        pending_raw    = stats.get("pending_raw", "?")
        lines.append(f"\n📊 Nicchie: {etsy_niches} | Raw totale: {total_raw} | Pending: {pending_raw}")

        report = "\n".join(lines)
        await self._notify_telegram(report)
        logger.info("wiki_health_check completato — report inviato")
