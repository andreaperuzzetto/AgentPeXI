from agents.worker import _make_task
from agents.billing.agent import BillingAgent

run = _make_task("billing", BillingAgent)

__all__ = ["run"]
