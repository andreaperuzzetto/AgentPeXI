"""Test unit per tools/pdf_generator.py — mock funzioni interne WeasyPrint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools.pdf_generator import PDFGenerationError, render_pdf, render_pdf_to_bytes


# ---------------------------------------------------------------------------
# render_pdf
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_pdf_happy_path(tmp_path: Path):
    """render_pdf rende HTML con Jinja2 e chiama _write_pdf."""
    template = tmp_path / "template.html"
    template.write_text("<html><body>{{ title }}</body></html>", encoding="utf-8")
    output = tmp_path / "output.pdf"

    # Mock _write_pdf (funzione interna che usa WeasyPrint — non disponibile in CI)
    with patch("tools.pdf_generator._write_pdf", return_value=str(output)) as mock_write:
        result = await render_pdf(
            template_path=str(template),
            context={"title": "Proposta Commerciale"},
            output_path=str(output),
        )

    assert result == str(output)
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_render_pdf_template_not_found(tmp_path: Path):
    """render_pdf solleva PDFGenerationError se il template non esiste."""
    with pytest.raises(PDFGenerationError):
        await render_pdf(
            template_path=str(tmp_path / "nonexistent.html"),
            context={},
            output_path=str(tmp_path / "out.pdf"),
        )


@pytest.mark.asyncio
async def test_render_pdf_weasyprint_error(tmp_path: Path):
    """render_pdf wrappa le eccezioni WeasyPrint in PDFGenerationError."""
    template = tmp_path / "template.html"
    template.write_text("<html><body>{{ value }}</body></html>", encoding="utf-8")
    output = tmp_path / "output.pdf"

    with patch("tools.pdf_generator._write_pdf",
               side_effect=PDFGenerationError("WeasyPrint internal error")):
        with pytest.raises(PDFGenerationError) as exc_info:
            await render_pdf(
                template_path=str(template),
                context={"value": "test"},
                output_path=str(output),
            )

    assert exc_info.value.code == "tool_pdf_generation_error"


@pytest.mark.asyncio
async def test_render_pdf_jinja_syntax_error(tmp_path: Path):
    """Template Jinja2 non valido solleva PDFGenerationError."""
    template = tmp_path / "bad_template.html"
    template.write_text("<html>{% invalid_tag %}</html>", encoding="utf-8")
    output = tmp_path / "output.pdf"

    with pytest.raises(PDFGenerationError):
        await render_pdf(
            template_path=str(template),
            context={},
            output_path=str(output),
        )


# ---------------------------------------------------------------------------
# render_pdf_to_bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_pdf_to_bytes_happy_path(tmp_path: Path):
    """render_pdf_to_bytes restituisce bytes PDF."""
    template = tmp_path / "template.html"
    template.write_text("<html><body>{{ name }}</body></html>", encoding="utf-8")

    with patch("tools.pdf_generator._write_pdf_bytes", return_value=b"%PDF-1.4") as mock_wb:
        data = await render_pdf_to_bytes(
            template_path=str(template),
            context={"name": "Test"},
        )

    assert data == b"%PDF-1.4"
    mock_wb.assert_called_once()
