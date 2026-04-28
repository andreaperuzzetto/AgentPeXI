"""Handler Telegram — pipeline Etsy e comandi Personal.

Comandi Etsy:    /listings, /niche, /design, /analytics, /finance
Comandi Personal: /remind, /reminders, /summarize, /research,
                  /feedback, /urgency
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from apps.backend.core.models import AgentTask
from apps.backend.telegram.formatters import md_escape, reply_chunked

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


# ---------------------------------------------------------------------------
# Design helpers (da scheduler — spostati qui in B3/3.1, vivranno qui fino
# a quando non si renderizza un servizio dedicato)
# ---------------------------------------------------------------------------

def _pick_template(niche: str) -> str:
    """Inferisce template PDF dal nome della nicchia."""
    n = niche.lower()
    if "habit" in n:
        return "habit_tracker"
    if "budget" in n or "finance" in n or "expense" in n:
        return "budget_tracker"
    if "meal" in n or "food" in n or "recipe" in n:
        return "meal_planner"
    if "workout" in n or "fitness" in n or "exercise" in n:
        return "workout_tracker"
    if "journal" in n or "diary" in n or "gratitude" in n:
        return "gratitude_journal"
    if "reading" in n or "book" in n:
        return "reading_log"
    if "travel" in n or "trip" in n or "itinerary" in n:
        return "travel_planner"
    if "goal" in n or "vision" in n or "resolution" in n:
        return "goal_planner"
    if "project" in n or "task" in n or "checklist" in n:
        return "project_planner"
    if "daily" in n or "day" in n:
        return "daily_planner"
    if "monthly" in n or "month" in n:
        return "monthly_planner"
    return "weekly_planner"


def _pick_art_type(niche: str) -> str:
    """Inferisce art_type per Digital Art PNG dal nome della nicchia."""
    n = niche.lower()
    if "quote" in n or "inspirational" in n or "motivation" in n or "saying" in n:
        return "quote_print"
    if "botanical" in n or "plant" in n or "floral" in n or "flower" in n or "leaf" in n:
        return "botanical_print"
    if "nursery" in n or "kids" in n or "baby" in n or "children" in n or "animal" in n:
        return "nursery_print"
    return "wall_art"


# ---------------------------------------------------------------------------
# /listings
# ---------------------------------------------------------------------------

async def cmd_listings(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/listings — lista listing Etsy recenti."""
    rows = await deps.pepe.memory.get_etsy_listings(limit=10)
    if not rows:
        await update.message.reply_text("Nessun listing trovato.")
        return
    lines = ["📦 *Listing recenti*\n"]
    for row in rows:
        lines.append(
            f"• {row['title'][:40]} — {row['status']} | 🛒 {row['sales']} | €{row['revenue_eur']:.2f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /niche
# ---------------------------------------------------------------------------

async def cmd_niche(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/niche <nicchia> [quick] — singola o multi-nicchia."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Uso:\n"
            "  `/niche <nicchia> [quick]` — singola nicchia\n"
            "  `/niche <n1> | <n2> [quick]` — confronto multi-nicchia\n\n"
            "Esempi:\n"
            "  `/niche weekly planner`\n"
            "  `/niche weekly planner | habit tracker | budget sheet`\n\n"
            "Deep di default. Aggiungi `quick` per scansione rapida.",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(args)
    quick = raw.strip().lower().endswith(" quick") or raw.strip().lower() == "quick"
    if quick:
        raw = raw.strip()
        if raw.lower().endswith("quick"):
            raw = raw[:-5].rstrip(" |").strip()

    if "|" in raw:
        niches = [n.strip() for n in raw.split("|") if n.strip()]
    else:
        niches = [n.strip() for n in raw.split(",") if n.strip()]
    niches = niches[:5]

    if not niches:
        await update.message.reply_text(
            "Specifica almeno una nicchia dopo /niche.", parse_mode="Markdown"
        )
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
        task_id=str(uuid.uuid4()),
        agent_name="research",
        input_data={"niches": niches, "quick": quick, "depth": "quick" if quick else "deep"},
        source="telegram_manual",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        out = result.output_data or {}
        niches_data = out.get("niches", [])

        if is_multi:
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
                    f"{viable} *{md_escape(name)}*\n"
                    f"   Demand: {demand.get('level','—')} ({trend}) | "
                    f"Competition: {comp} | Sweet spot: {price_str}"
                )
            if rec_niche:
                lines.append(f"\n🏆 *Winner: {md_escape(rec_niche)}* [{md_escape(rec_pt)}]")
            if summary:
                lines.append(f"💡 {summary}")
            reply = "\n".join(lines)

        else:
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
                    f"✅ *Research completato: {md_escape(niches[0])}*\n\n"
                    f"📊 Demand: {demand_str} | Competition: {comp_str}\n"
                    f"💰 Viable: {viable}{price_str}{md_escape(pt_str)}\n"
                    f"🔑 Keywords: {md_escape(kw_str)}"
                )
            else:
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
                        f"✅ *Research completato: {md_escape(fallback_niche)}*\n\n"
                        + (f"💡 {md_escape(summary)}\n" if summary else "")
                        + f"🔑 Keywords: {md_escape(kw_str)}{md_escape(pt_str)}"
                    )
                else:
                    reply = (
                        f"✅ Research completato per «{md_escape(niches[0])}».\n"
                        f"Nessun dato strutturato restituito.\n\n"
                        f"_Raw output keys: {', '.join(out.keys()) or 'vuoto'}_"
                    )

        await reply_chunked(update.message, reply)

    except Exception as exc:
        label = " | ".join(niches)
        logger.error("Research Etsy manuale fallito (%s): %s", label, exc)
        await update.message.reply_text(f"❌ Research fallito: {exc}")


