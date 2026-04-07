from agents.worker import _make_task
from agents.delivery_orchestrator.agent import DeliveryOrchestratorAgent

run = _make_task("delivery_orchestrator", DeliveryOrchestratorAgent)

__all__ = ["run"]
