from agents.worker import _make_task
from agents.analyst.agent import AnalystAgent

run = _make_task("analyst", AnalystAgent)

__all__ = ["run"]
