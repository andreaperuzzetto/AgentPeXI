"""Handler Telegram — Configurazione sistema (Blocco 3 / step 3.4).

Comandi:
  /budget  [set <key> <value>]  — stato budget giornaliero + aggiornamento limiti
  /mock    [on|off]             — attiva/disattiva mock mode (spostato da system.py)
  /policy  [set <key> <value>]  — stato publication policy + aggiornamento chiavi
  /config  [<key> <value>]      — alias generico per set config (raw key=value)
  /ads     [on|off|budget <n>]  — gestione Etsy Ads (stub — implementazione in B5)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from apps.backend.telegram.dependencies import BotDependencies

logger = logging.getLogger("agentpexi.telegram.config")

# ---------------------------------------------------------------------------
# /budget
# ---------------------------------------------------------------------------

_BUDGET_KEYS = ("daily_llm_usd", "daily_image_usd", "daily_listing_fee_usd", "warn_threshold")


def _bar(pct: float, width: int = 10) -> str:
    """Barra ASCII proporzionale al percentuale (0.0–1.0+)."""
    filled = min(int(pct * width), width)
    return "█" * filled + "░" * (width - filled)


async def cmd_budget(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/budget [set <key> <value>] — mostra stato budget o aggiorna un limite."""
    bm = deps.budget_manager
    if bm is None:
        await update.message.reply_text("⚠️ BudgetManager non disponibile.")
        return

    args = context.args or []

    # /budget set <key> <value>
    if len(args) == 3 and args[0].lower() == "set":
        key, raw_val = args[1], args[2]
        if key not in _BUDGET_KEYS:
            await update.message.reply_text(
                f"❌ Chiave non valida. Usa una di:\n`{'`, `'.join(_BUDGET_KEYS)}`",
                parse_mode="Markdown",
            )
            return
        try:
            value = float(raw_val)
        except ValueError:
            await update.message.reply_text("❌ Valore non valido — usa un numero (es. `0.80`).", parse_mode="Markdown")
            return
        await bm.set_limit(key, value)
        await update.message.reply_text(f"✅ `budget.{key}` aggiornato a `{value}`.", parse_mode="Markdown")
        return

    # /budget — mostra snapshot
    try:
        s = await bm.get_status_summary()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Errore lettura budget: {exc}")
        return

    status_icon = {"OK": "🟢", "WARNING": "🟡", "EXCEEDED": "🔴"}.get(s.status.name, "❓")

    lines = [
        f"{status_icon} *Budget giornaliero* — {s.status.name}",
        "",
        f"🤖 LLM:     ${s.llm_today:.4f} / ${s.llm_limit:.2f}  {_bar(s.llm_pct)} {s.llm_pct*100:.0f}%",
        f"🖼 Immagini: ${s.image_today:.4f} / ${s.image_limit:.2f}  {_bar(s.image_pct)} {s.image_pct*100:.0f}%",
        f"🏷 Fee Etsy: ${s.fee_today:.4f} / ${s.fee_limit:.2f}  {_bar(s.fee_pct)} {s.fee_pct*100:.0f}%",
        "",
        f"📊 *Totale oggi*: ${s.total_today:.4f} / ${s.total_limit:.2f}",
        "",
        "Per aggiornare un limite:",
        "`/budget set daily_llm_usd 1.00`",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /mock
# ---------------------------------------------------------------------------

async def cmd_mock(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/mock [on|off] — attiva o disattiva mock mode Etsy."""
    args = context.args or []
    arg = args[0].lower() if args else ""

    if arg == "on":
        deps.pepe.set_mock_mode(True)
        if deps.pepe._ws_broadcast:
            await deps.pepe._ws_broadcast({
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
        deps.pepe.set_mock_mode(False)
        if deps.pepe._ws_broadcast:
            await deps.pepe._ws_broadcast({
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
        status = "🟡 ATTIVO" if deps.pepe.mock_mode else "⚫ INATTIVO"
        await update.message.reply_text(
            f"*Mock Mode*: {status}\n\n"
            "Uso: `/mock on` oppure `/mock off`",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# /policy
# ---------------------------------------------------------------------------

_POLICY_LABELS: dict[str, str] = {
    "policy.max_per_day":           "Max listing/giorno",
    "policy.min_gap_hours":         "Gap minimo tra publish (h)",
    "policy.niche_cooldown_days":   "Cooldown stessa nicchia (gg)",
    "policy.availability_start":    "Finestra start (HH:MM)",
    "policy.availability_end":      "Finestra end (HH:MM)",
    "policy.etsy_ads_on_publish":   "Ads on publish",
    "policy.etsy_ads_daily_budget": "Ads budget giornaliero (€)",
}


async def cmd_policy(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/policy [set <key> <value>] — mostra la publication policy o aggiorna una chiave."""
    pp = deps.publication_policy
    if pp is None:
        await update.message.reply_text("⚠️ PublicationPolicy non disponibile.")
        return

    args = context.args or []

    # /policy set <key> <value>
    if len(args) >= 3 and args[0].lower() == "set":
        key   = args[1]
        value = " ".join(args[2:])
        # Accetta sia "max_per_day" sia "policy.max_per_day"
        full_key = key if key.startswith("policy.") else f"policy.{key}"
        known = set(_POLICY_LABELS)
        if full_key not in known:
            await update.message.reply_text(
                f"❌ Chiave sconosciuta: `{key}`\n\n"
                f"Chiavi valide:\n" + "\n".join(f"`{k}`" for k in sorted(known)),
                parse_mode="Markdown",
            )
            return
        await pp.set_config(full_key, value)
        await update.message.reply_text(f"✅ `{full_key}` → `{value}`", parse_mode="Markdown")
        return

    # /policy — snapshot
    try:
        all_cfg = await pp.get_all()
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Errore lettura policy: {exc}")
        return

    lines = ["📋 *Publication Policy*", ""]
    for full_key, label in _POLICY_LABELS.items():
        val = all_cfg.get(full_key, "—")
        short_key = full_key.replace("policy.", "")
        lines.append(f"• `{short_key}` = `{val}`   _{label}_")

    lines += ["", "Per aggiornare:", "`/policy set max_per_day 3`"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /config  (raw key=value per power user)
# ---------------------------------------------------------------------------

async def cmd_config(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/config <key> <value> — imposta una chiave config raw (budget.* o policy.*)."""
    pp = deps.publication_policy
    bm = deps.budget_manager

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "*Uso*: `/config <key> <value>`\n\n"
            "Esempi:\n"
            "`/config policy.max_per_day 4`\n"
            "`/config budget.daily_llm_usd 0.75`",
            parse_mode="Markdown",
        )
        return

    key   = args[0]
    value = " ".join(args[1:])

    if key.startswith("policy."):
        if pp is None:
            await update.message.reply_text("⚠️ PublicationPolicy non disponibile.")
            return
        await pp.set_config(key, value)
        await update.message.reply_text(f"✅ `{key}` → `{value}`", parse_mode="Markdown")

    elif key.startswith("budget."):
        if bm is None:
            await update.message.reply_text("⚠️ BudgetManager non disponibile.")
            return
        short_key = key.replace("budget.", "")
        try:
            await bm.set_limit(short_key, float(value))
        except ValueError:
            await update.message.reply_text("❌ Valore non numerico per budget.")
            return
        await update.message.reply_text(f"✅ `{key}` → `{value}`", parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "❌ Namespace non riconosciuto.\n"
            "Usa `policy.*` o `budget.*`.",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# /ads  (stub — implementazione completa in Blocco 5)
# ---------------------------------------------------------------------------

async def cmd_ads(
    deps: "BotDependencies",
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """/ads [on|off|budget <n>] — gestione Etsy Ads (stub B5)."""
    pp = deps.publication_policy
    if pp is None:
        await update.message.reply_text("⚠️ PublicationPolicy non disponibile.")
        return

    args = context.args or []
    arg  = args[0].lower() if args else ""

    if arg == "on":
        await pp.set_config("policy.etsy_ads_on_publish", "true")
        await update.message.reply_text(
            "✅ Etsy Ads abilitati al publish.\n\n"
            "⚠️ *Stub B5* — la chiamata API reale sarà implementata nel Blocco 5.",
            parse_mode="Markdown",
        )

    elif arg == "off":
        await pp.set_config("policy.etsy_ads_on_publish", "false")
        await update.message.reply_text("⚫ Etsy Ads disabilitati.")

    elif arg == "budget" and len(args) >= 2:
        try:
            daily = float(args[1])
        except ValueError:
            await update.message.reply_text("❌ Valore non valido: `/ads budget 1.50`", parse_mode="Markdown")
            return
        await pp.set_config("policy.etsy_ads_daily_budget", str(daily))
        await update.message.reply_text(f"✅ Ads daily budget → €{daily:.2f}/giorno.", parse_mode="Markdown")

    else:
        # Mostra stato attuale
        try:
            enabled = await pp.ads_enabled()
            budget  = await pp.ads_daily_budget()
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Errore lettura ads config: {exc}")
            return

        icon = "✅ ATTIVI" if enabled else "⚫ INATTIVI"
        await update.message.reply_text(
            f"📢 *Etsy Ads*: {icon}\n"
            f"💰 Budget giornaliero: €{budget:.2f}\n\n"
            "Comandi:\n"
            "`/ads on` — abilita\n"
            "`/ads off` — disabilita\n"
            "`/ads budget 2.00` — aggiorna budget\n\n"
            "⚠️ _Stub B5 — integrazione API reale in arrivo._",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    app: Application,
    deps: "BotDependencies",
    chat_filter,
) -> None:
    """Registra tutti gli handler config nell'Application."""
    from functools import partial

    add = app.add_handler
    add(CommandHandler("budget", partial(cmd_budget, deps), filters=chat_filter))
    add(CommandHandler("mock",   partial(cmd_mock,   deps), filters=chat_filter))
    add(CommandHandler("policy", partial(cmd_policy, deps), filters=chat_filter))
    add(CommandHandler("config", partial(cmd_config, deps), filters=chat_filter))
    add(CommandHandler("ads",    partial(cmd_ads,    deps), filters=chat_filter))