# ---------------------------------------------------------------------------
# /design
# ---------------------------------------------------------------------------

async def cmd_design_etsy(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/design <nicchia> [png] — Design Agent standalone, Publisher NON avviato."""
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

    is_png = args[-1].lower() == "png"
    if is_png:
        args = args[:-1]
    niche = " ".join(args).strip()
    if not niche:
        await update.message.reply_text(
            "Specifica una nicchia dopo /design.", parse_mode="Markdown"
        )
        return

    product_type = "digital_art_png" if is_png else "printable_pdf"
    task_id = str(uuid.uuid4())

    if product_type == "digital_art_png":
        art_type = _pick_art_type(niche)
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
        pdf_template = _pick_template(niche)
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
        result = await deps.pepe.dispatch_task(task)
        out = result.output_data or {}
        variants = out.get("variants", [])
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


# ---------------------------------------------------------------------------
# /analytics
# ---------------------------------------------------------------------------

async def cmd_analytics(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/analytics — esegue subito il job analytics."""
    await update.message.reply_text("⏳ Avvio analytics manuale...")
    try:
        from apps.backend.core.models import AgentTask as _AgentTask
        task = _AgentTask(agent_name="analytics", input_data={}, source="telegram_manual")
        result = await deps.pepe.dispatch_task(task)
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


# ---------------------------------------------------------------------------
# /ladder — Ladder System diagnostic (B4)
# ---------------------------------------------------------------------------

_LADDER_ICONS = {
    "ok":        "✅",
    "too_new":   "🕐",
    "views_low": "🔍",
    "ctr_low":   "🖼",
    "conv_low":  "📝",
}
_LADDER_LABELS = {
    "ok":        "OK",
    "too_new":   "Troppo nuovo",
    "views_low": "Views basse — SEO",
    "ctr_low":   "CTR basso — thumbnail",
    "conv_low":  "Conv bassa — listing",
}


def _fmt_ladder_single(r: dict) -> str:
    """Formatta un risultato diagnostico singolo."""
    if "error" in r:
        return f"❌ {r['error']}"
    level   = r.get("level", "?")
    icon    = _LADDER_ICONS.get(level, "❓")
    label   = _LADDER_LABELS.get(level, level)
    niche   = r.get("niche", "?")
    action  = r.get("action") or "—"
    views   = r.get("views", "?")
    ctr     = r.get("ctr", "?")
    conv    = r.get("conv", "?")
    days    = r.get("days_live", "?")
    return (
        f"{icon} [{r.get('item_id', '?')}] {niche}\n"
        f"   Livello: {label}\n"
        f"   Views: {views}  CTR: {ctr}  Conv: {conv}  Giorni: {days}\n"
        f"   Azione: {action}"
    )


def _fmt_ladder_summary(results: list[dict]) -> str:
    """Formatta il riepilogo diagnostica su tutto il portfolio."""
    if not results:
        return "ℹ️ Nessun listing pubblicato da diagnosticare."

    counts: dict[str, int] = {}
    for r in results:
        lv = r.get("level", "?")
        counts[lv] = counts.get(lv, 0) + 1

    lines = ["📊 Ladder System — diagnostica portfolio\n"]
    for level, cnt in sorted(counts.items()):
        icon  = _LADDER_ICONS.get(level, "❓")
        label = _LADDER_LABELS.get(level, level)
        lines.append(f"  {icon} {label}: {cnt}")

    critical = [r for r in results if r.get("level") in ("views_low", "ctr_low", "conv_low")]
    if critical:
        lines.append(f"\n⚠️ Critici ({len(critical)}):")
        for r in critical[:5]:
            lines.append(f"  • [{r.get('item_id')}] {r.get('niche','?')} — {r.get('action','?')}")
        if len(critical) > 5:
            lines.append(f"  … e altri {len(critical) - 5}")

    return "\n".join(lines)


async def cmd_ladder(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/ladder [id] — diagnostica Ladder System su listing specifico o portfolio."""
    if deps.analytics_agent is None:
        await update.message.reply_text("❌ AnalyticsAgent non disponibile.")
        return

    if context.args:
        try:
            item_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Uso: /ladder [queue_item_id]")
            return
        await update.message.reply_text("⏳ Diagnostica in corso...")
        try:
            result = await deps.analytics_agent.run_ladder_diagnostic_by_id(item_id)
            await update.message.reply_text(_fmt_ladder_single(result))
        except Exception as exc:
            logger.error("Ladder diagnostic id=%s fallito: %s", item_id, exc)
            await update.message.reply_text(f"❌ Diagnostica fallita: {exc}")
    else:
        await update.message.reply_text("⏳ Diagnostica portfolio in corso...")
        try:
            results = await deps.analytics_agent.run_ladder_diagnostic_all()
            await update.message.reply_text(_fmt_ladder_summary(results))
        except Exception as exc:
            logger.error("Ladder diagnostic all fallito: %s", exc)
            await update.message.reply_text(f"❌ Diagnostica fallita: {exc}")


# ---------------------------------------------------------------------------
# /finance
# ---------------------------------------------------------------------------

async def cmd_finance(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/finance — avvia manualmente il Finance Agent."""
    if not deps.scheduler:
        await update.message.reply_text("❌ Scheduler non disponibile.")
        return
    await update.message.reply_text("⏳ Finance report in avvio...")
    task = asyncio.create_task(deps.scheduler._run_finance(), name="finance_manual")
    task.add_done_callback(
        lambda t: logger.error("Finance manuale fallito: %s", t.exception())
        if not t.cancelled() and t.exception() else None
    )


# ---------------------------------------------------------------------------
# Personal — /remind / /reminders
# ---------------------------------------------------------------------------

async def cmd_remind(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/remind <testo> alle <quando> [ogni <ricorrenza>]"""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Uso: `/remind <testo> alle <quando>`\n"
            "Esempio: `/remind riunione alle 15:00 domani`",
            parse_mode="Markdown",
        )
        return

    recurring = None
    if " ogni " in text:
        parts = text.split(" ogni ", 1)
        text = parts[0].strip()
        recurring = parts[1].strip()

    when = ""
    for kw in (" alle ", " entro ", " il ", " tra ", " domani", " dopodomani"):
        if kw in text.lower():
            idx = text.lower().index(kw)
            when = text[idx:].strip()
            text = text[:idx].strip()
            break
    if not when:
        when = text

    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="remind",
        input_data={"action": "create", "text": f"{text} {when}".strip(), "recurring": recurring},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore remind.")
        )
    except Exception as exc:
        logger.error("dispatch_task remind create fallito: %s", exc)
        reply = f"⚠️ Errore agente remind: {exc}"
    await reply_chunked(update.message, reply)


async def cmd_remind_list(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/reminders — lista reminder attivi."""
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="remind",
        input_data={"action": "list"},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore remind.")
        )
    except Exception as exc:
        logger.error("dispatch_task remind list fallito: %s", exc)
        reply = f"⚠️ Errore agente remind: {exc}"
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# Personal — /summarize
# ---------------------------------------------------------------------------

async def cmd_summarize(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/summarize <url|testo> [short] — riassume URL o testo."""
    args = context.args or []
    mode = "short" if args and args[-1].lower() == "short" else "detailed"
    if mode == "short":
        args = args[:-1]

    content = " ".join(args).strip()
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

    if file_id:
        source_type, content = "file", file_id
    elif content.startswith("http"):
        source_type = "url"
    else:
        source_type = "text"

    length = "brief" if mode == "short" else "normal"
    await update.message.reply_text("📄 Sto leggendo e riassumendo…")
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="summarize",
        input_data={"source_type": source_type, "content": content, "length": length, "save": True},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("reply")
            or (result.output_data or {}).get("error", "Errore summarize.")
        )
    except Exception as exc:
        logger.error("dispatch_task summarize fallito: %s", exc)
        reply = f"⚠️ Errore agente summarize: {exc}"
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# Personal — /research
# ---------------------------------------------------------------------------

async def cmd_research(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/research <query> [quick] — ricerca web strutturata."""
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

    await update.message.reply_text(f"🔍 Ricerco: «{query}»…")
    task = AgentTask(
        task_id=str(uuid.uuid4()),
        agent_name="research_personal",
        input_data={"query": query, "depth": mode},
        source="telegram",
    )
    try:
        result = await deps.pepe.dispatch_task(task)
        reply = (
            (result.output_data or {}).get("response")
            or (result.output_data or {}).get("error", "Errore research.")
        )
    except Exception as exc:
        logger.error("dispatch_task research_personal fallito: %s", exc)
        reply = f"⚠️ Errore agente research: {exc}"
    await reply_chunked(update.message, reply)


# ---------------------------------------------------------------------------
# Personal — /feedback / /urgency
# ---------------------------------------------------------------------------

async def cmd_feedback(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/feedback <positivo|negativo> <parola_chiave>"""
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
        await deps.pepe.memory.upsert_learning(
            agent="urgency",
            pattern_type="keyword",
            pattern_value=keyword,
            signal_type=signal,
            weight_delta=weight_delta,
        )
        icon = "✅" if signal == "positive" else "🔕"
        reply = (
            f"{icon} Capito. Quando vedo «{keyword}» lo tratterò come "
            f"{'prioritario' if signal == 'positive' else 'rumore'}."
        )
    except Exception as exc:
        reply = f"❌ Errore salvataggio feedback: {exc}"
    await update.message.reply_text(reply)


async def cmd_urgency(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/urgency add <keyword>"""
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
        await update.message.reply_text(
            "Specifica la keyword dopo `add`.", parse_mode="Markdown"
        )
        return

    try:
        await deps.pepe.memory.upsert_learning(
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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler Etsy + Personal nell'Application."""
    from functools import partial

    add = app.add_handler

    # Etsy / pipeline
    add(CommandHandler("listings",  partial(cmd_listings,    deps), filters=chat_filter))
    add(CommandHandler("niche",     partial(cmd_niche,       deps), filters=chat_filter))
    add(CommandHandler("design",    partial(cmd_design_etsy, deps), filters=chat_filter))
    add(CommandHandler("analytics", partial(cmd_analytics,   deps), filters=chat_filter))
    add(CommandHandler("ladder",    partial(cmd_ladder,      deps), filters=chat_filter))
    add(CommandHandler("finance",   partial(cmd_finance,     deps), filters=chat_filter))

    # Personal
    add(CommandHandler("remind",    partial(cmd_remind,      deps), filters=chat_filter))
    add(CommandHandler("reminders", partial(cmd_remind_list, deps), filters=chat_filter))
    add(CommandHandler("summarize", partial(cmd_summarize,   deps), filters=chat_filter))
    add(CommandHandler("research",  partial(cmd_research,    deps), filters=chat_filter))
    add(CommandHandler("feedback",  partial(cmd_feedback,    deps), filters=chat_filter))
    add(CommandHandler("urgency",   partial(cmd_urgency,     deps), filters=chat_filter))
