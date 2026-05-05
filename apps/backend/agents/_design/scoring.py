"""DesignAgent — PDF validation and confidence scoring."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentpexi.design")


# =====================================================================
# PDF Validation (Intervento 14)
# =====================================================================

async def _validate_pdf(pdf_path: Path, template: str, expected_pages: int) -> dict:
    """Valida il PDF generato: dimensione minima e conteggio pagine."""
    issues: list[str] = []

    if not pdf_path.exists():
        return {"valid": False, "issues": ["PDF file not found"], "file_size_kb": 0, "page_count": 0}

    file_size_kb = pdf_path.stat().st_size / 1024

    MIN_SIZE_KB: dict[str, float] = {
        "weekly_planner":   25,   # genera ~30KB con Pillow/ReportLab
        "daily_planner":    25,
        "monthly_planner":  30,
        "budget_tracker":   20,
        "budget_sheet":     20,
        "habit_tracker":    20,
        "goal_planner":     20,
        "meal_planner":     20,
        "workout_tracker":  20,
        "gratitude_journal": 20,
        "reading_log":      20,
        "travel_planner":   20,
        "project_planner":  20,
        "daily_journal":    20,
        "default":          15,
    }
    min_size = MIN_SIZE_KB.get(template, MIN_SIZE_KB["default"])

    if file_size_kb < min_size:
        issues.append(f"File too small: {file_size_kb:.1f}KB (min {min_size}KB) — possible generation error")

    page_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)

        if expected_pages > 0 and page_count < expected_pages:
            issues.append(f"Wrong page count: {page_count} (expected {expected_pages})")

        if page_count > 0 and file_size_kb / page_count < 1.0:
            issues.append(f"Pages seem empty: avg {file_size_kb / page_count:.1f}KB per page")

    except Exception as e:
        issues.append(f"Could not read PDF for validation: {e}")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "file_size_kb": round(file_size_kb, 1),
        "page_count": page_count,
    }


# =====================================================================
# Confidence Scoring (Intervento 15)
# =====================================================================

def _calculate_design_confidence(
    variants_generated: int,
    variants_requested: int,
    thumbnails: list[dict],
    validation_results: list[dict],
    fonts_available: dict[str, bool],
    research_available: bool,
) -> tuple[float, list[str]]:
    """
    Calcola confidence score per il Design Agent.

    Pesi:
    - Variants completati: 0.35
    - Thumbnails generati: 0.25
    - PDF validation passed: 0.20
    - Font custom disponibili: 0.10
    - Research context disponibile: 0.10
    """
    missing_data: list[str] = []
    score = 0.0

    # Variants (0.35)
    if variants_requested > 0:
        ratio = variants_generated / variants_requested
        score += 0.35 * ratio
        if ratio < 1.0:
            missing_data.append(f"{variants_requested - variants_generated} variants failed to generate")

    # Thumbnails (0.25)
    thumbnail_score = 0.0
    total_thumbnails = variants_generated * 3
    generated_thumbnails = sum(
        (1 if t.get("cover") else 0)
        + (1 if t.get("interior") else 0)
        + (1 if t.get("mockup") else 0)
        for t in thumbnails
    )
    if total_thumbnails > 0:
        thumbnail_score = generated_thumbnails / total_thumbnails
    score += 0.25 * thumbnail_score
    if thumbnail_score < 1.0:
        missing_data.append("Some thumbnails failed to generate")

    # PDF validation (0.20)
    if validation_results:
        valid_count = sum(1 for v in validation_results if v.get("valid", False))
        val_ratio = valid_count / len(validation_results)
        score += 0.20 * val_ratio
        if val_ratio < 1.0:
            missing_data.append("Some PDFs failed validation")
    else:
        score += 0.10
        missing_data.append("PDF validation not performed")

    # Font custom (0.10)
    fonts_ok = sum(1 for v in fonts_available.values() if v)
    fonts_total = len(fonts_available) if fonts_available else 1
    font_ratio = fonts_ok / fonts_total
    score += 0.10 * font_ratio
    if font_ratio < 0.5:
        missing_data.append("Custom fonts not available — using fallback fonts")

    # Research context (0.10)
    if research_available:
        score += 0.10
    else:
        missing_data.append("No research context — template/colors not niche-optimized")

    return round(min(score, 1.0), 3), missing_data
