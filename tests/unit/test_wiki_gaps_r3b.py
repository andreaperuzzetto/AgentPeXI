"""tests/unit/test_wiki_gaps_r3b.py — gap coverage per i 3 wiki mixin (≥92%).

Copre le righe non coperte in:
  - _compile_mixin.py  : 127, 136-137, 199, 201, 219-221, 229-276
  - _maintenance_mixin.py : 40, 51-52, 69-74
  - _query_mixin.py    : 43, 48-49, 74, 88-90, 108, 118-119, 126-128, 137, 139, 146
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.core._wiki._io_mixin import _IOMixin
from apps.backend.core._wiki._query_mixin import _QueryMixin
from apps.backend.core._wiki._compile_mixin import _CompileMixin
from apps.backend.core._wiki._maintenance_mixin import _MaintenanceMixin


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class FakeWiki(_IOMixin, _QueryMixin, _CompileMixin, _MaintenanceMixin):
    pass


@pytest.fixture
def wiki(tmp_path):
    obj = FakeWiki()
    obj.base_path     = tmp_path
    obj.wiki_path     = tmp_path / "wiki"
    obj.raw_path      = tmp_path / "raw"
    obj._manifest_lock = asyncio.Lock()
    obj.wiki_path.mkdir(parents=True, exist_ok=True)
    obj.raw_path.mkdir(parents=True, exist_ok=True)
    return obj


def make_llm(response: str = "mocked output"):
    """Mock LLM via percorso OpenAI-compat (llm.chat.completions.create)."""
    llm = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = response
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    llm.chat.completions.create = AsyncMock(return_value=mock_resp)
    return llm


# ══════════════════════════════════════════════════════════════════════════════
# _CompileMixin — righe scoperte: 127, 136-137, 199, 201, 219-221, 229-276
# ══════════════════════════════════════════════════════════════════════════════

class TestCompileMixinGaps:

    # ── update_index ──────────────────────────────────────────────────────────

    async def test_update_index_skips_underscore_file(self, wiki):
        """Riga 127 — update_index esegue continue quando il file inizia con _"""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "_notes.md").write_text("# Notes (must be skipped)")
        (d / "normal.md").write_text("---\nsummary: normal niche\n---\n# Normal")
        await asyncio.wait_for(wiki.update_index("etsy", None), timeout=5)
        idx = (d / "_index.md").read_text()
        assert "normal" in idx
        assert "notes" not in idx

    async def test_update_index_parse_error_continues(self, wiki):
        """Righe 136-137 — update_index cattura eccezioni nel frontmatter e continua."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "good.md").write_text("---\nsummary: ok\n---\n# Good")
        with patch(
            "apps.backend.core._wiki._compile_mixin._parse_frontmatter",
            side_effect=Exception("parse error"),
        ):
            await asyncio.wait_for(wiki.update_index("etsy", None), timeout=5)
        idx = (d / "_index.md").read_text()
        assert "0 articoli" in idx

    # ── cleanup_orphan_raw ────────────────────────────────────────────────────

    async def test_cleanup_compiled_entry_is_skipped(self, wiki):
        """Riga 199 — entry con compiled_at != None viene saltata interamente."""
        rel = "raw/etsy/research/20200101T000000_done.json"
        wiki._write_manifest(
            {rel: {"compiled_at": "2024-01-01T00:00:00+00:00", "wiki_files_updated": []}}
        )
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result == {"compiled": 0, "deleted": 0, "skipped": 0, "errors": []}

    async def test_cleanup_wrong_domain_is_skipped(self, wiki):
        """Riga 201 — entry con dominio diverso viene saltata."""
        rel = "raw/personal/analytics/20200101T000000_raw.json"
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result == {"compiled": 0, "deleted": 0, "skipped": 0, "errors": []}

    async def test_cleanup_bad_filename_falls_back_to_mtime(self, wiki):
        """Righe 219-221 — filename non ha timestamp → fallback su mtime.
        File vecchio (>30 gg) senza niche → viene eliminato (righe 261-270).
        """
        agent_dir = wiki.raw_path / "etsy" / "research"
        agent_dir.mkdir(parents=True)
        raw_file = agent_dir / "badname_nodate.json"
        raw_file.write_text(json.dumps({}))
        old_ts = time.time() - 41 * 24 * 3600  # 41 giorni fa
        os.utime(raw_file, (old_ts, old_ts))
        rel = str(raw_file.relative_to(wiki.base_path))
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result["deleted"] == 1

    async def test_cleanup_json_read_error_appends_to_errors(self, wiki):
        """Righe 231-234 — cleanup cattura errore lettura JSON e lo registra in errors."""
        agent_dir = wiki.raw_path / "etsy" / "research"
        agent_dir.mkdir(parents=True)
        raw_file = agent_dir / "20200101T000000_corrupt.json"
        raw_file.write_text("NOT VALID JSON }{")
        rel = str(raw_file.relative_to(wiki.base_path))
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert len(result["errors"]) == 1
        assert result["errors"][0].startswith("read:")

    async def test_cleanup_compiles_old_etsy_niche(self, wiki):
        """Righe 244-259 — file etsy vecchio con niche → compilazione forzata."""
        (wiki.wiki_path / "etsy" / "niches").mkdir(parents=True)
        agent_dir = wiki.raw_path / "etsy" / "research"
        agent_dir.mkdir(parents=True)
        raw_file = agent_dir / "20200101T000000_my-niche.json"
        raw_file.write_text(json.dumps({"niche": "my niche", "data": "test"}))
        rel = str(raw_file.relative_to(wiki.base_path))
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm("# My Niche")), timeout=10
        )
        assert result["compiled"] == 1
        assert result["deleted"] == 0
        assert result["errors"] == []

    async def test_cleanup_deletes_old_file_without_niche(self, wiki):
        """Righe 261-270 — file vecchio senza niche → eliminato dal filesystem."""
        agent_dir = wiki.raw_path / "etsy" / "research"
        agent_dir.mkdir(parents=True)
        raw_file = agent_dir / "20200101T000000_noniche.json"
        raw_file.write_text(json.dumps({"other": "data"}))
        rel = str(raw_file.relative_to(wiki.base_path))
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result["deleted"] == 1
        assert not raw_file.exists()


