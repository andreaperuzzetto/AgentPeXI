"""Bundle handler."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from apps.backend.telegram.formatters import md_escape

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


def _fmt_bundle_spec(spec: dict) -> str:
    """Formatta una bundle spec per Telegram."""
    titles = spec.get("component_titles", [])
    kws    = spec.get("keywords", [])
    price  = spec.get("suggested_price", 0.0)
    n      = spec.get("n_components", len(titles))
    score  = spec.get("entry_score", 0.0)

    components_str = "\n".join(f"  {i+1}. {md_escape(t)}" for i, t in enumerate(titles)) or "  (nessuno)"
    kw_str         = md_escape(", ".join(kws[:8])) + (" …" if len(kws) > 8 else "")

    return (
        f"📦 *Bundle pronto — {md_escape(spec['niche'])}*\n\n"
        f"🗂 Componenti ({n}):\n{components_str}\n\n"
        f"💰 Prezzo suggerito: €{price:.2f} _(70% somma prezzi)_\n"
        f"📊 Score niche: {score:.3f}\n"
        f"🔑 Keywords: {kw_str}"
    )


async def cmd_bundle(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/bundle [niche] — verifica/genera spec bundle per una niche.

    Senza argomenti: scansiona tutte le niches e mostra quelle bundle-ready.
    Con argomento: mostra spec bundle per la niche specificata
                   (anche se non soddisfa il trigger — utile per preview).
    """
    if deps.bundle_strategy is None:
        await update.message.reply_text("❌ BundleStrategy non configurata.")
        return

    bs    = deps.bundle_strategy
    niche = " ".join(context.args).strip() if context.args else ""

    if niche:
        # Spec per una niche specifica
        ready = await bs.should_create_bundle(niche)
        spec  = await bs.generate_bundle_spec(niche)

        if spec["n_components"] == 0:
            await update.message.reply_text(
                f"❌ Nessun listing pubblicato nell'ultimo mese per la niche *{md_escape(niche)}*.",
                parse_mode="Markdown",
            )
            return

        status_line = "✅ Condizioni trigger soddisfatte." if ready else "⚠️ Trigger non soddisfatto (score basso o bundle già attivo)."
        msg = _fmt_bundle_spec(spec) + f"\n\n_{status_line}_"
        await update.message.reply_text(msg, parse_mode="Markdown")

    else:
        # Scan tutte le niches
        await update.message.reply_text("🔍 Scansiono niches bundle-ready…")
        try:
            candidates = await bs.check_all_niches()
        except Exception as exc:
            logger.error("check_all_niches fallito: %s", exc)
            await update.message.reply_text(f"❌ Errore: {exc}")
            return

        if not candidates:
            await update.message.reply_text(
                "Nessuna niche bundle-ready al momento.\n"
                "Servono ≥3 listing pubblicati nell'ultimo mese con score > 0.6."
            )
            return

        for c in candidates:
            msg = _fmt_bundle_spec(c["spec"])
            await update.message.reply_text(msg, parse_mode="Markdown")
