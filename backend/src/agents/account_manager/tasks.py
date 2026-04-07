from agents.worker import _make_task
from agents.account_manager.agent import AccountManagerAgent

run = _make_task("account_manager", AccountManagerAgent)

__all__ = ["run"]
