from agents.worker import _make_task
from agents.sales.agent import SalesAgent

run = _make_task("sales", SalesAgent)

__all__ = ["run"]
