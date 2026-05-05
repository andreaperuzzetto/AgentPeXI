from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from apps.backend.core._wiki._helpers import (
    _parse_frontmatter,
    _slugify,
    _WIKILINK_RE,
    _QUERY_PASS1_SYSTEM,
    _QUERY_PASS2_SYSTEM,
    _LINT_SYSTEM,
)

logger = logging.getLogger("agentpexi.wiki")


class _QueryMixin:
    """Query and lint: tiered retrieval, niche context lookup, health check."""

    wiki_path: Path
    _manifest_lock: asyncio.Lock

    async def query(self, domain: str, query_text: str, llm) -> str:
        """Tiered retrieval in due pass.

        Pass 1 (cheap) — legge solo il frontmatter YAML di ogni file (campo summary:).
          LLM identifica i file rilevanti. Se la risposta è sufficiente dai summary → stop.
        Pass 2 (costoso, solo se necessario) — apre i body completi dei file rilevanti.
          LLM produce sintesi da iniettare nel system prompt di Pepe.
        """
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return ""

        # Pass 1 — solo frontmatter
        summaries: dict[str, str] = {}
        for md_file in sorted(domain_path.rglob("*.md")):
            if md_file.name.startswith("_"):
                continue
            try:
                fm  = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                rel = str(md_file.relative_to(self.wiki_path))
                summaries[rel] = fm.get("summary", "")
            except Exception:
                continue

        if not summaries:
            return ""

        index_snapshot = "\n".join(
            f"{path}: {summary}" for path, summary in summaries.items()
        )
        pass1_user = f"QUERY: {query_text}\n\nINDICE WIKI:\n{index_snapshot}"

        try:
            raw      = await self._llm_call(llm, _QUERY_PASS1_SYSTEM, pass1_user, max_tokens=500)
            # Estrai JSON anche se l'LLM wrappa in ```json
            json_str = re.search(r"\{.*\}", raw, re.DOTALL)
            pass1    = json.loads(json_str.group() if json_str else raw)
        except Exception as exc:
            logger.warning("query Pass 1 failed: %s", exc)
            return ""

        if pass1.get("sufficient_from_summaries") and pass1.get("quick_answer"):
            return str(pass1["quick_answer"])

        # Pass 2 — body completi dei file rilevanti (max 5)
        relevant = pass1.get("relevant_files", [])[:5]
        if not relevant:
            return ""

        bodies: list[str] = []
        for rel_path in relevant:
            full = self.wiki_path / rel_path
            if full.exists():
                bodies.append(f"### {rel_path}\n{full.read_text(encoding='utf-8')}")

        if not bodies:
            return ""

        pass2_user = f"QUERY: {query_text}\n\n{'---'.join(bodies)}"
        try:
            return await self._llm_call(llm, _QUERY_PASS2_SYSTEM, pass2_user, max_tokens=700)
        except Exception as exc:
            logger.warning("query Pass 2 failed: %s", exc)
            return ""

    async def get_niche_context(self, niche: str) -> str | None:
        """Ritorna contenuto wiki/etsy/niches/{slug}.md se esiste, None altrimenti."""
        p = self.wiki_path / "etsy" / "niches" / f"{_slugify(niche)}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    async def lint(self, domain: str, llm) -> str:
        """Health check sulla wiki: sezioni vuote, wikilink rotti, raw pending, suggerimenti."""
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return f"Wiki {domain}: directory non trovata."

        all_slugs: set[str]       = set()
        file_summaries: list[str] = []

        for md_file in sorted(domain_path.rglob("*.md")):
            if md_file.name.startswith("_"):
                continue
            all_slugs.add(md_file.stem)
            try:
                fm = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                file_summaries.append(
                    f"{md_file.relative_to(domain_path)}: "
                    f"updated={fm.get('last_updated', '?')}, "
                    f"confidence={fm.get('confidence', '?')}, "
                    f"summary={fm.get('summary', '(no summary)')[:60]}"
                )
            except Exception:
                file_summaries.append(f"{md_file.name}: (errore lettura)")

        # Wikilink rotti
        broken: list[str] = []
        for md_file in domain_path.rglob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            for link in _WIKILINK_RE.findall(text):
                slug = _slugify(link.split("|")[0])
                if slug not in all_slugs:
                    broken.append(f"  {md_file.name} → [[{link}]]")

        # Manifest — raw non compilati
        manifest = self._read_manifest()
        pending  = [k for k, v in manifest.items() if domain in k and v["compiled_at"] is None]

        snapshot = "\n".join(file_summaries)
        extra: list[str] = []
        if broken:
            extra.append("WIKILINK ROTTI:\n" + "\n".join(broken[:20]))
        if pending:
            extra.append(
                f"RAW NON COMPILATI ({len(pending)}):\n"
                + "\n".join(f"  {p}" for p in pending[:10])
            )

        user = f"WIKI SNAPSHOT ({domain}):\n{snapshot}"
        if extra:
            user += "\n\n" + "\n\n".join(extra)

        try:
            return await self._llm_call(llm, _LINT_SYSTEM, user, max_tokens=800)
        except Exception as exc:
            return f"Lint fallito: {exc}"
