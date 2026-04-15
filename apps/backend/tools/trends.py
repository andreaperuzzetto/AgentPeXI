"""Google Trends wrapper via pytrends (sincrono → eseguito in thread executor)."""

from __future__ import annotations

import asyncio
from typing import Any


def _sync_trends(keyword: str) -> dict[str, Any]:
    """Chiamata sincrona a pytrends — da eseguire in executor."""
    try:
        from pytrends.request import TrendReq

        pt = TrendReq(hl="en-US", tz=0)
        pt.build_payload([keyword], timeframe="today 3-m")
        df = pt.interest_over_time()

        if df.empty or keyword not in df.columns:
            return {
                "trend_direction": "unknown",
                "current_value": 0,
                "avg_value": 0,
                "percent_change": 0,
            }

        values = df[keyword].tolist()
        current = values[-1] if values else 0
        avg = sum(values) / len(values) if values else 0
        first = values[0] if values else 0
        pct_change = ((current - first) / first * 100) if first > 0 else 0

        if pct_change > 10:
            direction = "growing"
        elif pct_change < -10:
            direction = "declining"
        else:
            direction = "stable"

        return {
            "trend_direction": direction,
            "current_value": int(current),
            "avg_value": round(avg, 1),
            "percent_change": round(pct_change, 1),
            "data_points": len(values),
            "source": "google_trends",
        }
    except Exception as e:
        return {
            "trend_direction": "unknown",
            "current_value": 0,
            "avg_value": 0,
            "percent_change": 0,
            "data_points": 0,
            "source": "google_trends_failed",
            "error": str(e),
        }


async def get_google_trends(keyword: str) -> dict[str, Any]:
    """Async wrapper — esegue pytrends in thread executor."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _sync_trends, keyword)
    except Exception as e:
        return {
            "trend_direction": "unknown",
            "error": str(e),
            "source": "google_trends_failed",
        }
