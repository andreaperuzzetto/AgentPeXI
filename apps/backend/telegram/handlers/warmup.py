"""Telegram handler: /warmup command and warmup approval callbacks.

Commands:
  /warmup                  — trigger full warmup run (4 sections, Sonnet synthesis)
  /warmup-detail <niche>   — show full details for a single warmup candidate

Callbacks (InlineKeyboard):
  approve_warmup_batch     — approve all Sonnet-recommended niches
  approve_warmup:<doc_id>  — approve a single warmup candidate
  reject_warmup:<doc_id>   — reject a single warmup candidate
"""
from __future__ import annotations

import hashlib
import logging
from functools import partial

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.warmup")


# ---------------------------------------------------------------------------
# /warmup — full run
# ---------------------------------------------------------------------------

async def cmd_warmup(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: BotDependencies,
) -> None:
    """Trigger the full WarmupOrchestrator run and send the approval report."""
    chat_id: int = update.effective_chat.id

    if deps.research_agent is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Research agent non disponibile.")
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔍 *Warmup avviato* — analisi di 4 sezioni in corso\\.\n"
            "_Ci vorranno circa 20 minuti\\. Riceverai il report a fine analisi\\._"
        ),
        parse_mode="MarkdownV2",
    )

    try:
        result = await deps.research_agent.run_full_warmup()
    except Exception as exc:
        logger.exception("cmd_warmup: run_full_warmup failed")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Errore durante il warmup: {exc}",
        )
        return

    report = result.get("report", {})
    recommended = report.get("recommended", [])
    report_text = report.get("report_text", "Warmup completato.")
    total = result.get("total", 0)

    sections_summary = ""
    for section, candidates in result.get("all_candidates", {}).items():
        sections_summary += f"\n• *{_fmt_section(section)}*: {len(candidates)} candidati"

    message = (
        f"✅ *Warmup completato* — {total} candidati trovati\n"
        f"{sections_summary}\n\n"
        f"📊 *Sonnet raccomanda {len(recommended)} niche:*\n"
    )
    for i, c in enumerate(recommended, 1):
        niche = c.get("niche", "N/A")
        score = float(c.get("score") or 0.0)
        section = c.get("section", "N/A")
        rationale = c.get("rationale", "")[:60]
        message += (
            f"\n{i}\\. *{_esc(niche)}*\n"
            f"   Score: `{score:.2f}` • {_esc(section)}\n"
            f"   _{_esc(rationale)}_\n"
        )

    buttons: list[list[InlineKeyboardButton]] = []
    if recommended:
        buttons.append([
            InlineKeyboardButton(
                f"✅ Approva batch ({len(recommended)} niche)",
                callback_data="approve_warmup_batch",
            )
        ])
    for c in recommended:
        niche = c.get("niche", "N/A")
        product_type = c.get("product_type", "")
        _hash = hashlib.md5(f"{niche}:{product_type}".encode()).hexdigest()[:16]
        doc_id = c.get("doc_id") or _hash
        buttons.append([
            InlineKeyboardButton(f"✅ {niche[:30]}", callback_data=f"approve_warmup:{doc_id}"),
            InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject_warmup:{doc_id}"),
        ])

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Approval callbacks
# ---------------------------------------------------------------------------

async def cb_approve_warmup_batch(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: BotDependencies,
) -> None:
    """Approve all Sonnet-recommended niches: add to production_queue as pending_design."""
    query = update.callback_query
    await query.answer()
    chat_id: int = update.effective_chat.id

    if deps.research_agent is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Research agent non disponibile.")
        return
    if deps.production_queue is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Production queue non disponibile.")
        return

    try:
        warmup_docs = await deps.research_agent.memory.query_insights_by_type(
            "warmup_candidate", limit=100
        )
        pending = [d for d in warmup_docs if d.get("metadata", {}).get("status") == "pending"]
        pending.sort(key=_safe_score, reverse=True)
        
        # Check existing queue items to prevent duplicates
        existing_items = await deps.production_queue.get_items_by_status("pending_design")
        existing_pairs = {(item.niche.lower(), item.product_type.lower()) for item in existing_items}
        
        approved_count = 0
        for doc in pending[:8]:
            meta = doc.get("metadata", {})
            niche = (meta.get("niche") or "").strip()
            product_type = meta.get("product_type") or "printable_pdf"
            if not niche:
                continue
            
            # Skip if already queued
            if (niche.lower(), product_type.lower()) in existing_pairs:
                continue
            
            try:
                score = float(meta.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            await deps.production_queue.create_item(
                niche=niche,
                product_type=product_type,
                keywords=[],
                entry_score=score,
            )
            approved_count += 1
            existing_pairs.add((niche.lower(), product_type.lower()))

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {approved_count} niche aggiunte alla production queue come `pending_design`.",
        )
    except Exception as exc:
        logger.exception("cb_approve_warmup_batch failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Errore approvazione batch: {exc}")


