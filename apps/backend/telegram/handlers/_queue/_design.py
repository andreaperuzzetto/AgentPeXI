"""Design Etsy handler."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from apps.backend.core.models import AgentTask
from apps.backend.telegram.handlers._queue._listings import _pick_art_type, _pick_template

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.queue")


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
