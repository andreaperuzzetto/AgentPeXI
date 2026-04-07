from agents.worker import _make_task
from agents.proposal.agent import ProposalAgent

run = _make_task("proposal", ProposalAgent)

__all__ = ["run"]
