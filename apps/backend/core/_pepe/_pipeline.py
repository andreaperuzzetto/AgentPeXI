"""Pipeline automation and pending-action handler mixin for Pepe."""
from __future__ import annotations

import asyncio
import logging

from apps.backend.core.config import settings
from apps.backend.core.models import AgentTask, AgentResult, TaskStatus

logger = logging.getLogger("agentpexi.pepe")


# ------------------------------------------------------------------
# Module-level helper (used only in _run_analytics_auto)
# ------------------------------------------------------------------

def _format_analytics_summary(output: dict) -> str:
    """Formatta il report analytics in formato compatto (identico al messaggio Telegram)."""
    from datetime import date as _date
    date_str = output.get("date", _date.today().isoformat())
    total_views = output.get("total_views", 0)
    total_fav   = output.get("total_favorites", 0)
    total_sales = output.get("total_sales", 0)
    total_rev   = output.get("total_revenue_eur", 0.0)
    delta       = output.get("delta_views_vs_yesterday", 0)
    active      = output.get("total_listings_active", 0)
    drafts      = output.get("drafts", 0)
    failures    = output.get("failures", {})

    delta_sign = f"+{delta}" if delta >= 0 else str(delta)

    bestsellers = output.get("bestsellers", [])
    if bestsellers:
        bs = bestsellers[0]
        bs_line = f"{bs.get('title', '')[:40]} ({bs.get('sales', 0)} vendite)"
    else:
        bs_line = "nessuno"

    ab = output.get("ab_performance", {})
    ab_winner = ab.get("winner")
    if ab_winner and ab_winner != "inconclusive":
        ab_line = f"A/B: variante {ab_winner} vince ({ab.get('winner_confidence', '')} confidence)\n"
    elif ab_winner == "inconclusive":
        ab_line = "A/B: dati insufficienti\n"
    else:
        ab_line = ""

    tot_failures = sum(v for v in failures.values() if isinstance(v, int))
    failure_detail = ""
    if tot_failures:
        parts = []
        if failures.get("no_views"):
            parts.append(f"{failures['no_views']} senza views >7gg")
        if failures.get("no_conversion"):
            parts.append(f"{failures['no_conversion']} senza conversioni >45gg")
        if parts:
            failure_detail = f"Da ottimizzare: {', '.join(parts)}\n"

    return (
        f"Etsy — {date_str}\n"
        f"{'─' * 14}\n"
        f"Views: {total_views} ({delta_sign} vs ieri)  |  Favorites: {total_fav}\n"
        f"Vendite: {total_sales}  |  Revenue: €{total_rev:.2f}\n"
        f"Listing attivi: {active}  |  Bozze: {drafts}\n"
        f"{ab_line}"
        f"Bestseller: {bs_line}\n"
        f"{failure_detail}"
    ).rstrip()


# ------------------------------------------------------------------
# Mixin
# ------------------------------------------------------------------

