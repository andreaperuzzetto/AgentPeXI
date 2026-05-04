"""Confidence gate, wiki compilation, error synthesis and learning loop mixin for Pepe."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from apps.backend.core.config import settings
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus

logger = logging.getLogger("agentpexi.pepe")


class ConfidenceMixin:

    # ------------------------------------------------------------------
    # Confidence gate
    # ------------------------------------------------------------------

    async def _apply_confidence_gate(
        self,
        user_message: str,
        agent_name: str,
        result: AgentResult,
        session_id: str,
        source: str,
    ) -> str:
        """Applica confidence gate sul risultato di un agente.

        >= 0.85: procedi autonomamente + advance pipeline
        0.60-0.84: procedi con disclaimer e proposta
        < 0.60: blocca, spiega cosa manca con opzioni
        None: agente non supporta confidence → procedi normalmente
        """
        output = result.output_data or {}
        confidence = output.get("confidence") if isinstance(output, dict) else None
        missing_data = output.get("missing_data", []) if isinstance(output, dict) else []

        # Task FAILED
        if result.status == TaskStatus.FAILED:
            from datetime import timezone as _tz
            error_msg = (
                output.get("error", "Errore sconosciuto")
                if isinstance(output, dict)
                else str(output)
            )
            if source == "orb_voice":
                # Voce: frase corta umana. Il dettaglio tecnico arriva via WebSocket
                # come campo "detail" nel messaggio "error" → green card sul frontend.
                reply = result.reply_voice or "Non sono riuscito, puoi ripetere?"
                # Broadcast green card con dettaglio tecnico
                if self._ws_broadcast:
                    try:
                        from datetime import datetime as _dt
                        await self._ws_broadcast({
                            "type": "error",
                            "message": reply,
                            "detail": error_msg,
                            "agent": agent_name,
                            "ts": _dt.now(_tz.utc).isoformat(),
                        })
                    except Exception:
                        pass
            else:
                reply = await self._synthesize_error(agent_name, error_msg, {}, missing_data)
            await self.memory.save_message(session_id, "assistant", reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                next_action="error_recovery",
                trigger="confidence_gate",
            )
            return reply

        # Source of truth: AgentCard.confidence_threshold
        # Fallback: PersonalLayer / DomainContext per retrocompatibilità transizione
        card = self._agent_cards.get(agent_name)
        if card:
            threshold = card.confidence_threshold
            # confidence_disclaimer non è in AgentCard — usa il layer di appartenenza
            if card.layer == "personal":
                disclaimer = self._personal_layer.confidence_disclaimer
            else:
                disclaimer = self._business_domain.confidence_disclaimer if self._business_domain else 0.60
        else:
            # Fallback legacy: agente senza card ancora
            _personal_names = {n for n, c in self._agent_cards.items() if c.layer == "personal"}
            if agent_name in _personal_names or not self._has_business_domain():
                threshold = self._personal_layer.confidence_threshold
                disclaimer = self._personal_layer.confidence_disclaimer
            else:
                d = self._business_domain
                threshold = d.confidence_threshold if d else 0.85
                disclaimer = d.confidence_disclaimer if d else 0.60

        # confidence None o >= threshold: procedi autonomamente
        if confidence is None or confidence >= threshold:
            # Canale vocale con reply_voice dedicata → non serve _synthesize_reply
            if source == "orb_voice" and result.reply_voice:
                final_reply = result.reply_voice
            else:
                final_reply = await self._synthesize_reply(user_message, agent_name, result, autonomous=True)

            # Wiki hook — Branch 2 (prima di _advance_pipeline, vedi Step 5.2.2a)
            if hasattr(self, "wiki") and self.wiki is not None:
                self._fire(
                    self._compile_wiki_entry(agent_name, result, session_id),
                    name="wiki_compile",
                )

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                trigger="confidence_gate",
            )

            # Triggera passo successivo pipeline DOPO il broadcast —
            # garantisce che il report arrivi prima di "🎨 Design Agent avviato"
            await self._advance_pipeline_if_autonomous(agent_name, result, session_id)

            return final_reply

        # confidence >= disclaimer threshold: procedi con disclaimer e proposta
        if confidence >= disclaimer:
            # Canale vocale: il disclaimer confidence è inutile ad alta voce → usa reply_voice
            if source == "orb_voice" and result.reply_voice:
                final_reply = result.reply_voice
            else:
                final_reply = await self._synthesize_reply(user_message, agent_name, result)
                disclaimer_text = (
                    f"\n\n⚠️ **Nota**: analisi basata su dati parziali "
                    f"(confidence {confidence:.0%}). "
                    f"Dati mancanti: {', '.join(missing_data[:3])}.\n"
                    f"Vuoi che proceda comunque o preferisci attendere dati migliori?"
                )
                final_reply += disclaimer_text

            # Wiki hook — Branch 3
            if hasattr(self, "wiki") and self.wiki is not None:
                self._fire(
                    self._compile_wiki_entry(agent_name, result, session_id),
                    name="wiki_compile",
                )

            await self.memory.save_message(session_id, "assistant", final_reply, source)
            await self._broadcast_context_update(
                confidence=confidence,
                next_action="await_user_confirmation",
                trigger="confidence_gate",
            )
            return final_reply

        # confidence < 0.60: NON procedere, rilancia automaticamente
        missing_str = ", ".join(missing_data[:5]) if missing_data else "dati insufficienti"
        reply = (
            f"❌ Dati insufficienti per procedere con sicurezza "
            f"(confidence: {confidence:.0%}).\n\n"
            f"**Mancano**: {missing_str}\n\n"
            f"**Causa principale**: i dati di pricing e keyword "
            f"provengono da inferenza LLM invece che da fonti dirette.\n\n"
            f"**Cosa puoi fare**:\n"
            f"• Attendere l'attivazione delle API di dominio per dati reali\n"
            f"• Specificare una nicchia più narrow per migliorare la ricerca\n"
            f"• Procedere lo stesso accettando il rischio di dati parziali"
        )
        await self.memory.save_message(session_id, "assistant", reply, source)
        await self._broadcast_context_update(
            confidence=confidence,
            next_action="blocked_low_confidence",
            trigger="confidence_gate",
        )
        return reply

    # ------------------------------------------------------------------
    # Error synthesis
    # ------------------------------------------------------------------

    async def _compile_wiki_entry(
        self, agent_name: str, result: AgentResult, session_id: str  # noqa: ARG002
    ) -> None:
        """Alimenta la wiki in background dopo ogni agent completion (Branch 2 e 3).

        Chiamata sempre tramite asyncio.create_task — non bloccante.
        Guard hasattr(self, "wiki") già applicato nel chiamante.
        """
        # Early return per agenti che non producono dati wiki
        if agent_name in {"recall", "remind"}:
            return

        # Copia difensiva — result.output_data potrebbe essere None o oggetto condiviso
        output = dict(result.output_data or {})

        # LLM client per-agente — source of truth: AgentCard.layer (fallback per agenti senza card)
        card = self._agent_cards.get(agent_name)
        if card:
            llm = self._local_client if card.layer == "personal" else self.client
        else:
            # Fallback legacy
            llm = self._local_client if not self._has_business_domain() else self.client

        try:
            if agent_name == "research":
                niches = output.get("niches") or []
                # Supporta sia output singola nicchia che autonomous (winner.niche)
                winner_niche = (output.get("winner") or {}).get("niche", "")
                niche = output.get("niche") or winner_niche or (niches[0].get("name", "") if niches and isinstance(niches[0], dict) else niches[0] if niches else "")
                if niche:
                    await self.wiki.compile_niche(niche, "research", output, llm)
                await self.wiki.store_raw("etsy", "research", output)

            elif agent_name == "analytics":
                niche = output.get("niche", "")
                if niche:
                    await self.wiki.compile_niche(niche, "analytics", output, llm)

            elif agent_name == "publisher":
                # Publisher restituisce N risultati (uno per file) — iteriamo su publish_details
                for detail in output.get("publish_details", []):
                    niche = detail.get("niche", "")
                    if not niche:
                        continue

                    status = detail.get("status", "")
                    listing_id = detail.get("listing_id")

                    # Raw sempre — successo o fallimento
                    await self.wiki.store_raw("etsy", "publisher", detail)

                    # Wiki compile solo se listing creato (dati significativi)
                    if listing_id:
                        await self.wiki.compile_niche(niche, "publisher", detail, llm)

                    # ChromaDB — successo
                    if listing_id:
                        text = (
                            f"Publisher: listing creato per niche '{niche}'. "
                            f"Template: {detail.get('file_type', '')} | "
                            f"Schema: {detail.get('color_scheme', '') or 'N/A'} | "
                            f"Prezzo: {detail.get('price_source', '')} | "
                            f"Variante A/B: {detail.get('ab_variant', '')} | "
                            f"SEO validata: {detail.get('seo_validated', False)} | "
                            f"Immagini: {detail.get('images_uploaded', 0)}/3."
                        )
                        await self.memory.store_insight(text, {
                            "type": "publish_success",
                            "niche": niche,
                            "template": detail.get("file_type", ""),
                            "color_scheme": detail.get("color_scheme", ""),
                            "ab_variant": detail.get("ab_variant", ""),
                            "seo_validated": str(detail.get("seo_validated", False)),
                            "images_uploaded": str(detail.get("images_uploaded", 0)),
                            "price_source": detail.get("price_source", ""),
                            "date": datetime.now(timezone.utc).date().isoformat(),
                        })

                    # ChromaDB — fallimento (skipped o error)
                    elif status in ("skipped_file_too_large", "skipped_no_thumbnails", "error"):
                        error_msg = detail.get("error", "")
                        text = (
                            f"Publisher: listing NON creato per niche '{niche}'. "
                            f"Motivo: {status}. "
                            f"Template: {detail.get('file_type', '')} | "
                            f"Schema: {detail.get('color_scheme', '') or 'N/A'}. "
                            f"Errore: {error_msg[:200] if error_msg else 'nessuno'}."
                        )
                        await self.memory.store_insight(text, {
                            "type": "publish_failure",
                            "niche": niche,
                            "failure_type": status,
                            "template": detail.get("file_type", ""),
                            "color_scheme": detail.get("color_scheme", ""),
                            "date": datetime.now(timezone.utc).date().isoformat(),
                        })

            elif agent_name == "design":
                niche = output.get("niche", "")
                preset = output.get("preset", "")
                template = output.get("template", "")
                variants = output.get("variants", [])

                # Raw sempre — una entry per variante generata
                for variant in variants:
                    await self.wiki.store_raw("etsy", "design", {
                        "niche": niche,
                        "preset": preset,
                        "template": template,
                        "color_scheme": variant.get("color_scheme", ""),
                        "colors": variant.get("colors", {}),
                        "validation": variant.get("validation", {}),
                        "pages": variant.get("pages", 0),
                        "include_dates": output.get("include_dates", False),
                    })

                # ChromaDB — design_outcome per variante (letto da _lookup_failure_patterns)
                for variant in variants:
                    if not niche or not preset or not template:
                        continue
                    validation = variant.get("validation", {})
                    color_scheme = variant.get("color_scheme", "")
                    text = (
                        f"Design: variante generata per niche '{niche}'. "
                        f"Preset: {preset} | Template: {template} | "
                        f"Schema colore: {color_scheme or 'N/A'} | "
                        f"PDF valido: {validation.get('valid', False)} | "
                        f"Pagine: {variant.get('pages', 0)} | "
                        f"Dimensione: {validation.get('file_size_kb', 0):.0f}KB."
                    )
                    await self.memory.store_insight(text, {
                        "type": "design_outcome",
                        "niche": niche,
                        "preset": preset,
                        "template": template,
                        "color_scheme": color_scheme,
                        "pdf_valid": str(validation.get("valid", False)),
                        "pages": str(variant.get("pages", 0)),
                        "file_size_kb": str(round(validation.get("file_size_kb", 0))),
                        "date": datetime.now(timezone.utc).date().isoformat(),
                    })

            elif agent_name == "finance":
                content = json.dumps(output, ensure_ascii=False)
                await self.wiki.compile_wiki_file("etsy", "patterns/pricing", content, llm)

            elif agent_name == "research_personal":
                await self.wiki.store_raw("personal", "research", output)

            elif agent_name == "summarize":
                content = output.get("summary") or output.get("text") or str(output)
                await self.wiki.store_raw("personal", "summarize", output)
                if content:
                    await self.wiki.compile_wiki_file("personal", "preferences", content, llm)

        except Exception as exc:
            logger.warning("_compile_wiki_entry (%s): %s", agent_name, exc)

    @staticmethod
    def _voice_error_phrase(error_msg: str) -> str:
        """Mappa un messaggio di errore tecnico in una frase vocale breve e umana.

        Nessuna chiamata LLM — lookup sincrono per mantenere latenza minima
        sul canale vocale. Il dettaglio completo viene inviato via WebSocket
        come campo 'detail' per la green card sul frontend.
        """
        msg = error_msg.lower()
        if any(k in msg for k in ("quando", "when")):
            return "Non ho capito quando, puoi ripetere?"
        if any(k in msg for k in ("testo mancante", "missing", "manca", "campo")):
            return "Non ho capito bene, puoi ripetere?"
        if any(k in msg for k in ("timeout", "timed out", "ci ha messo")):
            return "Ci ho messo troppo, riprova."
        if any(k in msg for k in ("connect", "network", "unreachable", "connessione")):
            return "C'è un problema di connessione, riprova tra un momento."
        if any(k in msg for k in ("duplicat", "già un reminder", "già present")):
            return "Hai già qualcosa di simile, vuoi aggiungerlo lo stesso?"
        if any(k in msg for k in ("notion", "calendar", "sincronizzazione")):
            return "Fatto, anche se la sincronizzazione esterna non è riuscita."
        if any(k in msg for k in ("auth", "api key", "unauthorized", "credenziali")):
            return "C'è un problema con le credenziali, controlla la configurazione."
        return "Non sono riuscito, puoi ripetere?"

    async def _synthesize_error(
        self,
        agent_name: str,
        error_message: str,
        context_data: dict | None = None,
        missing_data: list[str] | None = None,
    ) -> str:
        """Sintetizza errore in linguaggio naturale per l'utente."""
        domain_label = self._business_domain.name if self._business_domain else "personal"
        error_system = (
            f"Sei Pepe, orchestratore di AgentPeXI per il dominio {domain_label}. "
            "Un agente ha fallito. Riferisci onestamente cosa è successo: "
            "descrivi l'errore reale (anche tecnico se necessario), spiega la causa probabile "
            "solo se deducibile dall'errore stesso — non speculare. "
            "Proponi solo azioni concretamente applicabili nel sistema "
            "(es. riprovare, riformulare la richiesta, verificare una configurazione specifica). "
            "NON inventare workaround generici. NON attribuire il problema a server esterni "
            "se non è nell'errore. NON dare consigli su volumi di dati o tempi di attesa "
            "a meno che non siano nell'errore stesso. Sii diretto. Max 100 parole."
        )
        context_str = json.dumps(context_data, ensure_ascii=False, default=str) if context_data else "{}"
        missing_str = ", ".join(missing_data) if missing_data else "nessuno"
        user_content = (
            f"Agente: {agent_name}\n"
            f"Errore: {error_message}\n"
            f"Contesto: {context_str}\n"
            f"Missing data: {missing_str}"
        )
        text = await self._llm_simple_call(
            error_system, user_content, max_tokens=512, use_haiku=True, agent_name=agent_name
        )
        return text or f"L'agente {agent_name} ha fallito: {error_message}"

    # ------------------------------------------------------------------
    # Learning loop (Intervento 9)
    # ------------------------------------------------------------------

    async def _evaluate_and_gate_pattern(
        self,
        signal: str,
        pattern_value: str,
        metric_type: str,
        current_metric: float,
    ) -> bool:
        """Applica acceptance gate prima di salvare un pattern appreso.

        Recupera il baseline della metrica dalle ultime LEARNING_EVAL_WINDOW occorrenze.
        Se il delta >= LEARNING_ACCEPTANCE_THRESHOLD: salva il pattern, ritorna True.
        Se il delta < threshold: non salva, logga il rifiuto, ritorna False.
        Se dati insufficienti (prima volta): salva comunque (cold start), ritorna True.
        """
        baseline = await self.memory.get_baseline_metric(
            metric_type, window=settings.LEARNING_EVAL_WINDOW
        )

        if baseline is None:
            # Cold start: nessun dato storico, accetta comunque
            logger.info(
                "Learning gate: cold start per signal=%s, pattern=%s — accettato",
                signal, pattern_value
            )
            return True

        delta = current_metric - baseline

        if delta >= settings.LEARNING_ACCEPTANCE_THRESHOLD:
            await self.memory.save_learning_evaluation(
                pattern_id=f"{signal}:{pattern_value}",
                signal_type=signal,
                metric_type=metric_type,
                baseline_value=baseline,
                post_value=current_metric,
                accepted=True,
            )
            logger.info(
                "Learning gate: ACCETTATO signal=%s delta=%.3f (baseline=%.3f post=%.3f)",
                signal, delta, baseline, current_metric
            )
            return True
        else:
            await self.memory.save_learning_evaluation(
                pattern_id=f"{signal}:{pattern_value}",
                signal_type=signal,
                metric_type=metric_type,
                baseline_value=baseline,
                post_value=current_metric,
                accepted=False,
            )
            logger.info(
                "Learning gate: RIFIUTATO signal=%s delta=%.3f < threshold=%.3f",
                signal, delta, settings.LEARNING_ACCEPTANCE_THRESHOLD
            )
            return False

    async def _store_design_winner(
        self, niche: str, template: str, color_scheme: str, views: int, sales: int
    ) -> None:
        """Scrive design_winner su ChromaDB quando un listing converte bene.

        Letto da DesignAgent._lookup_failure_patterns per guidare la scelta
        di preset e template nelle run successive sulla stessa niche.
        """
        try:
            text = (
                f"Design winner per niche '{niche}': "
                f"template '{template}', schema colore '{color_scheme or 'N/A'}'. "
                f"Performance: {sales} vendite, {views} views."
            )
            await self.memory.store_insight(text, {
                "type": "design_winner",
                "niche": niche,
                "template": template,
                "color_scheme": color_scheme,
                "views": str(views),
                "sales": str(sales),
                "date": datetime.now(timezone.utc).date().isoformat(),
            })
            logger.info("Design winner salvato: niche=%s template=%s sales=%d", niche, template, sales)
        except Exception as exc:
            logger.warning("Errore salvataggio design_winner niche=%s: %s", niche, exc)

    async def _handle_learning_loop(self, analytics_output: dict) -> None:
        """Processa i risultati dell'Analytics Agent e triggera azioni autonome.

        - Bestseller → aggiunge varianti alla production queue + notifica
        - 0 views a 7gg → triggera Research per fix tag
        - 0 conversioni a 45gg → triggera Research per revisione prezzo
        """
        listings = analytics_output.get("listings_analyzed", [])

        for listing in listings:
            listing_id = listing.get("listing_id")
            niche = listing.get("niche", "")
            views = listing.get("views", 0)
            sales = listing.get("sales", 0)
            days_live = listing.get("days_live", 0)
            failure_type = listing.get("failure_type")
            template = listing.get("template", "")
            color_scheme = listing.get("color_scheme", "")

            # --- Design winner — indipendente dal segnale primario ---
            # Criteri: almeno 1 vendita, almeno 10 views, metadati design presenti.
            # Viene scritto su ChromaDB e letto da DesignAgent al prossimo run sulla niche.
            if sales >= 1 and views >= 10 and template and niche:
                await self._store_design_winner(niche, template, color_scheme, views, sales)

            # Determina segnale dal dato
            signal = None
            if sales >= 10:
                signal = "bestseller"
            elif failure_type == "no_views" and days_live >= 7:
                signal = "no_views"
            elif failure_type == "no_conversion" and days_live >= 45 and views > 0:
                signal = "no_conversion"

            if signal is None:
                continue

            action = self.domain.learning_triggers.get(signal)

            if action == "propose_variant":
                # Gate: accetta pattern bestseller solo se le vendite segnano un delta positivo
                accepted = await self._evaluate_and_gate_pattern(
                    signal="bestseller",
                    pattern_value=niche,
                    metric_type="sales_delta",
                    current_metric=float(sales),
                )
                if not accepted:
                    continue
                proposal_msg = (
                    f"🌟 **Bestseller rilevato**: {listing.get('title', listing_id)}\n"
                    f"📊 {sales} vendite, {views} views\n\n"
                    f"Vuoi che creo varianti di questo prodotto? "
                    f"Rispondi 'sì' per aggiungerle in coda automaticamente."
                )
                await self.notify_telegram(proposal_msg, priority=True)

                # Salva come pending_action per handler sì/no
                await self.memory.save_pending_action(
                    action_type="bestseller_variant_proposal",
                    payload={
                        "listing_id": listing_id,
                        "niche": niche,
                        "product_type": listing.get("product_type", "printable_pdf"),
                        "original_sales": sales,
                    },
                )

            elif action == "fix_tags":
                # Gate: accetta fix_tags solo se il delta views giustifica l'intervento
                views_delta = listing.get("delta_views_vs_yesterday", 0)
                accepted = await self._evaluate_and_gate_pattern(
                    signal="no_views",
                    pattern_value=niche,
                    metric_type="views_delta",
                    current_metric=float(views_delta),
                )
                if not accepted:
                    continue
                fix_task = AgentTask(
                    agent_name="research",
                    input_data={
                        "niches": [niche],
                        "task_type": "fix_tags",
                        "target_listing_id": listing_id,
                        "problem": "0 views dopo 7 giorni — tag strategy da rivedere",
                        "current_tags": listing.get("tags", []),
                    },
                    source="learning_loop",
                )
                await self._queue.put(fix_task)
                await self.notify_telegram(
                    f"🔍 Avviata ricerca automatica per fix tag: {listing.get('title', listing_id)}\n"
                    f"0 views dopo {days_live} giorni."
                )

            elif action == "fix_pricing":
                # Gate: accetta fix_pricing solo se il delta conversioni giustifica l'intervento
                accepted = await self._evaluate_and_gate_pattern(
                    signal="no_conversion",
                    pattern_value=niche,
                    metric_type="task_success_rate",
                    current_metric=0.0,  # 0 conversioni = task_success_rate = 0
                )
                if not accepted:
                    continue
                fix_task = AgentTask(
                    agent_name="research",
                    input_data={
                        "niches": [niche],
                        "task_type": "fix_pricing",
                        "target_listing_id": listing_id,
                        "problem": f"0 vendite dopo {days_live} giorni con {views} views — prezzo da rivedere",
                        "current_price": listing.get("price_usd"),
                    },
                    source="learning_loop",
                )
                await self._queue.put(fix_task)
                await self.notify_telegram(
                    f"💰 Avviata analisi prezzo automatica: {listing.get('title', listing_id)}\n"
                    f"{views} views ma 0 vendite dopo {days_live} giorni."
                )

            else:
                logger.debug("Segnale '%s' non gestito nel dominio '%s'", signal, self.domain.name)
