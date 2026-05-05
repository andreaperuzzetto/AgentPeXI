"""DesignAgent — filesystem and PDF utility helpers."""
from __future__ import annotations

import re
from pathlib import Path


def _niche_slug(niche: str, max_len: int = 40) -> str:
    """Converte niche in slug filesystem-safe."""
    slug = re.sub(r"[^a-z0-9]+", "_", niche.lower()).strip("_")
    return slug[:max_len]


def _get_cover_title(niche: str, template: str, research_context: dict | None) -> str:
    """Genera titolo cover che include la keyword primaria per SEO (Intervento 6)."""
    if research_context:
        top_keywords = research_context.get("top_keywords", [])
        if top_keywords:
            primary_keyword = top_keywords[0]
            title = f"{primary_keyword.title()} {template.replace('_', ' ').title()}"
            return title[:60]

    return f"{niche.title()} {template.replace('_', ' ').title()}"[:60]


def _count_pdf_pages(pdf_path: Path) -> int:
    """Conta le pagine di un PDF generato."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception:
        return 0
