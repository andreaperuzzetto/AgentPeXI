"""Bot Telegram — interfaccia Telegram per AgentPeXI.

Integrazione asincrona con python-telegram-bot v20+.
NON usa run_polling() — si integra nel loop asyncio di FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from apps.backend.core.config import settings
from apps.backend.core.domains import DOMAIN_ETSY
from apps.backend.core.models import AgentTask

if TYPE_CHECKING:
    from apps.backend.core.pepe import Pepe
    from apps.backend.core.scheduler import Scheduler
    from apps.backend.screen.watcher import ScreenWatcher

logger = logging.getLogger("agentpexi.telegram")


def _md_escape(text: str) -> str:
    """Escapa i caratteri speciali Markdown v1 nei valori dinamici.
    Usare per nomi nicchia, file path, output LLM e qualsiasi valore
    che possa contenere _ * ` [ ] che romperebbero il parse Telegram.
    """
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


class TelegramBot:
    """Bot Telegram per AgentPeXI — comandi + chat + vocale."""

    def __init__(
        self,
        pepe: Pepe,
        scheduler: Scheduler | None = None,
        screen_watcher: ScreenWatcher | None = None,
    ) -> None:
        self.pepe = pepe
        self.scheduler = scheduler
        self.screen_watcher = screen_watcher
        self._app: Application | None = None

        # Filtro: rispondi solo all'utente autorizzato.
        # Fail-closed: se TELEGRAM_CHAT_ID non è configurato, il bot non parte.
        if not settings.TELEGRAM_CHAT_ID:
            raise RuntimeError(
                "TELEGRAM_CHAT_ID non configurato in .env — "
                "impostarlo per evitare che qualsiasi utente possa interagire col bot"
            )
        self._chat_filter = filters.Chat(chat_id=int(settings.TELEGRAM_CHAT_ID))

    # ------------------------------------------------------------------
    # Startup / shutdown (chiamati dal lifespan FastAPI)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Avvia il bot nel loop asyncio corrente (no run_polling)."""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN non configurato — bot Telegram disattivato")
            return

        self._app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .build()
        )

        # Registra handler
        self._register_handlers()

        # Registra notifier in Pepe
        self.pepe.set_telegram_notifier(self._send_notification)
        self.pepe.set_reminder_notifier(self._send_reminder_notification)

        # Avvio asincrono (nello stesso event loop di FastAPI)
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot Telegram avviato")

    async def stop(self) -> None:
        """Shutdown graceful."""
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        logger.info("Bot Telegram fermato")

    # ------------------------------------------------------------------
    # Registrazione handler
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        assert self._app is not None
        add = self._app.add_handler

        add(CommandHandler("status", self._cmd_status, filters=self._chat_filter))
        add(CommandHandler("report", self._cmd_report, filters=self._chat_filter))
        add(CommandHandler("pause", self._cmd_pause, filters=self._chat_filter))
        add(CommandHandler("resume", self._cmd_resume, filters=self._chat_filter))
        add(CommandHandler("ask", self._cmd_ask, filters=self._chat_filter))
        add(CommandHandler("listings", self._cmd_listings, filters=self._chat_filter))
        add(CommandHandler("retry", self._cmd_retry, filters=self._chat_filter))
        add(CommandHandler("resume_agent", self._cmd_resume_agent, filters=self._chat_filter))
        add(CommandHandler("new", self._cmd_new, filters=self._chat_filter))
        add(CommandHandler("mock", self._cmd_mock, filters=self._chat_filter))
        add(CommandHandler("analytics", self._cmd_analytics, filters=self._chat_filter))
        add(CommandHandler("pipeline", self._cmd_pipeline, filters=self._chat_filter))
        add(CommandHandler("finance", self._cmd_finance, filters=self._chat_filter))
        add(CommandHandler("niche", self._cmd_niche, filters=self._chat_filter))
        add(CommandHandler("design", self._cmd_design_etsy, filters=self._chat_filter))
        add(CommandHandler("personal", self._cmd_personal, filters=self._chat_filter))
        add(CommandHandler("etsy", self._cmd_etsy, filters=self._chat_filter))
        add(CommandHandler("list", self._cmd_list, filters=self._chat_filter))
        add(CommandHandler("screen", self._cmd_screen, filters=self._chat_filter))
        add(CommandHandler("wiki", self._cmd_wiki, filters=self._chat_filter))

        # Comandi Personal (Blocco 3)
        add(CommandHandler("remind", self._cmd_remind, filters=self._chat_filter))
        add(CommandHandler("reminders", self._cmd_remind_list, filters=self._chat_filter))
        add(CommandHandler("summarize", self._cmd_summarize, filters=self._chat_filter))
        add(CommandHandler("research", self._cmd_research, filters=self._chat_filter))
        add(CommandHandler("feedback", self._cmd_feedback, filters=self._chat_filter))
        add(CommandHandler("urgency", self._cmd_urgency, filters=self._chat_filter))

        # Messaggi vocali
        add(MessageHandler(self._chat_filter & filters.VOICE, self._handle_voice))

        # Messaggi testo generici (deve essere l'ultimo handler)
        add(MessageHandler(self._chat_filter & filters.TEXT & ~filters.COMMAND, self._handle_text))

    # ------------------------------------------------------------------
    # Comandi
    # ------------------------------------------------------------------

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status — stato del sistema."""
        statuses = self.pepe.get_agent_statuses()
        if not statuses:
            await update.message.reply_text("🟢 Sistema attivo. Nessun agente registrato.")
            return

        lines = ["🟢 *Sistema AgentPeXI*\n"]
        status_icons = {"idle": "⚪", "running": "🔵", "error": "🔴"}
        for name, status in statuses.items():
            icon = status_icons.get(status, "❓")
            lines.append(f"{icon} *{name}*: {status}")

        queue_size = self.pepe._queue.qsize()
        lines.append(f"\n📋 Task in coda: {queue_size}")

        domain = self.pepe.get_active_domain()
        domain_name = domain.name if domain else "personal"
        domain_icon = "🏪" if domain_name == "etsy_store" else "🧠"
        lines.append(f"\n{domain_icon} *Dominio attivo*: {domain_name}")

        mock_line = "\n🟡 *MOCK MODE ATTIVO*" if self.pepe.mock_mode else ""
        if mock_line:
            lines.append(mock_line)

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/report — chiede report a Pepe."""
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(
            "Dammi un report sullo stato attuale del sistema e delle attività recenti.",
            source="telegram",
            session_id=session_id,
        )
        await self._reply_chunked(update.message, reply)

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pause — ferma i worker di Pepe."""
        await self.pepe.stop()
        await update.message.reply_text("⏸️ Worker Pepe fermati.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/resume — riavvia i worker di Pepe."""
        await self.pepe.start()
        await update.message.reply_text("▶️ Worker Pepe riavviati.")

    async def _cmd_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/ask <domanda> — chiede qualcosa a Pepe."""
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text("Uso: /ask <la tua domanda>")
            return
        session_id = str(update.effective_chat.id)
        reply = await self.pepe.handle_user_message(text, source="telegram", session_id=session_id)
        await self._reply_chunked(update.message, reply)

    async def _cmd_listings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/listings — lista listing Etsy recenti."""
        rows = await self.pepe.memory.get_etsy_listings(limit=10)
        if not rows:
            await update.message.reply_text("Nessun listing trovato.")
            return

        lines = ["📦 *Listing recenti*\n"]
        for row in rows:
            lines.append(
                f"• {row['title'][:40]} — {row['status']} | 🛒 {row['sales']} | €{row['revenue_eur']:.2f}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_retry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/retry [task_id] — riprova ultimo task fallito o uno specifico."""
        task_id = context.args[0] if context.args else None
        try:
            result = await self.pepe.retry_task(task_id=task_id)
            await update.message.reply_text(
                f"✅ Retry completato: {result.agent_name} → {result.status.value}"
            )
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
        except RuntimeError as exc:
            await update.message.reply_text(f"⚠️ {exc}")

    async def _cmd_resume_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/resume_agent <name> — riattiva agente sospeso."""
        if not context.args:
            await update.message.reply_text("Uso: /resume_agent <nome_agente>")
            return
        name = context.args[0]
        if self.pepe.resume_agent(name):
            await update.message.reply_text(f"✅ Agente *{name}* riattivato.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Agente *{name}* non trovato o non sospeso.", parse_mode="Markdown")

    async def _cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/new — nuova sessione, azzera conversazione precedente."""
        session_id = str(update.effective_chat.id)
        await self.pepe.memory.clear_session(session_id)
        await update.message.reply_text(
            "✅ Nuova sessione avviata. La conversazione precedente è stata archiviata."
        )

    async def _cmd_analytics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/analytics — esegue subito il job analytics (senza aspettare le 08:00)."""
        await update.message.reply_text("⏳ Avvio analytics manuale...")
        try:
            from apps.backend.core.models import AgentTask as _AgentTask
            task = _AgentTask(
                agent_name="analytics",
                input_data={},
                source="telegram_manual",
            )
            result = await self.pepe.dispatch_task(task)
            out = result.output_data or {}
            listings_count = len(out.get("listings_analyzed", []))
            await update.message.reply_text(
                f"✅ Analytics completato\n"
                f"Listing analizzati: {listings_count}\n"
                f"Controlla la dashboard per il report completo."
            )
        except Exception as exc:
            logger.error("Analytics manuale fallito: %s", exc)
            await update.message.reply_text(f"❌ Analytics fallito: {exc}")

    async def _cmd_pipeline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pipeline [png] — avvia manualmente Research → Design → Publisher.
        Default: printable_pdf. Aggiungi "png" per Digital Art PNG.
        """
        if not self.scheduler:
            await update.message.reply_text("❌ Scheduler non disponibile.")
            return
        args = context.args or []
        product_type = "digital_art_png" if args and args[0].lower() == "png" else "printable_pdf"
        label = "🖼 Digital Art PNG" if product_type == "digital_art_png" else "📄 Printable PDF"
        await update.message.reply_text(f"⏳ Pipeline {label} avviata — Research in corso…")
        task = asyncio.create_task(
            self.scheduler._run_pipeline(product_type=product_type),
            name=f"pipeline_manual_{product_type}",
        )
        task.add_done_callback(
            lambda t: logger.error("Pipeline manuale fallita: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )

    async def _cmd_finance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/finance — avvia manualmente il Finance Agent."""
        if not self.scheduler:
            await update.message.reply_text("❌ Scheduler non disponibile.")
            return
        await update.message.reply_text("⏳ Finance report in avvio...")
        task = asyncio.create_task(self.scheduler._run_finance(), name="finance_manual")
        task.add_done_callback(
            lambda t: logger.error("Finance manuale fallito: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )

    async def _cmd_niche(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/niche <nicchia> [quick] — singola nicchia deep (default) o quick.
        /niche <n1> | <n2> | <n3> [quick] — confronto multi-nicchia (max 5).
        Separatore: | oppure , (entrambi accettati).
        Es: /niche weekly planner
            /niche weekly planner quick
            /niche weekly planner | habit tracker | budget sheet
            /niche habit tracker, budget planner, goal journal quick
        """
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Uso:\n"
                "  `/niche <nicchia> [quick]` — singola nicchia\n"
                "  `/niche <n1> | <n2> [quick]` — confronto multi-nicchia (separatore `|` o `,`)\n\n"
                "Esempi:\n"
                "  `/niche weekly planner`\n"
                "  `/niche weekly planner | habit tracker | budget sheet`\n"
                "  `/niche habit tracker, budget planner, goal journal`\n\n"
                "Deep di default. Aggiungi `quick` per scansione rapida.",
                parse_mode="Markdown",
            )
            return

        import uuid as _uuid

        # Rileva flag [quick] come ultimo argomento
        raw = " ".join(args)
        quick = raw.strip().lower().endswith(" quick") or raw.strip().lower() == "quick"
        if quick:
            raw = raw.strip()
            if raw.lower().endswith("quick"):
                raw = raw[:-5].rstrip(" |").strip()

        # Splitta per | o , — multi-nicchia se più di uno
        if "|" in raw:
            niches = [n.strip() for n in raw.split("|") if n.strip()]
        else:
            niches = [n.strip() for n in raw.split(",") if n.strip()]
        niches = niches[:5]  # max 5

        if not niches:
            await update.message.reply_text("Specifica almeno una nicchia dopo /niche.", parse_mode="Markdown")
            return

        mode_label = "quick" if quick else "deep"
        is_multi = len(niches) > 1

        if is_multi:
            niches_str = "\n".join(f"  {i+1}. «{n}»" for i, n in enumerate(niches))
            await update.message.reply_text(
                f"🔍 Research Etsy [{mode_label}] — confronto {len(niches)} nicchie:\n{niches_str}\n\n"
                f"Analisi parallela in corso…",
            )
        else:
            await update.message.reply_text(f"🔍 Research Etsy [{mode_label}]: «{niches[0]}»…")

        task = AgentTask(
            task_id=str(_uuid.uuid4()),
            agent_name="research",
            input_data={"niches": niches, "quick": quick, "depth": "quick" if quick else "deep"},
            source="telegram_manual",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            out = result.output_data or {}
            niches_data = out.get("niches", [])

            if is_multi:
                # Risposta multi-nicchia: tabella comparativa + winner
                summary = out.get("summary", "")
                rec_niche = out.get("recommended_niche", "")
                rec_pt = out.get("recommended_product_type", "")

                lines = [f"✅ *Confronto completato: {len(niches_data)} nicchie analizzate*\n"]
                for entry in niches_data:
                    name = entry.get("name", "?")
                    viable = "✅" if entry.get("viable", True) else "⛔"
                    demand = entry.get("demand", {})
                    pricing = entry.get("pricing", {})
                    sweet_spot = pricing.get("conversion_sweet_spot_usd", "—")
                    comp = entry.get("competition", {}).get("level", "—")
                    trend = demand.get("trend", "—")
                    price_str = f"${sweet_spot}" if sweet_spot and sweet_spot != "—" else "—"
                    lines.append(
                        f"{viable} *{_md_escape(name)}*\n"
                        f"   Demand: {demand.get('level','—')} ({trend}) | "
                        f"Competition: {comp} | Sweet spot: {price_str}"
                    )

                if rec_niche:
                    lines.append(f"\n🏆 *Winner: {_md_escape(rec_niche)}* [{_md_escape(rec_pt)}]")
                if summary:
                    lines.append(f"💡 {summary}")

                reply = "\n".join(lines)
            else:
                # Risposta singola nicchia
                if niches_data and isinstance(niches_data, list):
                    entry = niches_data[0]
                    keywords = entry.get("keywords", [])
                    kw_str = ", ".join(keywords[:10]) or "—"
                    demand = entry.get("demand", {})
                    competition = entry.get("competition", {})
                    demand_str = f"{demand.get('level', '—')} ({demand.get('trend', '—')})"
                    comp_str = competition.get("level", "—")
                    viable = "✅ viable" if entry.get("viable", True) else "⛔ non viable"
                    pricing = entry.get("pricing", {})
                    sweet_spot = pricing.get("conversion_sweet_spot_usd")
                    price_str = f" | Sweet spot: ${sweet_spot}" if sweet_spot else ""
                    rec_pt = entry.get("recommended_product_type", "")
                    pt_str = f" | Tipo: {rec_pt}" if rec_pt else ""
                    reply = (
                        f"✅ *Research completato: {_md_escape(niches[0])}*\n\n"
                        f"📊 Demand: {demand_str} | Competition: {comp_str}\n"
                        f"💰 Viable: {viable}{price_str}{_md_escape(pt_str)}\n"
                        f"🔑 Keywords: {_md_escape(kw_str)}"
                    )
                else:
                    # Fallback: cerca dati utili in altre chiavi dell'output LLM
                    winner = out.get("winner") or {}
                    fallback_niche = (
                        out.get("niche")
                        or (winner.get("niche") if winner else "")
                        or niches[0]
                    )
                    summary = out.get("summary") or out.get("analysis") or ""
                    fb_pt = (
                        out.get("recommended_product_type")
                        or out.get("product_type")
                        or (winner.get("product_type") if winner else "")
                        or ""
                    )
                    fb_kw = out.get("keywords") or (winner.get("keywords") if winner else []) or []
                    if summary or fb_pt or fb_kw:
                        kw_str = ", ".join(fb_kw[:10]) if fb_kw else "—"
                        pt_str = f" | Tipo: {fb_pt}" if fb_pt else ""
                        reply = (
                            f"✅ *Research completato: {_md_escape(fallback_niche)}*\n\n"
                            + (f"💡 {_md_escape(summary)}\n" if summary else "")
                            + f"🔑 Keywords: {_md_escape(kw_str)}{_md_escape(pt_str)}"
                        )
                    else:
                        reply = (
                            f"✅ Research completato per «{_md_escape(niches[0])}».\n"
                            f"Nessun dato strutturato restituito.\n\n"
                            f"_Raw output keys: {', '.join(out.keys()) or 'vuoto'}_"
                        )

            await self._reply_chunked(update.message, reply)
        except Exception as exc:
            label = " | ".join(niches)
            logger.error("Research Etsy manuale fallito (%s): %s", label, exc)
            await update.message.reply_text(f"❌ Research fallito: {exc}")

    async def _cmd_design_etsy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/design <nicchia> [png] — avvia il Design Agent Etsy per una nicchia specifica.
        Default: Printable PDF. Aggiungi "png" per Digital Art PNG.
        Es: /design weekly planner
            /design botanical wall art png
        """
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Uso: `/design <nicchia> [png]`\n"
                "Esempi:\n"
                "  `/design weekly planner` — genera PDF\n"
                "  `/design botanical wall art png` — genera Digital Art PNG\n\n"
                "Il Publisher NON viene avviato — i file rimangono in draft.",
                parse_mode="Markdown",
            )
            return

        # Rileva flag [png] come ultimo argomento
        is_png = args[-1].lower() == "png"
        if is_png:
            args = args[:-1]
        niche = " ".join(args).strip()
        if not niche:
            await update.message.reply_text("Specifica una nicchia dopo /design.", parse_mode="Markdown")
            return

        import uuid as _uuid
        product_type = "digital_art_png" if is_png else "printable_pdf"

        # Costruisci brief minimale (Research saltato — keywords vuote)
        task_id = str(_uuid.uuid4())
        if product_type == "digital_art_png":
            # Importa _pick_art_type dal scheduler se disponibile, altrimenti inferisci inline
            art_type = (
                self.scheduler._pick_art_type(niche)
                if self.scheduler
                else "wall_art"
            )
            brief = {
                "niche": niche,
                "product_type": "digital_art_png",
                "art_type": art_type,
                "num_variants": 3,
                "color_schemes": ["warm", "neutral", "pastel"],
                "keywords": [],
                "production_queue_task_id": task_id,
            }
            label = f"🖼 Design PNG: «{niche}» (art_type: {art_type})"
        else:
            pdf_template = (
                self.scheduler._pick_template(niche)
                if self.scheduler
                else "weekly_planner"
            )
            brief = {
                "niche": niche,
                "product_type": "printable_pdf",
                "template": pdf_template,
                "size": "A4",
                "num_variants": 3,
                "color_schemes": ["sage", "blush", "slate"],
                "keywords": [],
                "production_queue_task_id": task_id,
            }
            label = f"🎨 Design PDF: «{niche}» (template: {pdf_template})"

        await update.message.reply_text(f"{label}\nIl Publisher non verrà avviato.")
        task = AgentTask(
            task_id=task_id,
            agent_name="design",
            input_data=brief,
            source="telegram_manual",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            out = result.output_data or {}
            variants = out.get("variants", [])
            # Estrai file paths in base al product_type (schema diverso)
            if product_type == "digital_art_png":
                file_paths = [v["file_path"] for v in variants if v.get("file_path")]
                provider = out.get("image_provider", "—")
                meta_line = f"🖼 Art type: {out.get('art_type', '—')} | Provider: {provider}"
            else:
                file_paths = [v["pdf_path"] for v in variants if v.get("pdf_path")]
                meta_line = f"🎨 Preset: {out.get('preset', '—')} | Template: {out.get('template', '—')}"
            cost = result.cost_usd or 0.0
            files_str = "\n".join(f"  • {Path(p).name}" for p in file_paths[:5]) or "  —"
            extra = f"\n  …e altri {len(file_paths) - 5}" if len(file_paths) > 5 else ""
            await update.message.reply_text(
                f"✅ Design completato: {niche}\n\n"
                f"{meta_line}\n"
                f"📁 File generati ({len(file_paths)}):\n{files_str}{extra}\n"
                f"💰 Costo: ${cost:.4f}",
            )
        except Exception as exc:
            logger.error("Design Etsy manuale fallito (%s): %s", niche, exc)
            await update.message.reply_text(f"❌ Design fallito: {exc}")

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/list — lista tutti i comandi disponibili."""
        domain = self.pepe.get_active_domain()
        domain_name = domain.name if domain else "personal"
        domain_icon = "🧠" if not domain else "🏪"
        lines = [
            f"📋 *Comandi AgentPeXI* ({domain_icon} {domain_name})\n",
            "*— Sistema —*",
            "/status — stato agenti, coda, dominio attivo",
            "/list — questo messaggio",
            "/pause — ferma i worker",
            "/resume — riavvia i worker",
            "/new — nuova sessione (azzera conversazione)",
            "",
            "*— Dominio —*",
            "/personal — passa al dominio Personal (Ollama locale)",
            "/etsy — passa al dominio Etsy store (Claude)",
            "/screen [on|off|status] — gestione Screen Watcher",
            "",
            "*— Etsy —*",
            "/pipeline [png] — avvia Research → Design → Publisher (PDF di default, aggiungi png per Digital Art)",
            "/niche <nicchia> [quick] — Research singola nicchia (deep di default)",
            "/niche <n1> | <n2> [quick] — confronto multi-nicchia (separatore | o ,, max 5)",
            "/design <nicchia> [png] — Design Agent standalone (PDF di default, aggiungi png per Digital Art)",
            "/analytics — esegue subito il job analytics",
            "/finance — genera report economico",
            "/listings — lista ultimi 10 listing",
            "/mock [on|off] — attiva/disattiva mock mode",
            "/wiki [stats|query|lint|health] — knowledge base wiki",
            "",
            "*— Personal —*",
            "/remind <testo> alle <quando> — crea reminder",
            "/reminders — lista reminder attivi",
            "/summarize <url|testo> [short] — riassumi contenuto",
            "/research <domanda> [quick] — ricerca web strutturata",
            "/feedback positivo|negativo <keyword> — insegna al sistema",
            "/urgency add <keyword> — aggiungi keyword ad alta urgenza",
            "",
            "*— Interazione —*",
            "/ask <domanda> — chiede qualcosa a Pepe",
            "/report — report stato sistema",
            "/retry [task\\_id] — riprova ultimo task fallito",
            "/resume\\_agent <nome> — riattiva agente sospeso",
            "",
            "💬 Oppure scrivi direttamente — Pepe risponde.",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/screen [on|off|status] — gestione Screen Watcher."""
        arg = (context.args[0].lower() if context.args else "status")

        if not self.screen_watcher:
            await update.message.reply_text(
                "❌ *Screen Watcher non disponibile*\n\n"
                "Il servizio non è partito all'avvio del server.\n"
                "Cause probabili: `mss`, `pyobjc` o `Vision` non installati.\n\n"
                "Controlla i log del server per il dettaglio dell'errore.",
                parse_mode="Markdown",
            )
            return

        if arg == "off":
            self.screen_watcher.pause()
            await update.message.reply_text(
                "⏸️ *Screen Watcher in pausa*\n\nNon catturerò più lo schermo.\nUsa /screen on per riprendere.",
                parse_mode="Markdown",
            )

        elif arg == "on":
            self.screen_watcher.resume()
            await update.message.reply_text(
                "▶️ *Screen Watcher attivo*\n\nRiprendo a monitorare lo schermo.",
                parse_mode="Markdown",
            )

        else:  # status (default)
            st = self.screen_watcher.get_status()
            icon = "▶️" if st["active"] else "⏸️"
            stato = "Attivo" if st["active"] else "In pausa"
            last_app = _md_escape(st["last_capture_app"] or "—")
            last_time = st["last_capture_time"] or "—"
            if last_time and last_time != "—":
                try:
                    from datetime import datetime
                    last_time = datetime.fromisoformat(last_time).strftime("%d/%m %H:%M")
                except Exception:
                    pass
            await update.message.reply_text(
                f"{icon} *Screen Watcher*: {stato}\n\n"
                f"📸 Catture oggi: {st['captures_today']}\n"
                f"🖥️ Ultima app: {last_app}\n"
                f"🕐 Ultima cattura: {last_time}\n\n"
                "Comandi: `/screen on` · `/screen off` · `/screen status`",
                parse_mode="Markdown",
            )

    async def _cmd_personal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/personal — passa al dominio Personal (Ollama locale, privacy totale)."""
        self.pepe.set_active_domain(None)
        if self.pepe._ws_broadcast:
            await self.pepe._ws_broadcast({
                "type": "system_status",
                "domain": "personal",
                "message": "Dominio Personal attivato",
            })
        await update.message.reply_text(
            "🧠 *Dominio business disattivato*\n\n"
            "Gli agenti personal sono sempre disponibili — "
            "usa /etsy per attivare la pipeline Etsy.",
            parse_mode="Markdown",
        )

    async def _cmd_etsy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/etsy — torna al dominio Etsy store."""
        self.pepe.set_active_domain(DOMAIN_ETSY)
        if self.pepe._ws_broadcast:
            await self.pepe._ws_broadcast({
                "type": "system_status",
                "domain": "etsy_store",
                "message": "Dominio Etsy attivato",
            })
        await update.message.reply_text(
            "🏪 *Dominio Etsy attivo*\n\n"
            "Gli agenti personal rimangono disponibili in qualsiasi momento.",
            parse_mode="Markdown",
        )

    async def _cmd_mock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/mock [on|off] — attiva o disattiva mock mode Etsy."""
        args = context.args or []
        arg = args[0].lower() if args else ""

        if arg == "on":
            self.pepe.set_mock_mode(True)
            if self.pepe._ws_broadcast:
                await self.pepe._ws_broadcast({
                    "type": "system_status",
                    "mock_mode": True,
                    "message": "Mock mode attivato",
                })
            await update.message.reply_text(
                "🟡 *MOCK MODE ATTIVO*\n\n"
                "Etsy API e Replicate sono simulati.\n"
                "I listing vengono salvati nel DB locale.\n"
                "Usa /ask per avviare una pipeline di test.",
                parse_mode="Markdown",
            )

        elif arg == "off":
            self.pepe.set_mock_mode(False)
            if self.pepe._ws_broadcast:
                await self.pepe._ws_broadcast({
                    "type": "system_status",
                    "mock_mode": False,
                    "message": "Mock mode disattivato",
                })
            await update.message.reply_text(
                "✅ *Mock mode disattivato*\n\n"
                "Il sistema tornerà a usare Etsy API reale "
                "non appena i token saranno disponibili.",
                parse_mode="Markdown",
            )

        else:
            status = "🟡 ATTIVO" if self.pepe.mock_mode else "⚫ INATTIVO"
            await update.message.reply_text(
                f"*Mock Mode*: {status}\n\n"
                "Uso: `/mock on` oppure `/mock off`",
                parse_mode="Markdown",
            )

    # ------------------------------------------------------------------
    # Comandi Personal (Blocco 3)
    # ------------------------------------------------------------------

    async def _cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/remind <testo> alle <quando> [ogni <ricorrenza>]
        Es: /remind chiamare il dentista alle 15:00 domani
            /remind bolletta luce entro venerdì ogni monthly:1
        """
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text(
                "Uso: `/remind <testo> alle <quando>`\n"
                "Esempio: `/remind riunione alle 15:00 domani`",
                parse_mode="Markdown",
            )
            return

        # Parsing grezzo: tutto prima di "ogni" è testo+quando
        recurring = None
        if " ogni " in text:
            parts = text.split(" ogni ", 1)
            text = parts[0].strip()
            recurring = parts[1].strip()

        # Divide testo da quando: cerca "alle", "entro", "il ", "tra "
        when = ""
        for kw in (" alle ", " entro ", " il ", " tra ", " domani", " dopodomani"):
            if kw in text.lower():
                idx = text.lower().index(kw)
                when = text[idx:].strip()
                text = text[:idx].strip()
                break
        if not when:
            when = text   # fallback: tutto è when

        import uuid as _uuid
        task = AgentTask(
            task_id=str(_uuid.uuid4()),
            agent_name="remind",
            input_data={"action": "create", "text": f"{text} {when}".strip(), "recurring": recurring},
            source="telegram",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            reply = (result.output_data or {}).get("reply") or (result.output_data or {}).get("error", "Errore remind.")
        except Exception as exc:
            logger.error("dispatch_task remind create fallito: %s", exc)
            reply = f"⚠️ Errore agente remind: {exc}"
        await self._reply_chunked(update.message, reply)

    async def _cmd_remind_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/reminders — lista reminder attivi."""
        import uuid as _uuid
        task = AgentTask(
            task_id=str(_uuid.uuid4()),
            agent_name="remind",
            input_data={"action": "list"},
            source="telegram",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            reply = (result.output_data or {}).get("reply") or (result.output_data or {}).get("error", "Errore remind.")
        except Exception as exc:
            logger.error("dispatch_task remind list fallito: %s", exc)
            reply = f"⚠️ Errore agente remind: {exc}"
        await self._reply_chunked(update.message, reply)

    async def _cmd_summarize(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/summarize <url|testo> [short] — riassume URL o testo.
        Supporta anche allegati inoltrati al bot.
        """
        args = context.args or []
        mode = "short" if args and args[-1].lower() == "short" else "detailed"
        if mode == "short":
            args = args[:-1]

        content = " ".join(args).strip()

        # Controlla se c'è un documento allegato nel messaggio
        file_id = None
        if update.message.document:
            file_id = update.message.document.file_id

        if not content and not file_id:
            await update.message.reply_text(
                "Uso: `/summarize <url> [short]` oppure inoltra un PDF/TXT al bot.\n"
                "Esempio: `/summarize https://example.com/article short`",
                parse_mode="Markdown",
            )
            return

        session_id = str(update.effective_chat.id)
        if file_id:
            msg = f"summarize file_id: {file_id} mode: {mode}"
        elif content.startswith("http"):
            msg = f"summarize url: {content} mode: {mode}"
        else:
            msg = f"summarize text: {content} mode: {mode}"

        import uuid as _uuid
        if file_id:
            source_type, content = "file", file_id
        elif content.startswith("http"):
            source_type = "url"
        else:
            source_type = "text"
        length = "brief" if mode == "short" else "normal"
        await update.message.reply_text("📄 Sto leggendo e riassumendo…")
        task = AgentTask(
            task_id=str(_uuid.uuid4()),
            agent_name="summarize",
            input_data={"source_type": source_type, "content": content, "length": length, "save": True},
            source="telegram",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            reply = (result.output_data or {}).get("reply") or (result.output_data or {}).get("error", "Errore summarize.")
        except Exception as exc:
            logger.error("dispatch_task summarize fallito: %s", exc)
            reply = f"⚠️ Errore agente summarize: {exc}"
        await self._reply_chunked(update.message, reply)

    async def _cmd_research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/research <query> [quick] — ricerca web + risposta strutturata.
        Es: /research come funziona la VAT UE per i venditori Etsy
            /research meteo Milano quick
        """
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Uso: `/research <domanda> [quick]`\n"
                "Esempio: `/research vantaggi regime forfettario`",
                parse_mode="Markdown",
            )
            return

        mode = "quick" if args[-1].lower() == "quick" else "deep"
        if mode == "quick":
            args = args[:-1]
        query = " ".join(args).strip()

        session_id = str(update.effective_chat.id)
        import uuid as _uuid
        await update.message.reply_text(f"🔍 Ricerco: «{query}»…")
        task = AgentTask(
            task_id=str(_uuid.uuid4()),
            agent_name="research_personal",
            input_data={"query": query, "depth": mode},
            source="telegram",
        )
        try:
            result = await self.pepe.dispatch_task(task)
            reply = (result.output_data or {}).get("response") or (result.output_data or {}).get("error", "Errore research.")
        except Exception as exc:
            logger.error("dispatch_task research_personal fallito: %s", exc)
            reply = f"⚠️ Errore agente research: {exc}"
        await self._reply_chunked(update.message, reply)

    async def _cmd_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/feedback <positivo|negativo> <parola_chiave>
        Insegna al sistema cosa è importante o no.
        Es: /feedback positivo scadenza
            /feedback negativo netflix
        """
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Uso: `/feedback positivo|negativo <parola_chiave>`\n"
                "Esempio: `/feedback positivo scadenza`",
                parse_mode="Markdown",
            )
            return

        signal_raw = args[0].lower()
        keyword = " ".join(args[1:]).lower().strip()

        if signal_raw in ("positivo", "positive", "sì", "si", "yes"):
            signal = "positive"
        elif signal_raw in ("negativo", "negative", "no"):
            signal = "negative"
        else:
            await update.message.reply_text(
                "Segnale non riconosciuto. Usa `positivo` o `negativo`.",
                parse_mode="Markdown",
            )
            return

        weight_delta = 0.1 if signal == "positive" else -0.1
        try:
            await self.pepe.memory.upsert_learning(
                agent="urgency",
                pattern_type="keyword",
                pattern_value=keyword,
                signal_type=signal,
                weight_delta=weight_delta,
            )
            icon = "✅" if signal == "positive" else "🔕"
            reply = f"{icon} Capito. Quando vedo «{keyword}» lo tratterò come {'prioritario' if signal == 'positive' else 'rumore'}."
        except Exception as exc:
            reply = f"❌ Errore salvataggio feedback: {exc}"

        await update.message.reply_text(reply)

    async def _cmd_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/urgency add <keyword>
        Aggiunge una keyword prioritaria al sistema urgenza.
        Quando Pepe vede questa keyword la tratterà sempre come HIGH.
        Es: /urgency add scadenza
            /urgency add fattura
        """
        args = context.args or []

        if not args or args[0].lower() != "add" or len(args) < 2:
            await update.message.reply_text(
                "Uso: `/urgency add <keyword>`\n"
                "Esempio: `/urgency add scadenza`\n\n"
                "Insegna a Pepe quali parole indicano sempre urgenza alta.",
                parse_mode="Markdown",
            )
            return

        keyword = " ".join(args[1:]).lower().strip()
        if not keyword:
            await update.message.reply_text("Specifica la keyword dopo `add`.", parse_mode="Markdown")
            return

        try:
            # weight_delta=0.3 → initial weight=0.8 su nuovo record (0.5+0.3)
            # Su record esistente spinge verso 0.9 (clampato)
            await self.pepe.memory.upsert_learning(
                agent="urgency",
                pattern_type="keyword",
                pattern_value=keyword,
                signal_type="explicit_positive",
                weight_delta=0.3,
            )
            await update.message.reply_text(
                f"🔴 «{keyword}» aggiunta come keyword ad alta urgenza.\n"
                f"D'ora in poi i messaggi che la contengono saranno trattati come HIGH."
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Errore salvataggio: {exc}")

    # ------------------------------------------------------------------
    # Wiki commands — Step 5.2.6
    # ------------------------------------------------------------------

    async def _cmd_wiki(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/wiki [stats|query <testo>|lint [etsy|personal]|health]

        Subcomandi:
          /wiki stats              — statistiche aggregate wiki
          /wiki query <testo>      — query wiki dominio Etsy
          /wiki lint [etsy|personal] — lint: wikilinks rotti + raw pending
          /wiki health             — esegue health check manuale (compact+lint+update_index)
        """
        wiki = getattr(self.pepe, "wiki", None)
        if wiki is None:
            await update.message.reply_text("❌ WikiManager non inizializzato. Controlla i log.")
            return

        args = context.args or []
        sub = args[0].lower() if args else "stats"

        # ── /wiki stats ──────────────────────────────────────────────────
        if sub == "stats":
            try:
                stats = await wiki.get_stats()
                lines = [
                    "📚 *Wiki — Statistiche*\n",
                    f"🏪 Etsy nicchie: {stats.get('etsy_niches', 0)}",
                    f"📊 Etsy pattern: {stats.get('etsy_patterns', 0)}",
                    f"🧠 Personal file: {stats.get('personal_files', 0)}",
                    f"📥 Raw totale: {stats.get('total_raw', 0)}",
                    f"⏳ Raw pending: {stats.get('pending_raw', 0)}",
                ]
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            except Exception as exc:
                await update.message.reply_text(f"❌ Errore stats: {exc}")

        # ── /wiki query <testo> ──────────────────────────────────────────
        elif sub == "query":
            q = " ".join(args[1:]).strip()
            if not q:
                await update.message.reply_text(
                    "Uso: `/wiki query <testo>`\nEsempio: `/wiki query weekly planner trends`",
                    parse_mode="Markdown",
                )
                return
            await update.message.reply_text(f"🔍 Query wiki Etsy: «{q}»…")
            try:
                result = await wiki.query("etsy", q, self.pepe.client)
                if result:
                    await self._reply_chunked(update.message, f"📚 *Wiki result*\n\n{result}")
                else:
                    await update.message.reply_text("Nessun risultato nella wiki per questa query.")
            except Exception as exc:
                await update.message.reply_text(f"❌ Errore query: {exc}")

        # ── /wiki lint [etsy|personal] ───────────────────────────────────
        elif sub == "lint":
            domain = args[1].lower() if len(args) > 1 and args[1].lower() in ("etsy", "personal") else "etsy"
            llm = self.pepe._local_client if domain == "personal" else self.pepe.client
            await update.message.reply_text(f"🔍 Lint wiki *{domain}*…", parse_mode="Markdown")
            try:
                report = await wiki.lint(domain, llm)
                header = f"📋 *Wiki lint — {domain}*\n\n"
                await self._reply_chunked(update.message, header + (report or "Nessun problema trovato."))
            except Exception as exc:
                await update.message.reply_text(f"❌ Errore lint: {exc}")

        # ── /wiki health ─────────────────────────────────────────────────
        elif sub == "health":
            await update.message.reply_text("⏳ Wiki health check in avvio (compact + lint + update_index)…")
            try:
                if self.scheduler:
                    task = asyncio.create_task(
                        self.scheduler._run_wiki_health_check(),
                        name="wiki_health_manual",
                    )
                    task.add_done_callback(
                        lambda t: logger.error("Wiki health check fallito: %s", t.exception())
                        if not t.cancelled() and t.exception() else None
                    )
                else:
                    # Fallback diretto senza scheduler
                    llm_etsy     = self.pepe.client
                    llm_personal = self.pepe._local_client
                    for domain, llm in (("etsy", llm_etsy), ("personal", llm_personal)):
                        await wiki.compact_wiki(domain, llm)
                        await wiki.update_index(domain, llm)
                    await update.message.reply_text("✅ Health check completato (senza scheduler).")
            except Exception as exc:
                await update.message.reply_text(f"❌ Health check fallito: {exc}")

        # ── subcomando sconosciuto ───────────────────────────────────────
        else:
            await update.message.reply_text(
                "📚 *Wiki — Comandi disponibili*\n\n"
                "`/wiki stats` — statistiche aggregate\n"
                "`/wiki query <testo>` — query knowledge base Etsy\n"
                "`/wiki lint [etsy|personal]` — lint wikilinks e raw pending\n"
                "`/wiki health` — esegui health check manuale",
                parse_mode="Markdown",
            )

    # ------------------------------------------------------------------
    # Handler messaggi testo
    # ------------------------------------------------------------------

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Messaggio testo → Pepe con session_id da chat_id.

        Se il messaggio è una reply a un messaggio del bot, prova prima
        l'acknowledgment del reminder corrispondente (via telegram_msg_id).
        """
        text = update.message.text
        session_id = str(update.effective_chat.id)

        # --- Reply handler: tentativo ACK reminder ---
        if update.message.reply_to_message is not None:
            replied_msg_id = update.message.reply_to_message.message_id
            acked = await self.pepe.memory.acknowledge_reminder(replied_msg_id)
            if acked:
                # Aggiorna Notion se disponibile
                notion_page_id = await self.pepe.memory.get_reminder_notion_id(replied_msg_id)
                if notion_page_id and getattr(settings, "NOTION_API_TOKEN", ""):
                    try:
                        from apps.backend.tools.notion_calendar import NotionCalendar
                        nc = NotionCalendar(token=settings.NOTION_API_TOKEN)
                        await nc.update_status(notion_page_id, "Done")
                    except Exception as exc:
                        logger.debug("ACK Notion update fallito (fail-safe): %s", exc)
                await update.message.reply_text("✅ Reminder confermato.")
                return
            # Nessun reminder trovato per questo msg — procede normalmente

        reply = await self.pepe.handle_user_message(text, source="telegram", session_id=session_id)
        await self._reply_chunked(update.message, reply)

    # ------------------------------------------------------------------
    # Handler messaggi vocali
    # ------------------------------------------------------------------

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Vocale → STT → Pepe → TTS → risposta audio."""
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        # Scarica file OGG in temp
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        try:
            # STT: OGG → testo
            transcription = await self._transcribe(tmp_path)
            if not transcription:
                await update.message.reply_text("🔇 Non ho capito l'audio, riprova.")
                return

            # Invia trascrizione come feedback
            await update.message.reply_text(f"🎤 _{_md_escape(transcription)}_", parse_mode="Markdown")

            # Pepe elabora
            session_id = str(update.effective_chat.id)
            reply = await self.pepe.handle_user_message(transcription, source="telegram", session_id=session_id)

            # Risposta testuale
            await update.message.reply_text(reply)

            # TTS: testo → audio
            audio_bytes = await self._synthesize(reply)
            if audio_bytes:
                await update.message.reply_voice(voice=audio_bytes)

        finally:
            # Pulizia file temporaneo
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # STT / TTS helpers (lazy import — moduli voice creati in step 1.8)
    # ------------------------------------------------------------------

    async def _transcribe(self, audio_path: str) -> str:
        """Trascrive audio via faster-whisper (lazy import)."""
        try:
            from apps.backend.voice.stt import transcribe
            return await transcribe(audio_path)
        except ImportError:
            logger.warning("Modulo voice.stt non disponibile — STT disabilitato")
            return ""
        except Exception as exc:
            logger.error("Errore STT: %s", exc)
            return ""

    async def _synthesize(self, text: str) -> bytes | None:
        """Sintetizza audio via ElevenLabs (lazy import)."""
        try:
            from apps.backend.voice.tts import synthesize
            return await synthesize(text)
        except ImportError:
            logger.debug("Modulo voice.tts non disponibile — TTS disabilitato")
            return None
        except Exception as exc:
            logger.error("Errore TTS: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Notifiche (callback registrato in Pepe)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _TG_LIMIT = 4000  # Telegram max ~4096 — lascia margine

    async def _reply_chunked(self, message: "telegram.Message", text: str) -> None:
        """Invia risposta spezzandola in chunk se supera il limite Telegram."""
        if len(text) <= self._TG_LIMIT:
            await message.reply_text(text)
            return
        # Spezza su newline per non troncare a metà riga
        lines: list[str] = text.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > self._TG_LIMIT:
                if chunk:
                    await message.reply_text(chunk.rstrip())
                chunk = line
            else:
                chunk += line
        if chunk.strip():
            await message.reply_text(chunk.rstrip())

    async def _send_reminder_notification(self, message: str) -> int:
        """Invia reminder e restituisce il telegram message_id (per ACK via reply)."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return 0
        try:
            sent = await self._app.bot.send_message(
                chat_id=int(settings.TELEGRAM_CHAT_ID), text=message
            )
            return sent.message_id
        except Exception as exc:
            logger.error("_send_reminder_notification fallito: %s", exc)
            return 0

    async def _send_notification(self, message: str, priority: bool = False) -> None:
        """Invia notifica a Andrea via Telegram, spezzando se necessario."""
        if not self._app or not settings.TELEGRAM_CHAT_ID:
            return
        chat_id = int(settings.TELEGRAM_CHAT_ID)
        text = message  # nessun prefisso emoji — tono consulente
        if len(text) <= self._TG_LIMIT:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
            return
        # Chunk lungo
        lines = text.splitlines(keepends=True)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) > self._TG_LIMIT:
                if chunk:
                    await self._app.bot.send_message(chat_id=chat_id, text=chunk.rstrip())
                chunk = line
            else:
                chunk += line
        if chunk.strip():
            await self._app.bot.send_message(chat_id=chat_id, text=chunk.rstrip())
