from agents.worker import _make_task
from agents.scout.agent import ScoutAgent

run = _make_task("scout", ScoutAgent)

__all__ = ["run"]
