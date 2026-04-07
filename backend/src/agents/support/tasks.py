from agents.worker import _make_task
from agents.support.agent import SupportAgent

run = _make_task("support", SupportAgent)

__all__ = ["run"]