async def cb_approve_warmup_niche(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: BotDependencies,
) -> None:
    """Approve a single warmup candidate: add to production_queue as pending_design."""
    query = update.callback_query
    await query.answer()
    chat_id: int = update.effective_chat.id
    doc_id_or_niche = query.data.replace("approve_warmup:", "").strip()

    if deps.research_agent is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Research agent non disponibile.")
        return
    if deps.production_queue is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Production queue non disponibile.")
        return

    try:
        warmup_docs = await deps.research_agent.memory.query_insights_by_type(
            "warmup_candidate", limit=100
        )
        target = next(
            (
                d for d in warmup_docs
                if d.get("id") == doc_id_or_niche
                or _niche_hash(
                    d.get("metadata", {}).get("niche", ""),
                    d.get("metadata", {}).get("product_type", ""),
                ) == doc_id_or_niche
                or d.get("metadata", {}).get("niche", "").replace(" ", "_")[:20] == doc_id_or_niche
            ),
            None,
        )
        if not target:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Candidato non trovato.")
            return

        meta = target.get("metadata", {})
        niche = (meta.get("niche") or "").strip()
        if not niche:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Niche vuota, impossibile approvare.")
            return
        product_type = meta.get("product_type") or "printable_pdf"
        try:
            score = float(meta.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0

        # Check if already queued to prevent duplicates
        existing_items = await deps.production_queue.get_items_by_status("pending_design")
        existing_pairs = {(item.niche.lower(), item.product_type.lower()) for item in existing_items}
        
        if (niche.lower(), product_type.lower()) in existing_pairs:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ *{_esc(niche)}* è già in coda\\.",
                parse_mode="MarkdownV2",
            )
            return

        await deps.production_queue.create_item(
            niche=niche,
            product_type=product_type,
            keywords=[],
            entry_score=score,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ *{_esc(niche)}* aggiunta alla production queue\\.",
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.exception("cb_approve_warmup_niche failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Errore: {exc}")


async def cb_reject_warmup_niche(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: BotDependencies,
) -> None:
    """Reject a warmup candidate (re-store with status=rejected in ChromaDB)."""
    query = update.callback_query
    await query.answer()
    chat_id: int = update.effective_chat.id
    doc_id_or_niche = query.data.replace("reject_warmup:", "").strip()

    if deps.research_agent is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Research agent non disponibile.")
        return

    try:
        warmup_docs = await deps.research_agent.memory.query_insights_by_type(
            "warmup_candidate", limit=100
        )
        target = next(
            (
                d for d in warmup_docs
                if d.get("id") == doc_id_or_niche
                or _niche_hash(
                    d.get("metadata", {}).get("niche", ""),
                    d.get("metadata", {}).get("product_type", ""),
                ) == doc_id_or_niche
                or d.get("metadata", {}).get("niche", "").replace(" ", "_")[:20] == doc_id_or_niche
            ),
            None,
        )
        niche_name = (target or {}).get("metadata", {}).get("niche", doc_id_or_niche)

        if target:
            meta = dict(target.get("metadata", {}))
            meta["status"] = "rejected"
            doc_id = target.get("id")
            if doc_id:
                await deps.research_agent.memory.update_insight_metadata(doc_id, meta)
            else:
                # Fallback: if no id, store_insight as before (best-effort)
                await deps.research_agent.memory.store_insight(
                    text=target.get("document", f"warmup candidate: {niche_name}"),
                    metadata=meta,
                )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Niche *{_esc(niche_name)}* marcata come rifiutata\\.",
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.exception("cb_reject_warmup_niche failed: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Errore: {exc}")


# ---------------------------------------------------------------------------
# /warmup-detail
# ---------------------------------------------------------------------------

async def cmd_warmup_detail(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: BotDependencies,
) -> None:
    """/warmup-detail <niche> — show full details for a warmup candidate."""
    chat_id: int = update.effective_chat.id
    args = context.args or []
    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Uso: /warmup_detail <niche>")
        return

    if deps.research_agent is None:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Research agent non disponibile.")
        return

    query_term = " ".join(args).strip().lower()
    try:
        docs = await deps.research_agent.memory.query_insights_by_type("warmup_candidate", limit=100)
        match = next(
            (d for d in docs if query_term in d.get("metadata", {}).get("niche", "").lower()),
            None,
        )
        if not match:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Nessun candidato trovato per: {query_term}")
            return

        meta = match.get("metadata", {})
        text = (
            f"📊 *Warmup candidate*\n\n"
            f"*Niche:* {_esc(meta.get('niche', 'N/A'))}\n"
            f"*Tipo:* {_esc(meta.get('product_type', 'N/A'))}\n"
            f"*Score:* `{meta.get('score', 'N/A')}`\n"
            f"*Sezione:* {_esc(meta.get('section', 'N/A'))}\n"
            f"*Status:* {_esc(meta.get('status', 'N/A'))}\n"
            f"*Source:* {_esc(meta.get('source', 'N/A'))}\n"
        )
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
    except Exception as exc:
        logger.exception("cmd_warmup_detail failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Errore: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_score(d: dict) -> float:
    """Return float score from a ChromaDB doc dict, defaulting to 0.0 on any error."""
    try:
        return float(d.get("metadata", {}).get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _niche_hash(niche: str, product_type: str) -> str:
    """Generate a stable hash for niche + product_type."""
    return hashlib.md5(f"{niche}:{product_type}".encode()).hexdigest()[:16]


def _fmt_section(key: str) -> str:
    return key.replace("_", " ").title()


def _esc(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(app: Application, deps: BotDependencies, chat_filter) -> None:
    """Register warmup command and callback handlers."""
    add = app.add_handler

    add(CommandHandler(
        "warmup", partial(cmd_warmup, deps=deps), filters=chat_filter,
    ))
    add(CommandHandler(
        "warmup_detail", partial(cmd_warmup_detail, deps=deps), filters=chat_filter,
    ))
    add(CallbackQueryHandler(
        partial(cb_approve_warmup_batch, deps=deps),
        pattern=r"^approve_warmup_batch$",
    ))
    add(CallbackQueryHandler(
        partial(cb_approve_warmup_niche, deps=deps),
        pattern=r"^approve_warmup:.+$",
    ))
    add(CallbackQueryHandler(
        partial(cb_reject_warmup_niche, deps=deps),
        pattern=r"^reject_warmup:.+$",
    ))
