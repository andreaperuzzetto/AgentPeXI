"""Listing & niche research handlers."""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from apps.backend.core.models import AgentTask
from apps.backend.telegram.formatters import md_escape, reply_chunked

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


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
