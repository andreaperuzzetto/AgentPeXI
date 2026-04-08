"""Test unit per tools/mockup_renderer.py — mock sottoprocesso Node.js/Puppeteer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tools.mockup_renderer import RenderError, RenderTimeoutError, render_to_pdf, render_to_png


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(returncode: int = 0, stderr: bytes = b""):
    """Mock del sottoprocesso Node.js."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


# ---------------------------------------------------------------------------
# render_to_png
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_to_png_happy_path(tmp_path: Path):
    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body>Test</body></html>", encoding="utf-8")
    output = str(tmp_path / "output.png")

    proc = _make_proc(returncode=0)

    with patch("tools.mockup_renderer.asyncio.create_subprocess_exec", return_value=proc):
        result = await render_to_png(
            html_path=str(html_file),
            output_path=output,
            viewport_width=1440,
            viewport_height=900,
        )

    assert result == output


@pytest.mark.asyncio
async def test_render_to_png_process_error(tmp_path: Path):
    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body>Test</body></html>", encoding="utf-8")
    output = str(tmp_path / "error.png")

    proc = _make_proc(returncode=1, stderr=b"Puppeteer error: page crash")

    with patch("tools.mockup_renderer.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RenderError) as exc_info:
            await render_to_png(html_path=str(html_file), output_path=output)

    assert exc_info.value.code == "tool_render_error"
    assert "Puppeteer error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_render_to_png_timeout(tmp_path: Path):
    import asyncio

    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body>Test</body></html>", encoding="utf-8")
    output = str(tmp_path / "timeout.png")

    proc = _make_proc()
    proc.communicate.side_effect = asyncio.TimeoutError()

    with patch("tools.mockup_renderer.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RenderTimeoutError) as exc_info:
            await render_to_png(html_path=str(html_file), output_path=output)

    assert exc_info.value.code == "tool_render_timeout"


# ---------------------------------------------------------------------------
# render_to_pdf
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_to_pdf_happy_path(tmp_path: Path):
    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body>PDF Test</body></html>", encoding="utf-8")
    output = str(tmp_path / "output.pdf")

    proc = _make_proc(returncode=0)

    with patch("tools.mockup_renderer.asyncio.create_subprocess_exec", return_value=proc):
        result = await render_to_pdf(
            html_path=str(html_file),
            output_path=output,
            format="A4",
        )

    assert result == output


@pytest.mark.asyncio
async def test_render_to_pdf_process_error(tmp_path: Path):
    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body>Test</body></html>", encoding="utf-8")

    proc = _make_proc(returncode=1, stderr=b"PDF generation failed")

    with patch("tools.mockup_renderer.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RenderError):
            await render_to_pdf(
                html_path=str(html_file),
                output_path=str(tmp_path / "fail.pdf"),
            )
