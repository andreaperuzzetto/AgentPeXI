"""Analytics and Ladder System handlers."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from apps.backend.core.models import AgentTask

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")

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
