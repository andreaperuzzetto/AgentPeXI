"""_DeliveryMixin — delivery di pin Pinterest via Tailwind o API diretta.

Routing runtime:
  PINTEREST_DELIVERY_METHOD=tailwind  (default, Piano A — Tailwind va-live)
  PINTEREST_DELIVERY_METHOD=direct    (Pinterest v5 API, attivabile post-approvazione)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.pinterest.delivery")


class _DeliveryMixin:
    """Mixin per la consegna di pin Pinterest: Tailwind (default) o API diretta."""

    async def deliver_pin(self, pin: dict) -> str:
        """Consegna un pin in base a PINTEREST_DELIVERY_METHOD.

        Returns:
            "tailwind_queued" se delivery via Tailwind, altrimenti il pinterest_pin_id.
        """
        method = os.getenv("PINTEREST_DELIVERY_METHOD", "tailwind")
        if method == "direct":
            return await self._deliver_via_direct(pin)
        return await self._deliver_via_tailwind(pin)

    async def _deliver_via_tailwind(self, pin: dict) -> str:
        """Scrive il pin come JSON in {STORAGE_PATH}/tailwind_queue/YYYY-MM-DD/pin_{id}.json.

        Il file viene importato manualmente in Tailwind (Piano A go-live).
        Payload: title, description, image_path, board_name, scheduled_at.
        """
        scheduled_raw = pin.get("scheduled_at", "")
        try:
            if isinstance(scheduled_raw, str):
                dt = datetime.fromisoformat(scheduled_raw.replace("Z", "+00:00"))
            else:
                dt = datetime.now(timezone.utc)
        except ValueError:
            dt = datetime.now(timezone.utc)

        date_str = dt.strftime("%Y-%m-%d")
        pin_id = pin.get("id", 0)

        out_dir = Path(settings.STORAGE_PATH) / "tailwind_queue" / date_str
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "title": pin.get("title", ""),
            "description": pin.get("description", ""),
            "image_path": pin.get("image_path", ""),
            "board_name": pin.get("board_name", ""),
            "scheduled_at": scheduled_raw,
        }

        out_path = out_dir / f"pin_{pin_id}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

        logger.info("Tailwind queue → %s", out_path)
        return "tailwind_queued"

    async def _deliver_via_direct(self, pin: dict) -> str:
        """Pubblica il pin via Pinterest v5 API (solo quando PINTEREST_DELIVERY_METHOD=direct).

        Non viene chiamato in produzione finché l'API non è approvata.
        """
        response = await self.pinterest_api.create_pin(
            board_id=pin.get("board_id", ""),
            title=pin.get("title", ""),
            description=pin.get("description", ""),
            image_url=pin.get("image_path", ""),
            link="",
        )
        return response.get("id", "")
