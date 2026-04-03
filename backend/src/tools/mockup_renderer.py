from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import structlog

from tools import AgentToolError

log = structlog.get_logger()

VIEWPORT_DESKTOP = {"width": 1440, "height": 900}
VIEWPORT_MOBILE = {"width": 390, "height": 844}

_RENDER_TIMEOUT = 60.0


class RenderTimeoutError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_render_timeout", message=message)


class RenderError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_render_error", message=message)


def _script_path() -> str:
    if "RENDER_SCRIPT_PATH" in os.environ:
        return os.environ["RENDER_SCRIPT_PATH"]
    base = Path(__file__).parent.parent.parent.parent  # repo root
    return str(base / "scripts" / "render.js")


async def _run_render(payload: dict) -> None:
    script = _script_path()
    node_bin = os.environ.get("NODE_PATH", "node")

    proc = await asyncio.create_subprocess_exec(
        node_bin,
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdin_data = json.dumps(payload).encode()

    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=_RENDER_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RenderTimeoutError(
            f"Puppeteer did not complete within {int(_RENDER_TIMEOUT)}s"
        ) from exc

    if proc.returncode != 0:
        error_msg = stderr.decode(errors="replace").strip()
        raise RenderError(error_msg)


async def render_to_png(
    html_path: str,
    output_path: str,
    viewport_width: int = 1440,
    viewport_height: int = 900,
    device_scale_factor: float = 2.0,
) -> str:
    html_content = Path(html_path).read_text(encoding="utf-8")
    payload = {
        "html": html_content,
        "output_path": output_path,
        "format": "png",
        "viewport": {
            "width": viewport_width,
            "height": viewport_height,
            "deviceScaleFactor": device_scale_factor,
        },
    }
    await _run_render(payload)
    log.info("mockup_renderer.png", output_path=output_path)
    return output_path


async def render_to_pdf(
    html_path: str,
    output_path: str,
    format: str = "A4",
    margin: dict | None = None,
    print_background: bool = True,
) -> str:
    html_content = Path(html_path).read_text(encoding="utf-8")
    pdf_options: dict = {
        "format": format,
        "printBackground": print_background,
    }
    if margin is not None:
        pdf_options["margin"] = margin

    payload = {
        "html": html_content,
        "output_path": output_path,
        "format": "pdf",
        "pdf_options": pdf_options,
    }
    await _run_render(payload)
    log.info("mockup_renderer.pdf", output_path=output_path)
    return output_path
