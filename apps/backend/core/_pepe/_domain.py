"""Domain routing mixin for Pepe."""
from __future__ import annotations

import logging

from apps.backend.core.domains import DomainContext

logger = logging.getLogger("agentpexi.pepe")


class DomainMixin:

    def set_mock_mode(self, value: bool) -> None:
        """Attiva/disattiva mock mode a runtime. Thread-safe (GIL)."""
        self.mock_mode = value
        # Propaga a MemoryManager — letto da BaseAgent._call_llm e ResearchAgent
        self.memory.mock_mode = value
        logger.info("Mock mode: %s", "ON" if value else "OFF")

    def get_mock_mode(self) -> bool:
        return self.mock_mode

    def set_active_domain(self, domain: DomainContext | None) -> None:
        """Cambia dominio attivo a runtime. Sticky fino al riavvio o al prossimo switch.

        None → disattiva il business domain (solo personal layer attivo).
        DomainContext → attiva il dominio business specificato.
        """
        if domain is None:
            prev = self._business_domain.name if self._business_domain else "none"
            self._business_domain = None
            logger.info("Business domain disattivato (era: %s)", prev)
        else:
            prev = self._business_domain.name if self._business_domain else "none"
            self._business_domain = domain
            logger.info("Business domain: %s → %s", prev, domain.name)

    def get_active_domain(self) -> DomainContext | None:
        """Restituisce il dominio business attivo, o None se solo personal layer è attivo."""
        return self._business_domain
