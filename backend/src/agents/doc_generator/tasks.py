from agents.worker import _make_task
from agents.doc_generator.agent import DocGeneratorAgent

run = _make_task("doc_generator", DocGeneratorAgent)

__all__ = ["run"]
