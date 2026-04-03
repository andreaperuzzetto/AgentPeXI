from __future__ import annotations

import asyncio
from pathlib import Path

import jinja2
import structlog

from tools import AgentToolError

log = structlog.get_logger()


class PDFGenerationError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_pdf_generation_error", message=message)


def _render_html(template_path: str, context: dict) -> str:
    template_file = Path(template_path)
    loader = jinja2.FileSystemLoader(str(template_file.parent))
    env = jinja2.Environment(loader=loader, autoescape=jinja2.select_autoescape())
    template = env.get_template(template_file.name)
    return template.render(**context)


def _write_pdf(html: str, output_path: str, base_url: str | None) -> str:
    from weasyprint import HTML

    try:
        HTML(string=html, base_url=base_url).write_pdf(output_path)
    except Exception as exc:
        raise PDFGenerationError(str(exc)) from exc
    return output_path


def _write_pdf_bytes(html: str, base_url: str | None) -> bytes:
    from weasyprint import HTML

    try:
        return HTML(string=html, base_url=base_url).write_pdf()
    except Exception as exc:
        raise PDFGenerationError(str(exc)) from exc


async def render_pdf(
    template_path: str,
    context: dict,
    output_path: str,
    base_url: str | None = None,
) -> str:
    try:
        html = _render_html(template_path, context)
    except jinja2.TemplateError as exc:
        raise PDFGenerationError(str(exc)) from exc

    result: str = await asyncio.to_thread(_write_pdf, html, output_path, base_url)
    log.info("pdf_generator.rendered", output_path=output_path)
    return result


async def render_pdf_to_bytes(
    template_path: str,
    context: dict,
    base_url: str | None = None,
) -> bytes:
    try:
        html = _render_html(template_path, context)
    except jinja2.TemplateError as exc:
        raise PDFGenerationError(str(exc)) from exc

    result: bytes = await asyncio.to_thread(_write_pdf_bytes, html, base_url)
    log.info("pdf_generator.rendered_bytes", template_path=template_path)
    return result
