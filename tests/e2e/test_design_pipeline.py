"""tests/e2e/test_design_pipeline.py — A.3 gate: Design step criteria.

  DS1: PDFGenerator.generate() completes without exception + produces non-empty file
  DS2: _validate_pdf() returns valid=True for a generated PDF (pages + size OK)
  DS3: _validate_pdf() returns valid=False for a missing file
  DS4: _calculate_design_confidence() for full output returns float in [0.0, 1.0]
  DS5: _calculate_design_confidence() for zero-variant run returns score < 0.5
"""
from __future__ import annotations

import pytest

from apps.backend.agents._design.scoring import _calculate_design_confidence, _validate_pdf
from apps.backend.tools.file_gen import ColorScheme, PDFGenerator


# Shared minimal ColorScheme — sage palette
_SAGE = ColorScheme(
    name="sage",
    primary=(106, 134, 103),
    secondary=(245, 242, 235),
    accent=(65, 90, 62),
    background=(235, 240, 228),
)


# ---------------------------------------------------------------------------
# DS1: PDFGenerator.generate() runs without exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds1_pdf_generator_no_exception(tmp_path):
    """PDFGenerator.generate('daily_journal') produces a non-empty PDF file."""
    gen = PDFGenerator()
    out = tmp_path / "test_daily_journal.pdf"
    result = await gen.generate("daily_journal", _SAGE, "A4", out)
    assert result.exists(), "PDF file was not created"
    assert result.stat().st_size > 0, "PDF file is empty"


# ---------------------------------------------------------------------------
# DS2: _validate_pdf returns valid=True for a generated PDF
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds2_validate_pdf_generated_file_is_valid(tmp_path):
    """_validate_pdf() on a real generated PDF returns valid=True and page_count >= 1."""
    gen = PDFGenerator()
    out = tmp_path / "test_validate.pdf"
    await gen.generate("daily_journal", _SAGE, "A4", out)
    result = await _validate_pdf(out, "daily_journal", 1)
    assert result["valid"] is True, f"Unexpected validation issues: {result['issues']}"
    assert result["page_count"] >= 1, f"Expected >= 1 pages, got {result['page_count']}"


# ---------------------------------------------------------------------------
# DS3: _validate_pdf returns valid=False for a missing file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ds3_validate_pdf_missing_file_is_invalid(tmp_path):
    """_validate_pdf() on a non-existent path returns valid=False and page_count=0."""
    result = await _validate_pdf(tmp_path / "nonexistent.pdf", "daily_journal", 1)
    assert result["valid"] is False
    assert result["page_count"] == 0


# ---------------------------------------------------------------------------
# DS4: _calculate_design_confidence returns float in [0.0, 1.0]
# ---------------------------------------------------------------------------

def test_ds4_design_confidence_full_output_in_range():
    """_calculate_design_confidence() with perfect input returns score in [0.0, 1.0]."""
    score, missing = _calculate_design_confidence(
        variants_generated=2,
        variants_requested=2,
        thumbnails=[{"cover": True, "interior": True, "mockup": True}] * 2,
        validation_results=[{"valid": True}] * 2,
        fonts_available={"NotoSerif": True, "Playfair": True},
        research_available=True,
    )
    assert 0.0 <= score <= 1.0, f"score={score} is out of [0, 1] range"
    assert len(missing) == 0, f"Unexpected missing_data: {missing}"


# ---------------------------------------------------------------------------
# DS5: _calculate_design_confidence for zero-variant run returns score < 0.5
# ---------------------------------------------------------------------------

def test_ds5_design_confidence_zero_variants_below_threshold():
    """When 0 variants generated and no research context, confidence < 0.5."""
    score, _ = _calculate_design_confidence(
        variants_generated=0,
        variants_requested=2,
        thumbnails=[],
        validation_results=[],
        fonts_available={},
        research_available=False,
    )
    assert score < 0.5, f"Expected score < 0.5 when nothing generated, got {score}"
