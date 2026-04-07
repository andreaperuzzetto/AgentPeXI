from agents.worker import _make_task
from agents.design.agent import DesignAgent

run = _make_task("design", DesignAgent)

__all__ = ["run"]
