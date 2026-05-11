"""Coverage tests for _design/scoring.py residual branches + _design/utils.py.

Targets:
- apps/backend/agents/_design/scoring.py  → 100% (covers lines 44, 53, 56-59, 122)
- apps/backend/agents/_design/utils.py    → 100% (covers lines 28-33: _count_pdf_pages)

Mock strategy (both files import PdfReader inside function body):
  patch("pypdf.PdfReader") — patches the class at source so any
  `from pypdf import PdfReader` inside the function picks up the mock.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.backend.agents._design.scoring import (
    _calculate_design_confidence,
    _validate_pdf,
)
from apps.backend.agents._design.utils import _count_pdf_pages


# ─── helpers ──────────────────────────────────────────────────────────────────


def _mock_path(size_bytes: int, exists: bool = True) -> MagicMock:
    """Return a MagicMock(spec=Path) with configurable stat().st_size."""
    mp = MagicMock(spec=Path)
    mp.exists.return_value = exists
    mp.stat.return_value.st_size = size_bytes
    mp.__str__ = MagicMock(return_value="/fake/test.pdf")
    return mp


# ─── scoring.py residual branches ─────────────────────────────────────────────


class TestScoringResidual:
    """4 uncovered branches in _validate_pdf and _calculate_design_confidence."""

    async def test_file_too_small_appends_issue(self):
        """Line 44: file_size_kb < min_size → 'File too small' issue."""
        # "default" template has min_size 15 KB; supply only 5 KB.
        mp = _mock_path(size_bytes=5 * 1024)

        with patch("pypdf.PdfReader") as mock_pdf:
            mock_pdf.return_value.pages = [MagicMock()] * 1
            result = await _validate_pdf(mp, template="default", expected_pages=0)

        assert result["valid"] is False
        assert any("File too small" in issue for issue in result["issues"])

    async def test_wrong_page_count_appends_issue(self):
        """Line 53: page_count < expected_pages → 'Wrong page count' issue."""
        # 100 KB file (well above minimum); PdfReader returns only 2 pages.
        mp = _mock_path(size_bytes=100 * 1024)

        with patch("pypdf.PdfReader") as mock_pdf:
            mock_pdf.return_value.pages = [MagicMock()] * 2
            result = await _validate_pdf(mp, template="default", expected_pages=5)

        assert result["valid"] is False
        assert any("Wrong page count" in issue for issue in result["issues"])
        assert result["page_count"] == 2

    async def test_pages_seem_empty_appends_issue(self):
        """Lines 56-59: file_size_kb / page_count < 1.0 → 'Pages seem empty'."""
        # 5 KB total, 10 pages → 0.5 KB/page < 1.0 threshold.
        mp = _mock_path(size_bytes=5 * 1024)

        with patch("pypdf.PdfReader") as mock_pdf:
            mock_pdf.return_value.pages = [MagicMock()] * 10
            result = await _validate_pdf(mp, template="default", expected_pages=0)

        assert any("Pages seem empty" in issue for issue in result["issues"])

    async def test_pdf_reader_exception_appends_issue(self):
        """Lines 58-59: PdfReader raises Exception → 'Could not read PDF' issue."""
        mp = _mock_path(size_bytes=100 * 1024)

        with patch("pypdf.PdfReader", side_effect=Exception("broken PDF")):
            result = await _validate_pdf(mp, template="default", expected_pages=0)

        assert result["valid"] is False
        assert any("Could not read PDF" in issue for issue in result["issues"])
        assert result["page_count"] == 0

    def test_partial_validation_failure_adds_missing_data(self):
        """Line 122: val_ratio < 1.0 when some PDFs fail → missing_data entry."""
        # One valid, one invalid → val_ratio = 0.5 → triggers line 122.
        validation_results = [{"valid": True}, {"valid": False}]

        _score, missing_data = _calculate_design_confidence(
            variants_generated=1,
            variants_requested=1,
            thumbnails=[{"cover": True, "interior": True, "mockup": True}],
            validation_results=validation_results,
            fonts_available={"font1": True},
            research_available=True,
        )

        assert any("Some PDFs failed validation" in msg for msg in missing_data)


# ─── utils.py: _count_pdf_pages ───────────────────────────────────────────────


class TestCountPdfPages:
    """2 paths in _count_pdf_pages (lines 28-33)."""

    def test_happy_path_returns_page_count(self):
        """Lines 29-31: PdfReader succeeds → returns len(reader.pages)."""
        with patch("pypdf.PdfReader") as mock_pdf:
            mock_pdf.return_value.pages = [MagicMock()] * 5
            result = _count_pdf_pages(Path("/fake/file.pdf"))

        assert result == 5

    def test_exception_returns_zero(self):
        """Lines 32-33: PdfReader raises Exception → returns 0."""
        with patch("pypdf.PdfReader", side_effect=Exception("corrupted")):
            result = _count_pdf_pages(Path("/fake/file.pdf"))

        assert result == 0