class PipelineMixin:

    # ------------------------------------------------------------------
    # Pipeline duplicate check (Intervento 5)
    # ------------------------------------------------------------------

    async def _check_pipeline_duplicate(self, delegation: dict) -> str | None:
        """Verifica se la nicchia è già in produzione o in coda.

        Ritorna messaggio di warning oppure None se si può procedere.
        """
        agent_input = delegation.get("input", {})
        niche = (
            agent_input.get("niche")
            or (agent_input.get("niches", [None])[0])
            or agent_input.get("query", "")
        )

        if not niche:
            return None

        try:
            product_type = agent_input.get("product_type", "printable_pdf")
            is_duplicate = await self.memory.is_duplicate_product(
                niche=niche,
                product_type=product_type,
            )
            if is_duplicate:
                return (
                    f"⚠️ La nicchia **{niche}** è già presente in production queue o "
                    f"tra i listing pubblicati.\n\n"
                    f"Vuoi:\n"
                    f"• Procedere comunque con una variante diversa\n"
                    f"• Vedere le performance del listing esistente\n"
                    f"• Scegliere una nicchia diversa"
                )
        except Exception:
            logger.exception("Duplicate check failed — proceeding without duplicate detection")
    # ------------------------------------------------------------------

    async def _get_pipeline_summary(self) -> str:
        """Ritorna un riassunto dello stato della production queue per il system prompt."""
        try:
            if not hasattr(self.memory, "get_production_queue_stats"):
                return ""
            stats = await self.memory.get_production_queue_stats()
            if not stats:
                return ""
            pending = stats.get("planned", 0)
            in_progress = stats.get("in_progress", 0)
            completed_today = stats.get("completed_today", 0)
            return (
                f"In coda: {pending} prodotti pianificati, "
                f"{in_progress} in lavorazione, "
                f"{completed_today} completati oggi"
            )
        except Exception:
            return ""

    async def _get_recent_analytics_summary(self) -> str:
        """Ritorna un riassunto delle performance recenti per il system prompt."""
        try:
            if not hasattr(self.memory, "get_analytics_summary"):
                return ""
            summary = await self.memory.get_analytics_summary(days=7)
            if not summary:
                return ""
            return (
                f"Ultimi 7 giorni: "
                f"{summary.get('total_views', 0)} views, "
                f"{summary.get('total_sales', 0)} vendite, "
                f"€{summary.get('revenue', 0):.2f} revenue"
            )
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Handler pending_actions (sì/no per proposte varianti)
    # ------------------------------------------------------------------

    async def _check_pending_action(self, message: str, source: str) -> str | None:
        """Controlla se esiste un pending_action e il messaggio è sì/no.

        Gestisce:
        - urgency_proposal (sì/no) → feedback learning loop
        - production_queue_proposal (sì/no) → aggiunge alla queue

        Ritorna la risposta da inviare, oppure None se non applicabile.
        """
        normalized = message.strip().lower()
        yes_words = {"sì", "si", "yes", "s"}
        no_words = {"no", "n", "nope"}

        # --- urgency_proposal ---
        urgency_pending = await self.memory.get_pending_action("urgency_proposal")
        if urgency_pending and normalized in yes_words | no_words:
            payload = urgency_pending.get("payload", {})
            text = payload.get("text", "")
            signal = "positive" if normalized in yes_words else "negative"
            weight_delta = 0.1 if signal == "positive" else -0.1
            # Estrai prime 2 parole chiave dal testo come pattern keyword
            words = [w.lower() for w in text.split() if len(w) > 4][:2]
            for kw in words:
                try:
                    await self.memory.upsert_learning(
                        agent="urgency",
                        pattern_type="keyword",
                        pattern_value=kw,
                        signal_type=signal,
                        weight_delta=weight_delta,
                    )
                except Exception:
                    logger.exception("Unexpected error")
            await self.memory.delete_pending_action("urgency_proposal")
            if normalized in yes_words:
                # Segnala a Pepe di gestire — per ora risposta testuale
                return "✅ Gestisco. Ti aggiorno a breve."
            else:
                return "👍 Ok, non lo gestisco. Ho preso nota per il futuro."

        # --- clarification (task_id correlato) ---
        clarification_pending = await self.memory.get_pending_action("clarification")
        if clarification_pending:
            from apps.backend.core.models import AgentTask as _AgentTask
            payload = clarification_pending.get("payload", {})
            task_id = payload.get("task_id")
            agent_name = payload.get("agent_name")
            partial_input = payload.get("partial_input", {})

            # Merge risposta utente con input parziale
            enriched_input = {**partial_input, "user_clarification": message}

            # Ricreare il task e rimetterlo in coda (stesso task_id per tracciabilità)
            new_task = _AgentTask(
                task_id=task_id,
                agent_name=agent_name,
                input_data=enriched_input,
                source=source,
            )
            await self.memory.resolve_pending_input(task_id)
            return await self._enqueue_and_wait(new_task)

        pending = await self.memory.get_pending_action("production_queue_proposal")

        if not pending:
            return None

        if normalized in yes_words:
            from uuid import uuid4

            payload = pending["payload"]
            niche_variant = f"{payload.get('niche', '')} variante {payload.get('color_scheme', '')} alternativa"
            brief = {
                "niche": niche_variant,
                "product_type": payload.get("product_type", "printable_pdf"),
                "template": payload.get("template", "weekly_planner"),
                "num_variants": 3,
                "color_schemes": [],
                "keywords": [],
            }
            await self.memory.add_to_production_queue(
                task_id=str(uuid4()),
                product_type=payload.get("product_type", "printable_pdf"),
                niche=niche_variant,
                brief=brief,
            )
            await self.memory.delete_pending_action("production_queue_proposal")
            return "✅ Aggiunto in coda! Sarà prodotto nel prossimo ciclo pipeline (domani alle 09:00)."

        if normalized in no_words:
            await self.memory.delete_pending_action("production_queue_proposal")
            return "👍 Ok, proposta ignorata."

        # Messaggio non è sì/no → ignora pending_action, processa normalmente
        return None

    # ------------------------------------------------------------------
    # Pipeline automation (Intervento 8)
    # ------------------------------------------------------------------

    async def _advance_pipeline_if_autonomous(
        self,
        agent_name: str,
        result: AgentResult,
        session_id: str,
    ) -> None:
        """Dopo un risultato con confidence >= 0.85, avanza la pipeline autonomamente.

        Research completato → nessuna azione (Pepe propone Design nella risposta).
        Design completato → auto-trigger Publisher se file_paths disponibili.
        Analytics completato → triggera learning loop.
        """
        output = result.output_data or {}

        if agent_name == "analytics":
            # Learning loop: processa risultati analytics
            await self._handle_learning_loop(output)
            return

        if agent_name == "publisher":
            # Publisher completato → auto-trigger Analytics per sincronizzare stats
            listings_created = output.get("listings_created", 0)
            if listings_created > 0:
                analytics_task = AgentTask(
                    agent_name="analytics",
                    input_data={
                        "trigger": "post_publish",
                        "listings_created": listings_created,
                        "_run_cost_usd": output.get("_run_cost_usd", 0.0),  # cumulativo research+design+publisher
                    },
                    source="pipeline_auto",
                )
                logger.info(
                    "Publisher completato (%d listing) → auto-trigger Analytics",
                    listings_created,
                )
                self._fire(
                    self._run_analytics_auto(analytics_task, session_id),
                    name="analytics_auto",
                )
            return

        if agent_name == "design":
            file_paths = output.get("file_paths", [])
            # Il Design Agent restituisce i file dentro "variants" (lista di dict),
            # non come "file_paths" flat. Estrai i path da ogni variante.
            variants = output.get("variants", [])
            if not file_paths:
                for v in variants:
                    path = v.get("pdf_path") or v.get("file_path") or v.get("svg_path") or v.get("path")
                    if path:
                        file_paths.append(path)
            if not file_paths:
                logger.info("Design completato senza file_paths né variants, publisher non triggerato")
                return

            # Estrai thumbnail path dai variants (generati da Playwright)
            # I publisher li usa come immagini Etsy — passali esplicitamente.
            thumbnail_paths: list[str] = []
            for v in variants:
                thumbs = v.get("thumbnails", {})
                for key in ("mockup", "cover", "interior"):
                    p = thumbs.get(key)
                    if p:
                        thumbnail_paths.append(str(p))

            # Recupera contesto necessario per Publisher dall'input del task originale
            publisher_input = {
                "file_paths": file_paths,
                "thumbnail_paths": thumbnail_paths,  # path espliciti da Design Agent
                "product_type": output.get("product_type", "printable_pdf"),
                "template": output.get("template", ""),
                "niche": output.get("niche", ""),
                "color_schemes": output.get("color_schemes", []),
                "keywords": output.get("keywords", []),
                "size": output.get("size", "A4"),
                "production_queue_task_id": output.get("production_queue_task_id"),
                "pricing": output.get("pricing", {}),  # da research_context, per prezzo research-driven
                "_run_cost_usd": output.get("_run_cost_usd", 0.0),  # costo cumulativo research+design
            }

            publish_task = AgentTask(
                agent_name="publisher",
                input_data=publisher_input,
                source="pipeline_auto",
            )
            logger.info(
                "Design completato (%d file) → auto-trigger Publisher",
                len(file_paths),
            )
            # Fire-and-forget: non blocca la risposta a Andrea
            self._fire(self._run_publisher_auto(publish_task, session_id), name="publisher_auto")
            return

        if agent_name == "research" and result.status == TaskStatus.COMPLETED:
            # Research → Design: auto-trigger se ci sono dati di ricerca
            research_output = output
            niches = research_output.get("niches", [])
            if not niches:
                # Prova a usare l'output come contesto diretto
                niche = research_output.get("niche", research_output.get("query", ""))
                if niche:
                    niches = [{"niche": niche, "product_type": research_output.get("product_type", "printable_pdf")}]

            if niches:
                # Prendi la prima nicchia viable per il design
                # Il research schema usa "name" e "recommended_product_type" (non "niche"/"product_type")
                first = niches[0] if isinstance(niches[0], dict) else {"name": niches[0]}
                niche_name = first.get("name") or first.get("niche", "")
                _VALID_PRODUCT_TYPES = {"printable_pdf", "digital_art_png", "svg_bundle"}
                product_type = (
                    first.get("recommended_product_type")
                    or first.get("product_type", "printable_pdf")
                )
                if product_type not in _VALID_PRODUCT_TYPES:
                    product_type = "printable_pdf"
                design_input = {
                    "niche": niche_name,
                    "product_type": product_type,
                    "research_context": research_output,
                    "keywords": first.get("keywords", []),
                    "color_schemes": first.get("color_schemes", []),
                    "_run_cost_usd": result.cost_usd,  # costo research, accumulato lungo la pipeline
                }
                design_task = AgentTask(
                    agent_name="design",
                    input_data=design_input,
                    source="pipeline_auto",
                )
                logger.info(
                    "Research completato → auto-trigger Design per nicchia '%s'",
                    niche_name or "?",
                )
                self._fire(
                    self._run_design_auto(design_task, session_id),
                    name="design_auto",
                )
            return

    async def _run_design_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il design in background dopo research, notifica via WS e Telegram."""
        try:
            niche = task.input_data.get('niche', '?')
            msg = f"Design avviato — {niche}"
            # Piccolo delay: garantisce che il reply del research (già in volo su Telegram)
            # arrivi prima del "Design avviato" — evita inversione di ordine dei messaggi
            await asyncio.sleep(2.0)
            await self.notify_telegram(msg)
            result = await self._enqueue_and_wait(task)
            if result.status == TaskStatus.COMPLETED:
                output = result.output_data or {}

                # Inietta nel result il contesto research che il Design Agent non propaga
                # (pricing, keywords, color_schemes) — il Publisher ne ha bisogno
                research_ctx = task.input_data.get("research_context", {})
                if research_ctx:
                    first_niche = next(
                        iter(research_ctx.get("niches", [{}])), {}
                    ) if research_ctx.get("niches") else research_ctx
                    if not output.get("pricing") and first_niche.get("pricing"):
                        output["pricing"] = first_niche["pricing"]
                    if not output.get("keywords") and first_niche.get("keywords"):
                        output["keywords"] = first_niche.get("keywords", [])
                    # color_schemes: prendi dai variants se non già presenti
                    if not output.get("color_schemes"):
                        variants = output.get("variants", [])
                        cs = [v.get("color_scheme", "") for v in variants if v.get("color_scheme")]
                        if cs:
                            output["color_schemes"] = cs
                    # In alternativa usa quelli dall'input del task
                    if not output.get("color_schemes") and task.input_data.get("color_schemes"):
                        output["color_schemes"] = task.input_data["color_schemes"]
                    result.output_data = output

                n_files = len(output.get("variants", [])) or output.get("variants_generated", 0)
                design_cost = result.cost_usd
                cost_so_far = task.input_data.get("_run_cost_usd", 0.0) + design_cost
                output["_run_cost_usd"] = cost_so_far  # propaga al publisher via output_data
                result.output_data = output
                msg = f"Design completato — {n_files} varianti in pending. Costo step: ${design_cost:.4f}. Pubblicazione in avvio."
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self.notify_telegram(msg)
                # _advance_pipeline_if_autonomous gestirà Design → Publisher
                await self._advance_pipeline_if_autonomous("design", result, session_id)
            else:
                error = (result.output_data or {}).get("error", "Errore sconosciuto")
                msg = f"Design fallito — {error}"
                await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
                await self.notify_telegram(msg, priority=True)
        except Exception as exc:
            logger.error("Design auto fallito: %s", exc)
            msg = f"Design interrotto — {exc}"
            await self.notify_telegram(msg, priority=True)

    async def _run_publisher_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue il publisher in background dopo il design, notifica via WS e Telegram."""
        try:
            niche = task.input_data.get("niche", "?")
            start_msg = f"Pubblicazione avviata — {niche}"
            await self.notify_telegram(start_msg)
            result = await self._enqueue_and_wait(task)
            output = result.output_data or {}
            n = output.get("listings_created", 0)
            publisher_cost = result.cost_usd
            cost_so_far = task.input_data.get("_run_cost_usd", 0.0) + publisher_cost
            output["_run_cost_usd"] = cost_so_far  # propaga all'analytics
            result.output_data = output
            msg = (
                f"Pubblicazione completata — {n} draft su Etsy. Costo step: ${publisher_cost:.4f}. Analisi in avvio."
                if n > 0
                else f"Pubblicazione completata — nessun draft creato. Costo step: ${publisher_cost:.4f}. Verifica log publisher."
            )
            await self.memory.save_message(session_id, "assistant", msg, "pipeline_auto")
            # Publisher → Analytics: sincronizza stats dopo ogni pubblicazione
            await self._advance_pipeline_if_autonomous("publisher", result, session_id)
        except Exception as exc:
            logger.error("Publisher auto fallito: %s", exc)
            msg = f"Pubblicazione interrotta — {exc}"
            await self.notify_telegram(msg, priority=True)

    async def _run_analytics_auto(self, task: AgentTask, session_id: str) -> None:
        """Esegue analytics in background dopo il publisher, notifica via WS e Telegram."""
        try:
            msg = "Analytics post-pubblicazione avviato."
            await self.notify_telegram(msg)
            result = await self._enqueue_and_wait(task)
            output = result.output_data or {}

            # Costruisci il report formattato (stesso formato di Telegram)
            # per mostrarlo anche nella chat web — i due canali restano identici
            summary_msg = _format_analytics_summary(output)
            await self.memory.save_message(session_id, "assistant", summary_msg, "pipeline_auto")
            # Telegram riceve il report già da analytics.py._send_daily_summary;
            # mandiamo solo il breve "completato" per non duplicare il report
            # Conta totale: attivi + bozze (evita "0" quando tutti i listing sono draft)
            listings_analyzed = (
                (output.get("total_listings_active") or 0)
                + (output.get("drafts") or 0)
                or output.get("listings_analyzed_count")
                or len(output.get("listings_analyzed", []))
            )
            analytics_cost = result.cost_usd
            total_run_cost = task.input_data.get("_run_cost_usd", 0.0) + analytics_cost
            total_run_eur = total_run_cost * settings.USD_EUR_RATE
            done_msg = (
                f"Analytics completato — {listings_analyzed} listing analizzati.\n"
                f"Costo run: ${total_run_cost:.4f} (≈ €{total_run_eur:.4f})"
            )
            await self.memory.save_message(session_id, "assistant", done_msg, "pipeline_auto")
            await self.notify_telegram(done_msg)
            # Learning loop
            await self._advance_pipeline_if_autonomous("analytics", result, session_id)
        except Exception as exc:
            logger.error("Analytics auto fallito: %s", exc)
            msg = f"Analytics interrotto — {exc}"
            await self.notify_telegram(msg, priority=True)
