from agents.worker import _make_task
from agents.delivery_tracker.agent import DeliveryTrackerAgent

run = _make_task("delivery_tracker", DeliveryTrackerAgent)

__all__ = ["run"]
