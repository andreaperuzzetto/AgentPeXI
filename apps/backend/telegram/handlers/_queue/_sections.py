"""Telegram handler — /sections subcommands (A.1)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("agentpexi.telegram")

# Sentinel per sopprimere circolare al momento dell'import
_DEPS_TYPE = "BotDependencies"


async def cmd_sections(deps: _DEPS_TYPE, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler per /sections e i suoi subcommand.

    Subcommand:
      /sections                          — lista sezioni con contatori
      /sections uncategorized            — coda niche non mappate con suggestion
      /sections map <niche_key> <section_name>  — mappa manualmente
      /sections add <section_name>       — crea nuova sezione su Etsy
      /sections flag <section_name>      — marca per revisione
    """
    if update.message is None:
        return

    args = context.args or []
    sub = args[0].lower() if args else ""

    if sub == "":
        await _sections_list(deps, update)
    elif sub == "uncategorized":
        await _sections_uncategorized(deps, update)
    elif sub == "map" and len(args) >= 3:
        niche_key = args[1]
        section_name = " ".join(args[2:])
        await _sections_map(deps, update, niche_key, section_name)
    elif sub == "add" and len(args) >= 2:
        section_name = " ".join(args[1:])
        await _sections_add(deps, update, section_name)
    elif sub == "flag" and len(args) >= 2:
        section_name = " ".join(args[1:])
        await _sections_flag(deps, update, section_name)
    else:
        await update.message.reply_text(
            "📂 *Comandi sezioni:*\n"
            "`/sections` — lista sezioni\n"
            "`/sections uncategorized` — niche non mappate\n"
            "`/sections map <niche\\_key> <section name>` — mappa manuale\n"
            "`/sections add <name>` — crea sezione Etsy\n"
            "`/sections flag <name>` — marca per revisione",
            parse_mode="Markdown",
        )