# ══════════════════════════════════════════════════════════════════════════════
# _MaintenanceMixin — righe scoperte: 40, 51-52, 69-74
# ══════════════════════════════════════════════════════════════════════════════

class TestMaintenanceMixinGaps:

    async def test_compact_wiki_personal_domain_uses_personal_limit(self, wiki):
        """Riga 40 — compact_wiki usa PERSONAL_HARD_LIMIT per il dominio personal."""
        d = wiki.wiki_path / "personal"
        d.mkdir(parents=True)
        (d / "profile.md").write_text("# Profile\nFew words only.")
        result = await asyncio.wait_for(wiki.compact_wiki("personal", make_llm()), timeout=5)
        assert "profile.md" in result["skipped"]
        assert result["compacted"] == []

    async def test_compact_wiki_unreadable_file_is_caught(self, wiki):
        """Righe 51-52 — compact_wiki cattura eccezioni di lettura senza propagarle."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        wiki_file = d / "locked.md"
        wiki_file.write_text("# content")
        os.chmod(wiki_file, 0o000)
        try:
            result = await asyncio.wait_for(wiki.compact_wiki("etsy", make_llm()), timeout=5)
            assert result["compacted"] == []
            assert result["skipped"] == []
        finally:
            os.chmod(wiki_file, 0o644)

    async def test_distill_file_llm_error_restores_original(self, wiki):
        """Righe 69-74 — _distill_file ripristina il file originale dal .bak se LLM fallisce."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        wiki_file = d / "article.md"
        original_content = "# Original content"
        wiki_file.write_text(original_content)
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=RuntimeError("LLM down"))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                wiki._distill_file(wiki_file, llm, target=500), timeout=5
            )
        assert wiki_file.read_text() == original_content
        assert not wiki_file.with_suffix(".md.bak").exists()


# ══════════════════════════════════════════════════════════════════════════════
# _QueryMixin — righe scoperte: 43, 48-49, 74, 88-90, 108, 118-119, 126-128,
#                               137, 139, 146
# ══════════════════════════════════════════════════════════════════════════════

