"""Personal learning mixin for MemoryManager."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("agentpexi.memory")


class LearningMixin:
    # ------------------------------------------------------------------
    # Personal Learning
    # ------------------------------------------------------------------

    _WEIGHT_MIN = 0.1
    _WEIGHT_MAX = 0.9
    _ACCEPTANCE_THRESHOLD = 0.02  # |weight_delta| minimo per modificare il peso

    async def upsert_learning(
        self,
        agent: str,
        pattern_type: str,
        pattern_value: str,
        signal_type: str,
        weight_delta: float,
    ) -> None:
        """INSERT OR UPDATE con UNIQUE(agent, pattern_type, pattern_value).

        Weight clampato a [_WEIGHT_MIN, _WEIGHT_MAX].

        Gate di accettazione (UPDATE path only):
        se |weight_delta| < _ACCEPTANCE_THRESHOLD il peso non viene modificato —
        il segnale è troppo debole per essere considerato apprendimento reale.
        occurrences e last_seen vengono sempre aggiornati (il pattern è stato visto).
        Ogni valutazione (accettata o no) viene registrata in learning_evaluations
        tramite save_learning_evaluation(), per permettere analisi future
        via get_pattern_acceptance_rate().

        INSERT path: sempre accettato — nessuna baseline disponibile alla prima osservazione.
        """
        async with self._db.execute(
            "SELECT id, weight, occurrences FROM personal_learning WHERE agent=? AND pattern_type=? AND pattern_value=?",
            (agent, pattern_type, pattern_value),
        ) as cur:
            row = await cur.fetchone()

        if row:
            accepted = abs(weight_delta) >= self._ACCEPTANCE_THRESHOLD
            new_weight = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, row["weight"] + weight_delta))

            if accepted:
                await self._db.execute(
                    """UPDATE personal_learning
                       SET weight=?, occurrences=?, last_seen=datetime('now'), signal_type=?
                       WHERE id=?""",
                    (new_weight, row["occurrences"] + 1, signal_type, row["id"]),
                )
            else:
                # Segnale troppo debole: aggiorna occurrences e last_seen, peso invariato
                new_weight = row["weight"]
                await self._db.execute(
                    """UPDATE personal_learning
                       SET occurrences=?, last_seen=datetime('now')
                       WHERE id=?""",
                    (row["occurrences"] + 1, row["id"]),
                )

            await self._db.commit()

            # Registra la valutazione — fail-safe, non blocca mai
            try:
                await self.save_learning_evaluation(
                    pattern_id=str(row["id"]),
                    signal_type=signal_type,
                    metric_type=pattern_type,
                    baseline_value=row["weight"],
                    post_value=new_weight,
                    accepted=accepted,
                )
            except Exception as exc:
                logger.debug("save_learning_evaluation fallito (fail-safe): %s", exc)
        else:
            # Prima osservazione: INSERT sempre accettato (nessuna baseline disponibile)
            initial = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, 0.5 + weight_delta))
            await self._db.execute(
                """INSERT INTO personal_learning
                   (agent, pattern_type, pattern_value, signal_type, weight)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent, pattern_type, pattern_value, signal_type, initial),
            )
            await self._db.commit()

    async def get_learning_patterns(
        self,
        agent: str,
        pattern_type: str | None = None,
        min_weight: float = 0.0,
    ) -> list[dict]:
        if pattern_type:
            async with self._db.execute(
                "SELECT * FROM personal_learning WHERE agent=? AND pattern_type=? AND weight>=? ORDER BY weight DESC",
                (agent, pattern_type, min_weight),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                "SELECT * FROM personal_learning WHERE agent=? AND weight>=? ORDER BY weight DESC",
                (agent, min_weight),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def save_learning_evaluation(
        self,
        pattern_id: str,
        signal_type: str,
        metric_type: str,
        baseline_value: float,
        post_value: float,
        accepted: bool,
    ) -> None:
        """Registra la valutazione di un pattern (accettato o rifiutato) nella tabella learning_evaluations."""
        delta = post_value - baseline_value
        evaluated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self._db.execute(
            """INSERT INTO learning_evaluations
               (pattern_id, signal_type, metric_type, baseline_value, post_value, delta, accepted, evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, signal_type, metric_type, baseline_value, post_value, delta, int(accepted), evaluated_at),
        )
        await self._db.commit()

    async def get_pattern_acceptance_rate(self, signal_type: str, last_n: int = 20) -> float:
        """Ritorna il tasso di accettazione degli ultimi N pattern per questo signal_type.
        Usato per decidere se il sistema sta imparando cose utili o rumore."""
        async with self._db.execute(
            """SELECT AVG(accepted) FROM (
                 SELECT accepted FROM learning_evaluations
                 WHERE signal_type = ?
                 ORDER BY id DESC LIMIT ?
               )""",
            (signal_type, last_n),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return 0.0
        return float(row[0])

    async def get_baseline_metric(self, metric_type: str, window: int = 10) -> float | None:
        """Calcola il valore baseline della metrica nelle ultime `window` occorrenze.
        Ritorna None se dati insufficienti."""
        async with self._db.execute(
            """SELECT AVG(post_value) FROM (
                 SELECT post_value FROM learning_evaluations
                 WHERE metric_type = ?
                 ORDER BY id DESC LIMIT ?
               )""",
            (metric_type, window),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    async def decay_old_patterns(self, days: int = 7, factor: float = 0.98) -> int:
        """Applica decay ai pattern non visti da più di N giorni. Restituisce numero di righe aggiornate."""
        cursor = await self._db.execute(
            f"""UPDATE personal_learning
               SET weight = MAX({self._WEIGHT_MIN}, weight * ?)
               WHERE last_seen < datetime('now', ?)""",
            (factor, f"-{days} days"),
        )
        updated = cursor.rowcount
        await self._db.commit()
        return updated if updated is not None else 0

    async def detect_watcher_habits(self, days: int = 7, min_days: int = 5) -> list[dict]:
        """Rileva pattern abitudinali Watcher: stessa app in stesso slot orario per min_days+.
        Slot orario = ora arrotondata al multiplo di 2 (0,2,4,...,22)."""
        async with self._db.execute(
            """SELECT
                 json_extract(description, '$.app_name') AS app_name,
                 (CAST(strftime('%H', timestamp) AS INTEGER) / 2 * 2) AS hour_slot,
                 COUNT(DISTINCT date(timestamp)) AS day_count
               FROM agent_steps
               WHERE agent_name = 'watcher'
               AND timestamp >= datetime('now', ?)
               AND json_valid(description)
               GROUP BY app_name, hour_slot
               HAVING day_count >= ?""",
            (f"-{days} days", min_days),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "pattern": f"{r['app_name']}_slot{r['hour_slot']:02d}",
                "app_name": r["app_name"],
                "hour_slot": r["hour_slot"],
                "day_count": r["day_count"],
            }
            for r in rows
            if r["app_name"]
        ]

    async def get_frequent_queries(self, days: int = 7, min_occurrences: int = 3) -> list[str]:
        """Pattern_value di tipo 'topic' con occurrences >= min e last_seen recente."""
        async with self._db.execute(
            """SELECT pattern_value FROM personal_learning
               WHERE pattern_type = 'topic'
               AND last_seen >= datetime('now', ?)
               AND occurrences >= ?
               ORDER BY occurrences DESC""",
            (f"-{days} days", min_occurrences),
        ) as cur:
            rows = await cur.fetchall()
        return [r["pattern_value"] for r in rows]