async def _sections_list(deps: _DEPS_TYPE, update: Update) -> None:
    """Lista sezioni con listing_count, last_listing_at, pending badge."""
    from apps.backend.core.etsy_sections_service import EtsySectionsService

    try:
        db = await deps.memory.get_db()
        ess = EtsySectionsService(db)
        sections = await ess.get_sections_with_uncategorized_counts()
    except Exception:
        logger.exception("cmd_sections list error")
        await update.message.reply_text("❌ Errore nel recupero sezioni.")
        return

    if not sections:
        await update.message.reply_text("📂 Nessuna sezione Etsy sincronizzata.")
        return

    pending = sections[0].get("pending_uncategorized", 0) if sections else 0
    lines = [f"📂 *Sezioni Etsy* ({len(sections)} totali):"]
    for s in sections:
        last = s.get("last_listing_at", "—") or "mai"
        badge = " ⚠️" if pending > 0 else ""
        lines.append(
            f"• *{s['section_name']}*{badge} — {s['listing_count']} listing | ultimo: {last}"
        )
    if pending > 0:
        lines.append(f"\n⚠️ {pending} niche in attesa di categorizzazione")
        lines.append("→ `/sections uncategorized` per gestirle")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _sections_uncategorized(deps: _DEPS_TYPE, update: Update) -> None:
    """Lista niche non mappate con suggestion di sezione."""
    try:
        db = await deps.memory.get_db()
        cursor = await db.execute(
            """
            SELECT niche_key, detected_at, suggested_section_id, suggested_confidence
            FROM uncategorized_niches
            WHERE status = 'pending'
            ORDER BY detected_at DESC
            LIMIT 20
            """
        )
        rows = await cursor.fetchall()
    except Exception:
        logger.exception("cmd_sections uncategorized error")
        await update.message.reply_text("❌ Errore nel recupero niche non mappate.")
        return

    if not rows:
        await update.message.reply_text("✅ Nessuna niche in attesa di categorizzazione.")
        return

    lines = [f"📋 *Niche non mappate* ({len(rows)} pending):"]
    for r in rows:
        suggestion = ""
        if r["suggested_section_id"] and r["suggested_confidence"]:
            conf_pct = int((r["suggested_confidence"] or 0) * 100)
            suggestion = f" → suggerita: {r['suggested_section_id']} ({conf_pct}%)"
        lines.append(f"• `{r['niche_key']}`{suggestion}")
    lines.append(
        "\n→ `/sections map <niche_key> <section name>` per mappare"
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _sections_map(deps: _DEPS_TYPE, update: Update, niche_key: str, section_name: str) -> None:
    """Mappa manualmente una niche a una sezione cercata per nome."""
    from apps.backend.core.etsy_sections_service import EtsySectionsService

    try:
        db = await deps.memory.get_db()
        ess = EtsySectionsService(db)

        # Cerca section_id per nome (case-insensitive)
        cursor = await db.execute(
            "SELECT section_id, section_name FROM etsy_sections WHERE LOWER(section_name) LIKE ? AND is_active = 1",
            (f"%{section_name.lower()}%",),
        )
        row = await cursor.fetchone()
        if not row:
            await update.message.reply_text(
                f"❌ Sezione '{section_name}' non trovata.\n"
                "→ `/sections` per vedere le sezioni disponibili."
            )
            return

        await ess.map_niche(niche_key, str(row["section_id"]), mapped_by="human")

        # Rimuovi da uncategorized
        await db.execute(
            "UPDATE uncategorized_niches SET status = 'mapped' WHERE niche_key = ? AND status = 'pending'",
            (niche_key,),
        )
        await db.commit()

        # Invia WS event per aggiornare il frontend (SectionsPanel + NicheTable)
        if deps.ws_broadcaster:
            try:
                await deps.ws_broadcaster({
                    "type": "section_mapped",
                    "niche_key": niche_key,
                    "section_id": str(row["section_id"]),
                    "section_name": row["section_name"],
                })
            except Exception:
                logger.warning("cmd_sections map: ws_broadcaster fallito, mapping già salvato")

        await update.message.reply_text(
            f"✅ `{niche_key}` mappata → *{row['section_name']}*",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("cmd_sections map error")
        await update.message.reply_text("❌ Errore durante la mappatura.")


async def _sections_add(deps: _DEPS_TYPE, update: Update, section_name: str) -> None:
    """Crea una nuova sezione su Etsy e la sincronizza in DB."""
    if not deps.etsy_api:
        await update.message.reply_text("❌ Etsy API non inizializzata.")
        return

    from apps.backend.core.etsy_sections_service import EtsySectionsService

    try:
        new_section = await deps.etsy_api.create_shop_section(title=section_name)
        db = await deps.memory.get_db()
        ess = EtsySectionsService(db)
        await ess.sync_sections([{
            "shop_section_id": new_section["shop_section_id"],
            "title": new_section.get("title", section_name),
            "active_listing_count": 0,
        }])
        await update.message.reply_text(
            f"✅ Sezione creata: *{new_section.get('title', section_name)}* "
            f"(ID: {new_section['shop_section_id']})",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("cmd_sections add error")
        await update.message.reply_text("❌ Errore nella creazione della sezione.")


async def _sections_flag(deps: _DEPS_TYPE, update: Update, section_name: str) -> None:
    """Marca una sezione per revisione (is_active = 0)."""
    try:
        db = await deps.memory.get_db()
        cursor = await db.execute(
            "SELECT section_id, section_name FROM etsy_sections WHERE LOWER(section_name) LIKE ? AND is_active = 1",
            (f"%{section_name.lower()}%",),
        )
        rows = await cursor.fetchall()
        if not rows:
            await update.message.reply_text(
                f"❌ Nessuna sezione attiva trovata per '{section_name}'.\n"
                "→ `/sections` per vedere le sezioni disponibili."
            )
            return
        ids = [r["section_id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE etsy_sections SET is_active = 0 WHERE section_id IN ({placeholders})",
            ids,
        )
        await db.commit()
        if len(rows) == 1:
            await update.message.reply_text(
                f"🚩 Sezione '*{rows[0]['section_name']}*' marcata per revisione (is\\_active=0).",
                parse_mode="Markdown",
            )
        else:
            names = ", ".join(f"*{r['section_name']}*" for r in rows)
            await update.message.reply_text(
                f"🚩 {len(rows)} sezioni marcate per revisione: {names}",
                parse_mode="Markdown",
            )
    except Exception:
        logger.exception("cmd_sections flag error")
        await update.message.reply_text("❌ Errore nel flagging della sezione.")