class TestQueryMixinGaps:

    # ── query() ───────────────────────────────────────────────────────────────

    async def test_query_skips_underscore_files_in_pass1(self, wiki):
        """Riga 43 — query non include i file con prefisso _ nei summary di pass1."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "_index.md").write_text("---\nsummary: index\n---\n# Index")
        (d / "niche.md").write_text("---\nsummary: a niche\n---\n# Niche")
        pass1 = json.dumps({
            "relevant_files": [],
            "sufficient_from_summaries": True,
            "quick_answer": "answer from summaries",
        })
        result = await asyncio.wait_for(
            wiki.query("etsy", "test query", make_llm(response=pass1)), timeout=5
        )
        assert result == "answer from summaries"

    async def test_query_frontmatter_exception_continues(self, wiki):
        """Righe 48-49 — query esegue continue quando _parse_frontmatter solleva eccezione."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "file.md").write_text("# content")
        with patch(
            "apps.backend.core._wiki._query_mixin._parse_frontmatter",
            side_effect=Exception("bad parse"),
        ):
            result = await asyncio.wait_for(
                wiki.query("etsy", "q", make_llm()), timeout=5
            )
        assert result == ""

    async def test_query_empty_relevant_files_returns_empty(self, wiki):
        """Riga 74 — query ritorna "" quando pass1 non restituisce relevant_files."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche.md").write_text("---\nsummary: a niche\n---\n# Niche")
        pass1 = json.dumps({
            "relevant_files": [],
            "sufficient_from_summaries": False,
            "quick_answer": None,
        })
        result = await asyncio.wait_for(
            wiki.query("etsy", "q", make_llm(response=pass1)), timeout=5
        )
        assert result == ""

    async def test_query_pass2_llm_error_returns_empty(self, wiki):
        """Righe 88-90 — query ritorna "" quando pass2 LLM solleva eccezione."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche.md").write_text("---\nsummary: great\n---\n# Niche body text")
        pass1_json = json.dumps({
            "relevant_files": ["etsy/niche.md"],
            "sufficient_from_summaries": False,
            "quick_answer": None,
        })
        resp1 = MagicMock()
        resp1.choices = [MagicMock()]
        resp1.choices[0].message.content = pass1_json
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(
            side_effect=[resp1, RuntimeError("pass2 down")]
        )
        result = await asyncio.wait_for(wiki.query("etsy", "q", llm), timeout=5)
        assert result == ""

    # ── lint() ────────────────────────────────────────────────────────────────

    async def test_lint_skips_underscore_files(self, wiki):
        """Riga 108 — lint esegue continue per i file con prefisso _ nella scansione."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "_system.md").write_text("# System notes")
        (d / "niche.md").write_text(
            "---\nsummary: t\nlast_updated: 2024-01-01\nconfidence: 0.9\n---\n# Niche"
        )
        result = await asyncio.wait_for(wiki.lint("etsy", make_llm("lint ok")), timeout=5)
        assert result == "lint ok"

    async def test_lint_parse_error_appends_error_entry(self, wiki):
        """Righe 118-119 — lint aggiunge voce di errore quando _parse_frontmatter fallisce."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "bad.md").write_text("# some content without wikilinks")
        with patch(
            "apps.backend.core._wiki._query_mixin._parse_frontmatter",
            side_effect=Exception("bad"),
        ):
            result = await asyncio.wait_for(
                wiki.lint("etsy", make_llm("report")), timeout=5
            )
        assert result == "report"

    async def test_lint_broken_wikilink_detected(self, wiki):
        """Righe 126-128, 137, 146 — lint rileva wikilink rotti e li include nel prompt LLM."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche.md").write_text(
            "---\nsummary: t\nlast_updated: 2024-01-01\nconfidence: 0.9\n---\n"
            "# Niche\n\n[[ghost-link]]"
        )
        result = await asyncio.wait_for(
            wiki.lint("etsy", make_llm("lint report")), timeout=5
        )
        assert result == "lint report"

    async def test_lint_pending_raw_in_extra(self, wiki):
        """Righe 139, 146 — lint include i raw non compilati nel prompt LLM."""
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche.md").write_text(
            "---\nsummary: t\nlast_updated: 2024-01-01\nconfidence: 0.9\n---\n# Niche"
        )
        wiki._write_manifest({
            "raw/etsy/research/20240101T120000_raw.json": {
                "compiled_at": None,
                "wiki_files_updated": [],
            }
        })
        result = await asyncio.wait_for(
            wiki.lint("etsy", make_llm("lint report")), timeout=5
        )
        assert result == "lint report"
