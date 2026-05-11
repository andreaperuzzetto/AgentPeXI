"""Shared type aliases and TypedDicts for AgentPeXI.

Import from this module rather than redefining inline dicts across agents.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, TypedDict

# ── LLM ──────────────────────────────────────────────────────────────────────

LLMRole: TypeAlias = Literal["user", "assistant"]


class LLMMessage(TypedDict):
    role: LLMRole
    content: str | list[dict[str, Any]]


# ── SEO ──────────────────────────────────────────────────────────────────────


class SeoResult(TypedDict):
    title: str
    description: str
    tags: list[str]
    seo_validated: bool
    seo_issues: list[str]


# ── Production Queue ──────────────────────────────────────────────────────────

QueueStatus: TypeAlias = Literal[
    "pending_design",
    "pending_approval",
    "approved",
    "scheduled",
    "published",
    "skipped",
    "failed",
    "discarded",
    "planned",
]

ProductTier: TypeAlias = Literal["tripwire", "core", "core_premium", "bundle"]

SkipReason: TypeAlias = Literal["user", "timeout", "budget", "policy"]


# ── Publish ───────────────────────────────────────────────────────────────────


class PublishResult(TypedDict, total=False):
    niche: str
    file_type: str
    template: str
    color_scheme: str
    ab_variant: str
    listing_id: str | None
    images_uploaded: int
    seo_validated: bool
    price_source: str
    status: str
    error: str
    seo_issues: list[str]
    section_id: str | None


# ── Failure Adjustments ───────────────────────────────────────────────────────


class FailureAdjustments(TypedDict, total=False):
    failure_constraints_active: list[str]
    chromadb_failures: list[dict[str, str]]
    chromadb_successes: list[dict[str, str]]
    similar_failures: list[dict[str, Any]]
    warning: str
