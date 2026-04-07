from agents.worker import _make_task
from agents.lead_profiler.agent import LeadProfilerAgent

run = _make_task("lead_profiler", LeadProfilerAgent)

__all__ = ["run"]
