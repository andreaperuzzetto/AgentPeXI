from __future__ import annotations

from abc import ABC, abstractmethod

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agents._sse import _publish_sse
from agents.models import AgentResult, AgentTask, AgentToolError, GateNotApprovedError
from db.session import get_db_session
from tools.db_tools import (
    _mark_task_blocked,
    _mark_task_completed,
    _mark_task_failed,
    _mark_task_running,
)


class BaseAgent(ABC):
    """
    Classe base per tutti gli agenti AgentPeXI.

    Ogni agente concreto deve implementare `execute()`.
    Non fare override di `run()` — gestisce logging, DB session e gestione errori.
    """

    agent_name: str

    def __init__(self) -> None:
        self.log = structlog.get_logger().bind(agent=self.agent_name)

    async def run(self, task: AgentTask) -> AgentResult:
        """Entry point chiamato dal Celery worker. Non fare override."""
        run_id: str = task.payload.get("run_id", "")
        self.log.info("task.started", task_id=str(task.id), task_type=task.type)

        async with get_db_session() as db:
            await _mark_task_running(task.id, db)
            await _publish_sse(run_id, "task_started", self.agent_name, {"task_type": task.type})

            try:
                result = await self.execute(task, db)
            except GateNotApprovedError as e:
                self.log.warning("task.gate_blocked", task_id=str(task.id), reason=str(e))
                await _mark_task_blocked(task.id, str(e), db)
                await _publish_sse(run_id, "task_blocked", self.agent_name, {"reason": str(e)})
                return AgentResult(
                    task_id=task.id,
                    success=False,
                    output={},
                    error=str(e),
                    requires_human_gate=True,
                )
            except AgentToolError as e:
                self.log.error("task.tool_error", task_id=str(task.id), error_code=e.code)
                await _mark_task_failed(task.id, e.code, db)
                await _publish_sse(
                    run_id, "task_failed", self.agent_name, {"error_code": e.code}
                )
                return AgentResult(task_id=task.id, success=False, output={}, error=e.code)
            except Exception as e:
                self.log.error("task.unexpected_error", task_id=str(task.id), error=str(e))
                await _mark_task_failed(task.id, str(e), db)
                raise

            if result.success:
                await _mark_task_completed(task.id, result.output, db)
                await _publish_sse(
                    run_id,
                    "task_completed",
                    self.agent_name,
                    {"output_keys": list(result.output.keys())},
                )
                self.log.info(
                    "task.completed",
                    task_id=str(task.id),
                    output_keys=list(result.output.keys()),
                )
            else:
                self.log.warning("task.failed", task_id=str(task.id), error=result.error)

        return result

    @abstractmethod
    async def execute(self, task: AgentTask, db: AsyncSession) -> AgentResult:
        """
        Logica specifica dell'agente. Implementare in ogni sottoclasse.

        Regole:
        - Verificare gate flags leggendo SEMPRE da db (mai da task.payload)
        - Non loggare PII — solo ID dei record
        - Blocco logico: restituire AgentResult(success=False, ...) non sollevare eccezioni
        - Errore tool: sollevare AgentToolError
        - Idempotenza: verificare idempotency_key prima di ogni scrittura esterna
        """
        ...
