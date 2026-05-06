"""Analytics mixin for MemoryManager."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from apps.backend.core._memory._base import _json_loads
from apps.backend.core.config import settings

logger = logging.getLogger("agentpexi.memory")


class AnalyticsMixin:

    async def get_cost_breakdown(self, period_days: int = 30) -> dict:
        """Cost breakdown per agente, per tool, per giorno, e totale."""
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

        # Per agente (da agent_logs)
        cursor = await self._db.execute(
            """SELECT agent_name, SUM(total_cost_usd) as cost
               FROM agent_logs WHERE updated_at >= ? AND status = 'completed'
               GROUP BY agent_name""",
            (since,),
        )
        per_agent = {row["agent_name"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Per tool (da tool_calls)
        cursor = await self._db.execute(
            """SELECT tool_name, SUM(cost_usd) as cost
               FROM tool_calls WHERE timestamp >= ? AND cost_usd IS NOT NULL
               GROUP BY tool_name""",
            (since,),
        )
        per_tool = {row["tool_name"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Per giorno (da llm_calls — costo LLM è la componente principale)
        cursor = await self._db.execute(
            """SELECT DATE(timestamp) as day, SUM(cost_usd) as cost
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY DATE(timestamp) ORDER BY day""",
            (since,),
        )
        per_day = {row["day"]: row["cost"] or 0.0 for row in await cursor.fetchall()}

        # Token per giorno (input + output + cache_read per giorno)
        cursor = await self._db.execute(
            """SELECT DATE(timestamp) as day,
                      COALESCE(SUM(input_tokens), 0)       AS input,
                      COALESCE(SUM(output_tokens), 0)      AS output,
                      COALESCE(SUM(cache_read_tokens), 0)  AS cache_read
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY DATE(timestamp) ORDER BY day""",
            (since,),
        )
        tokens_per_day = {
            row["day"]: {
                "input":      int(row["input"]),
                "output":     int(row["output"]),
                "cache_read": int(row["cache_read"]),
            }
            for row in await cursor.fetchall()
        }

        # Totale
        total = sum(per_agent.values())

        # Cache savings — per ogni modello calcola quanto si è risparmiato
        # rispetto a pagare il full input price al posto del cache_read price.
        # Formula: savings = cache_read_tokens × (input_price - cache_read_price) / 1_000_000
        cursor = await self._db.execute(
            """SELECT model,
                      SUM(cache_read_tokens)  AS total_cache_read,
                      SUM(cache_write_tokens) AS total_cache_write,
                      SUM(input_tokens)       AS total_input,
                      SUM(output_tokens)      AS total_output
               FROM llm_calls WHERE timestamp >= ?
               GROUP BY model""",
            (since,),
        )
        rows = await cursor.fetchall()

        total_cache_read: int = 0
        total_cache_write: int = 0
        total_input: int = 0
        total_output: int = 0
        savings_usd: float = 0.0

        for row in rows:
            model: str = row["model"] or ""
            cr: int = row["total_cache_read"] or 0
            cw: int = row["total_cache_write"] or 0
            inp: int = row["total_input"] or 0
            out: int = row["total_output"] or 0
            total_output += out

            # Identifica tier pricing dal nome modello
            if "haiku" in model.lower():
                in_price = settings.LLM_HAIKU_INPUT_PRICE
                cr_price = settings.LLM_HAIKU_CACHE_READ_PRICE
            else:  # sonnet o altro modello non-haiku
                in_price = settings.LLM_SONNET_INPUT_PRICE
                cr_price = settings.LLM_SONNET_CACHE_READ_PRICE

            savings_usd += cr * (in_price - cr_price) / 1_000_000

            total_cache_read += cr
            total_cache_write += cw
            total_input += inp

        # Efficienza cache: % dei token di input serviti da cache vs pagati full
        denominator = total_cache_read + total_input
        efficiency_pct = round(total_cache_read / denominator * 100, 1) if denominator > 0 else 0.0

        cache = {
            "read_tokens": total_cache_read,
            "write_tokens": total_cache_write,
            "savings_usd": round(savings_usd, 6),
            "efficiency_pct": efficiency_pct,
        }

        tokens = {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        }

        # Image cost today (da production_queue.image_cost_usd aggiornato oggi)
        cursor = await self._db.execute(
            """SELECT COALESCE(SUM(image_cost_usd), 0.0) AS total
               FROM production_queue
               WHERE date(updated_at) = date('now') AND image_cost_usd > 0"""
        )
        row = await cursor.fetchone()
        image_cost_today: float = float((row["total"] if row else None) or 0.0)

        # Fee cost today (listing_fee_usd degli item pubblicati oggi)
        cursor = await self._db.execute(
            """SELECT COALESCE(SUM(listing_fee_usd), 0.0) AS total
               FROM production_queue
               WHERE date(published_at) = date('now') AND status = 'published'"""
        )
        row = await cursor.fetchone()
        fee_cost_today: float = float((row["total"] if row else None) or 0.0)

        # Pinterest image-gen cost today (cost_image_gen da pinterest_queue pubblicati oggi)
        try:
            cursor = await self._db.execute(
                """SELECT COALESCE(SUM(cost_image_gen), 0.0) AS total
                   FROM pinterest_queue
                   WHERE date(published_at) = date('now') AND status = 'published'"""
            )
            row = await cursor.fetchone()
            pinterest_cost_today: float = float((row["total"] if row else None) or 0.0)
        except Exception:
            pinterest_cost_today = 0.0

        return {
            "per_agent": per_agent,
            "per_tool": per_tool,
            "per_day": per_day,
            "tokens_per_day": tokens_per_day,
            "total": total,
            "cache": cache,
            "tokens": tokens,
            "image_cost_today":     image_cost_today,
            "fee_cost_today":       fee_cost_today,
            "pinterest_cost_today": pinterest_cost_today,
        }

    async def get_agent_logs_summary(self, period_days: int = 14) -> dict:
        """Aggregati task da agent_logs per il frontend Analytics.

        Ritorna:
          total, completed, failed, running, by_status,
          per_day (YYYY-MM-DD → {status: count}),
          per_agent (agent_name → {total, completed, failed, cost}),
          production_queue stats.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")

        # Conteggi per status
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) as cnt FROM agent_logs "
            "WHERE created_at >= ? GROUP BY status",
            (since,),
        )
        by_status: dict[str, int] = {r["status"]: r["cnt"] for r in await cursor.fetchall()}

        # Per giorno × status (per grafico)
        cursor = await self._db.execute(
            """SELECT DATE(created_at) as day, status, COUNT(*) as cnt
               FROM agent_logs WHERE created_at >= ?
               GROUP BY day, status ORDER BY day""",
            (since,),
        )
        per_day: dict[str, dict[str, int]] = {}
        for r in await cursor.fetchall():
            day = r["day"]
            if day not in per_day:
                per_day[day] = {}
            per_day[day][r["status"]] = r["cnt"]

        # Per agente (totale task + costo)
        cursor = await self._db.execute(
            """SELECT agent_name,
                      COUNT(*) as total,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) as failed,
                      SUM(total_cost_usd) as cost
               FROM agent_logs WHERE created_at >= ?
               GROUP BY agent_name""",
            (since,),
        )
        per_agent: dict[str, dict] = {}
        for r in await cursor.fetchall():
            per_agent[r["agent_name"]] = {
                "total":     r["total"],
                "completed": r["completed"],
                "failed":    r["failed"],
                "cost":      r["cost"] or 0.0,
            }

        total     = sum(by_status.values())
        completed = by_status.get("completed", 0)
        failed    = by_status.get("failed", 0)
        running   = by_status.get("running", 0)

        pq_stats  = await self.get_production_queue_stats()

        return {
            "days":             period_days,
            "total":            total,
            "completed":        completed,
            "failed":           failed,
            "running":          running,
            "by_status":        by_status,
            "per_day":          per_day,
            "per_agent":        per_agent,
            "production_queue": pq_stats,
        }

    async def get_chroma_stats(self) -> dict:
        """
        Conta le entry in tutte le collection ChromaDB.

        Risposta: { available, count, by_collection: { pepe_memory, screen_memory,
                    personal_memory, shared_memory } }
        """
        if self._chroma_collection is None:
            return {"available": False, "count": 0, "by_collection": {}}
        try:
            by_collection = {}
            total = 0

            _collections = {
                "pepe_memory":     self._chroma_collection,
                "screen_memory":   self._screen_memory_collection,
                "personal_memory": self._personal_memory_collection,
                "shared_memory":   self._shared_memory_collection,
            }
            for name, col in _collections.items():
                if col is not None:
                    try:
                        n = await asyncio.to_thread(col.count)
                        by_collection[name] = n
                        total += n
                    except Exception:
                        by_collection[name] = 0
                else:
                    by_collection[name] = 0

            return {"available": True, "count": total, "by_collection": by_collection}
        except Exception as exc:
            return {"available": False, "count": 0, "by_collection": {}, "error": str(exc)}

    async def log_memory_query(
        self,
        doc_ids: list[str],
        collection: str,
        agent: str = "unknown",
        query_text: str | None = None,
    ) -> None:
        """Registra una query ChromaDB nella tabella memory_queries e invia WS event.

        Chiamata internamente da query_chromadb() e search_screen_memory().
        Silente in caso di errore — non deve bloccare il flusso principale.
        """
        if not doc_ids:
            return
        try:
            await self._db.execute(
                """INSERT INTO memory_queries (agent, collection, doc_ids, query_text)
                   VALUES (?, ?, ?, ?)""",
                (agent, collection, json.dumps(doc_ids), query_text),
            )
            await self._db.commit()
        except Exception as exc:
            logger.warning("log_memory_query fallito: %s", exc)
            return

        # Broadcast WS event per il neural brain (live node activation)
        if self._ws_broadcaster is not None:
            try:
                await self._ws_broadcaster({
                    "type": "memory_query",
                    "agent": agent,
                    "collection": collection,
                    "ids": doc_ids,
                    "query": query_text,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as exc:
                logger.warning("log_memory_query WS broadcast fallito: %s", exc)

    async def get_node_access_history(
        self,
        doc_id: str,
        collection: str,
        limit: int = 20,
    ) -> list[dict]:
        """Restituisce le ultime `limit` query che hanno acceduto al doc_id specificato.

        Filtra memory_queries dove doc_ids JSON contiene doc_id.
        """
        try:
            cursor = await self._db.execute(
                """SELECT agent, collection, doc_ids, query_text, queried_at
                   FROM memory_queries
                   WHERE collection = ?
                     AND doc_ids LIKE ?
                   ORDER BY queried_at DESC
                   LIMIT ?""",
                (collection, f'%"{doc_id}"%', limit),
            )
            rows = await cursor.fetchall()
            out = []
            for row in rows:
                ids = _json_loads(row["doc_ids"]) or []
                if doc_id in ids:
                    out.append({
                        "agent": row["agent"],
                        "collection": row["collection"],
                        "query_text": row["query_text"],
                        "queried_at": row["queried_at"],
                    })
            return out
        except Exception as exc:
            logger.warning("get_node_access_history fallito: %s", exc)
            return []

    async def get_analytics_summary(self, days: int = 7) -> dict:
        """Statistiche aggregate etsy_listings per periodo."""
        cursor = await self._db.execute(
            """SELECT
               COALESCE(SUM(views), 0) as total_views,
               COALESCE(SUM(sales), 0) as total_sales,
               COALESCE(SUM(revenue_eur), 0) as revenue
               FROM etsy_listings
               WHERE last_synced_at >= datetime('now', ?)""",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # Query pubbliche esposte da main.py
    # ------------------------------------------------------------------

    async def get_scheduled_tasks(self) -> list[dict]:
        """Task schedulati dal DB, ordinati per prossima esecuzione."""
        cursor = await self._db.execute("SELECT * FROM scheduled_tasks ORDER BY next_run")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_agent_steps(self, limit: int = 50, agent_name: str | None = None) -> list[dict]:
        """Ultimi N step (opzionalmente filtrati per agente), in ordine cronologico crescente."""
        if agent_name:
            cursor = await self._db.execute(
                """SELECT id, task_id, agent_name, step_number, step_type,
                          description, duration_ms, timestamp
                   FROM agent_steps
                   WHERE agent_name = ?
                   ORDER BY id DESC LIMIT ?""",
                (agent_name, limit),
            )
        else:
            cursor = await self._db.execute(
                """SELECT id, task_id, agent_name, step_number, step_type,
                          description, duration_ms, timestamp
                   FROM agent_steps
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
        rows = await cursor.fetchall()
        return list(reversed([dict(r) for r in rows]))

    async def get_domain_agent_stats(self, domain: str = "personal", days: int = 14) -> dict[str, dict]:
        """Aggregati completati/falliti per agente in un dominio, ultimi N giorni."""
        since = f"-{days} days"
        cursor = await self._db.execute(
            """SELECT agent_name, status, COUNT(*) as cnt
               FROM agent_logs
               WHERE domain = ? AND created_at >= datetime('now', ?)
               GROUP BY agent_name, status
               ORDER BY agent_name, status""",
            (domain, since),
        )
        rows = await cursor.fetchall()
        stats: dict[str, dict] = {}
        for r in rows:
            name = r["agent_name"]
            if name not in stats:
                stats[name] = {"completed": 0, "failed": 0, "running": 0}
            key = r["status"] if r["status"] in stats[name] else "running"
            stats[name][key] = r["cnt"]
        return stats

    async def get_agent_steps_count(self, agent: str = "*", hours: int = 24) -> int:
        """Conta gli step registrati nelle ultime N ore. agent='*' per tutti gli agenti."""
        if agent == "*":
            async with self._db.execute(
                "SELECT COUNT(*) FROM agent_steps WHERE timestamp >= datetime('now', ?)",
                (f"-{hours} hours",),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with self._db.execute(
                "SELECT COUNT(*) FROM agent_steps WHERE agent_name=? AND timestamp >= datetime('now', ?)",
                (agent, f"-{hours} hours"),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Scheduled Tasks
    # ------------------------------------------------------------------

    async def get_enabled_scheduled_tasks(self) -> list[dict]:
        """Restituisce tutti i task schedulati abilitati."""
        async with self._db.execute(
            "SELECT id, name, cron_expression, agent_name, task_data, enabled "
            "FROM scheduled_tasks WHERE enabled = 1"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_task_last_run(self, task_id: int, last_run_iso: str) -> None:
        """Aggiorna il campo last_run di un task schedulato."""
        await self._db.execute(
            "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
            (last_run_iso, task_id),
        )
        await self._db.commit()
