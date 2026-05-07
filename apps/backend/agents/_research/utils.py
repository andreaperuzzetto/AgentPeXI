"""_research/utils.py — shared helpers per Research Agent (C.1.0)."""
from __future__ import annotations

import hashlib


def _compute_cluster_id(niche: str) -> str:
    """cluster_id deterministico: sha256(niche.lower().strip())[:12].

    Case-insensitive e whitespace-insensitive:
        "ADHD Planner" == "adhd planner" == "  adhd planner  "
    """
    return hashlib.sha256(niche.lower().strip().encode()).hexdigest()[:12]
