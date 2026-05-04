import logging
import uuid
from datetime import datetime, timezone as _tz
from typing import Annotated

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from apps.backend.core.models import AgentTask

logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])


@router.get("/api/finance/summary")
async def get_finance_summary(
    year: int | None = None,
    month: int | None = None,
) -> dict:
    """
    P&L mensile aggregato + breakdown per niche (FE-Blocco 0.2).

    Query params opzionali:
      year  int — default: anno corrente
      month int — default: mese corrente

    Risposta: { year, month, n_sales, gross_eur, etsy_fees_eur,
                listing_fees_eur, design_costs_eur, net_eur, margin_pct,
                by_niche: [{ niche, gross_eur, net_eur, total_fees_eur, sales_count }] }
    """
    if not state.finance_tracker:
        return JSONResponse(status_code=503, content={"error": "FinanceTracker non inizializzato"})
    try:
        summary = await state.finance_tracker.monthly_summary(year=year, month=month)

        # Breakdown per niche — stessa finestra temporale usata da monthly_summary
        _now   = datetime.now(_tz.utc)
        _year  = year  or _now.year
        _month = month or _now.month
        _start = datetime(_year, _month, 1, tzinfo=_tz.utc).timestamp()
        _end   = datetime(_year + 1, 1, 1, tzinfo=_tz.utc).timestamp() if _month == 12 \
                 else datetime(_year, _month + 1, 1, tzinfo=_tz.utc).timestamp()

        db = await state.memory.get_db()
        cursor = await db.execute(
            """
            SELECT
                niche,
                COUNT(*)              AS sales_count,
                SUM(gross_eur)        AS gross_eur,
                SUM(net_eur)          AS net_eur,
                SUM(etsy_fee_eur + listing_fee_eur + design_cost_eur) AS total_fees_eur
            FROM revenue_events
            WHERE sold_at >= ? AND sold_at < ?
              AND niche IS NOT NULL
            GROUP BY niche
            ORDER BY gross_eur DESC
            """,
            (_start, _end),
        )
        rows = await cursor.fetchall()
        by_niche = [
            {
                "niche":          r["niche"],
                "sales_count":    int(r["sales_count"]),
                "gross_eur":      round(float(r["gross_eur"] or 0.0), 2),
                "net_eur":        round(float(r["net_eur"]   or 0.0), 2),
                "total_fees_eur": round(float(r["total_fees_eur"] or 0.0), 2),
            }
            for r in rows
        ]

        return {**summary, "by_niche": by_niche}
    except Exception as exc:
        logger.exception("get_finance_summary error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/finance/report")
async def get_finance_report(days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
    """Ultimo report finance da ChromaDB + trigger run se mai eseguito."""
    if not state.memory:
        return {"report": None}
    results = await state.memory.query_chromadb(
        query="finance report revenue cost margin ROI",
        n_results=1,
        where={"type": "finance_report"},
    )
    return {"report": results[0] if results else None, "days": days}


@router.post("/api/finance/run")
async def run_finance_agent(request: Request, body: dict | None = None) -> dict:
    """Esegue il FinanceAgent manualmente (period_days dal body, default 30)."""
    if not state.pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    period_days = max(1, min(int((body or {}).get("period_days", 30)), 365))
    task_id = str(uuid.uuid4())
    task = AgentTask(
        task_id=task_id,
        agent_name="finance",
        input_data={"period_days": period_days},
        source="web",
    )
    await state.pepe.dispatch_task(task)
    return {"status": "dispatched", "task_id": task_id, "period_days": period_days}


@router.get("/api/analytics/latest")
async def get_analytics_latest() -> dict:
    """Ultimo report analytics da ChromaDB."""
    if not state.memory:
        return {"report": None}
    results = await state.memory.query_chromadb(
        query="daily analytics report",
        n_results=1,
        where={"type": "analytics_report"},
    )
    return {"report": results[0] if results else None}


@router.get("/api/analytics/failures")
async def get_analytics_failures(limit: Annotated[int, Query(ge=1, le=500)] = 20) -> dict:
    """Ultime failure analysis dai listing."""
    if not state.memory:
        return {"failures": []}
    failures = await state.memory.get_all_listing_analyses(limit=limit)
    return {"failures": failures}


@router.get("/api/analytics/ctr-ab")
async def get_analytics_ctr_ab(limit: Annotated[int, Query(ge=1, le=100)] = 50) -> dict:
    """
    Ultimi risultati A/B thumbnail da ChromaDB (FE-Blocco 0.3).

    Ogni documento `type=design_winner` contiene winner + loser template/color_scheme/ctr.
    Risposta: { results: [{ niche, product_type, winner: {...}, loser: {...}, compared_at }] }
    """
    if not state.memory:
        return {"results": []}
    try:
        raw = await state.memory.query_chromadb(
            query="A/B thumbnail winner template color_scheme CTR design test",
            n_results=limit,
            where={"type": "design_winner"},
            agent="api",
        )
        results = []
        for item in raw:
            meta = item.get("metadata") or {}
            if not meta.get("niche"):
                continue
            results.append({
                "niche":        meta.get("niche"),
                "product_type": meta.get("product_type"),
                "winner": {
                    "template":     meta.get("template", ""),
                    "color_scheme": meta.get("color_scheme", ""),
                    "ctr":          float(meta.get("ctr", 0) or 0),
                },
                "loser": {
                    "template":     meta.get("loser_template", ""),
                    "color_scheme": meta.get("loser_color_scheme", ""),
                    "ctr":          float(meta.get("loser_ctr", 0) or 0),
                },
                "compared_at": meta.get("date"),
            })
        return {"results": results}
    except Exception as exc:
        logger.exception("get_analytics_ctr_ab error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/analytics/ladder")
async def get_analytics_ladder() -> dict:
    """
    Distribuzione listing per ladder_level (FE-Blocco 0.3).

    Conta listing distinti per livello diagnostico da listing_performance.
    Risposta: { ok, views_low, ctr_low, conv_low, undiagnosed, total, last_updated }
    """
    if not state.memory:
        return {"ok": 0, "views_low": 0, "ctr_low": 0, "conv_low": 0, "undiagnosed": 0, "total": 0, "last_updated": None}
    try:
        db = await state.memory.get_db()

        # Conta per ladder_level sull'ultimo snapshot per listing (max snapshot_at)
        cursor = await db.execute(
            """
            SELECT
                ladder_level,
                COUNT(DISTINCT etsy_listing_id) AS cnt
            FROM listing_performance
            WHERE snapshot_at = (
                SELECT MAX(lp2.snapshot_at)
                FROM listing_performance lp2
                WHERE lp2.etsy_listing_id = listing_performance.etsy_listing_id
            )
            GROUP BY ladder_level
            """
        )
        rows = await cursor.fetchall()

        counts: dict[str, int] = {}
        for r in rows:
            key = r["ladder_level"] or "undiagnosed"
            counts[key] = int(r["cnt"])

        # Timestamp ultimo snapshot disponibile
        cur2 = await db.execute("SELECT MAX(snapshot_at) AS last FROM listing_performance")
        row2 = await cur2.fetchone()
        last_updated = float(row2["last"]) if row2 and row2["last"] else None

        total = sum(counts.values())
        return {
            "ok":          counts.get("ok", 0),
            "views_low":   counts.get("views_low", 0),
            "ctr_low":     counts.get("ctr_low", 0),
            "conv_low":    counts.get("conv_low", 0),
            "undiagnosed": counts.get("undiagnosed", 0),
            "total":       total,
            "last_updated": last_updated,
        }
    except Exception as exc:
        logger.exception("get_analytics_ladder error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
