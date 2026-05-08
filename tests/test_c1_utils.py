"""Tests for _research/utils.py — _compute_cluster_id (C.1.0).

TDD: questi test sono scritti PRIMA dell'implementazione.
"""
from __future__ import annotations

import pytest

from apps.backend.agents._research.utils import _compute_cluster_id


def test_cluster_id_case_insensitive():
    """ADHD Planner e adhd planner devono produrre lo stesso cluster_id."""
    assert _compute_cluster_id("ADHD Planner") == _compute_cluster_id("adhd planner")


def test_cluster_id_length_12():
    """cluster_id deve essere esattamente 12 caratteri hex."""
    cid = _compute_cluster_id("adhd planner")
    assert len(cid) == 12


def test_cluster_id_strips_whitespace():
    """Spazi iniziali/finali non devono cambiare il cluster_id."""
    assert _compute_cluster_id("  adhd planner  ") == _compute_cluster_id("adhd planner")


def test_cluster_id_unique_per_niche():
    """Nicchie diverse devono produrre cluster_id diversi."""
    assert _compute_cluster_id("adhd planner") != _compute_cluster_id("wedding invitation")


def test_cluster_id_is_hex():
    """cluster_id deve contenere solo caratteri esadecimali."""
    cid = _compute_cluster_id("birthday party printable")
    assert all(c in "0123456789abcdef" for c in cid)


def test_cluster_id_deterministic():
    """Stessa nicchia chiamata due volte produce sempre lo stesso ID."""
    niche = "gratitude journal printable"
    assert _compute_cluster_id(niche) == _compute_cluster_id(niche)
