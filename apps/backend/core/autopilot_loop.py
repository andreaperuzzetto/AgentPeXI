"""AutopilotLoop — orchestratore asyncio del pipeline design→approval→publish.

Ciclo principale:
  1. Discard approvazioni stale (solo al primo giro)
  2. Controlla stato loop (paused_* → sleep)
  3. Controlla finestra disponibilità (no cambio stato)
  4. Controlla budget
  5. Controlla quota giornaliera
  6. Queue depth check (TARGET = 2)
  7. Bundle check / pick next niche
  8. Avvia pipeline design
  9. Invia approval notification
 10. Attendi risposta (hybrid wait 24h)
 11. Gestisci decisione

Lo stato del loop è persistito in `autopilot_state` e sopravvive ai restart.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiosqlite

from apps.backend.core.budget_manager import BudgetManager
from apps.backend.core.production_queue import ProductionQueueService
from apps.backend.core.publication_policy import PublicationPolicy
from apps.backend.core._autopilot import (
    _CommandsMixin,
    _LoopMixin,
    _DecisionMixin,
    _ApprovalMixin,
    _StateMixin,
)


# ---------------------------------------------------------------------------
# AutopilotLoop — thin assembler
# ---------------------------------------------------------------------------

class AutopilotLoop(_CommandsMixin, _LoopMixin, _DecisionMixin, _ApprovalMixin, _StateMixin, object):
    """Orchestratore del pipeline Etsy.

    Dipendenze iniettate nel costruttore:
      - db                   : aiosqlite.Connection (da memory_manager.get_db())
      - queue                : ProductionQueueService
      - budget               : BudgetManager
      - policy               : PublicationPolicy
      - bot_send             : async callable(text: str)
      - bot_send_photo       : async callable(path: str, caption: str)
      - bot_send_media_group : async callable(paths: list, caption: str)
      - design_pipeline      : async callable(item_id: int, niche_data: dict)
      - niche_picker         : async callable() -> dict | None
      - bundle_checker       : async callable() -> dict | None

    L'injection esplicita rende il loop testabile senza Telegram né DB reale.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        queue: ProductionQueueService,
        budget: BudgetManager,
        policy: PublicationPolicy,
        bot_send,
        bot_send_photo=None,
        bot_send_media_group=None,
        bot_send_markup=None,
        design_pipeline=None,
        niche_picker=None,
        bundle_checker=None,
    ) -> None:
        self._db    = db
        self.queue  = queue
        self.budget = budget
        self.policy = policy

        self._bot_send             = bot_send
        self._bot_send_photo       = bot_send_photo       or self._noop_photo
        self._bot_send_media_group = bot_send_media_group or self._noop_media
        # bot_send_markup(text, reply_markup) — per notifiche con inline keyboard
        self._bot_send_markup      = bot_send_markup
        self._design_pipeline      = design_pipeline      or self._noop_design
        self._niche_picker         = niche_picker         or self._default_niche_picker
        self._bundle_checker       = bundle_checker       or self._default_bundle_checker

        self._running         = False
        self._first_iteration = True
        self._loop_task: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()

        # item_id → asyncio.Event  (segnale dal CallbackQueryHandler Telegram)
        self._approval_events:  dict[int, asyncio.Event] = {}
        # item_id → str  ("approved" | "skipped_user" | ...)
        self._approval_results: dict[int, str]           = {}
