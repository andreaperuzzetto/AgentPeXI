"""tests/core/test_wiki_infra_coverage.py — ~90 pytest-asyncio tests.

Parti:
  1. core/_wiki/ mixins (_IOMixin, _QueryMixin, _CompileMixin, _MaintenanceMixin)
  2. core/storage.py — StorageManager
  3. core/task_registry.py — TaskRegistry
  4. core/finance_tracker.py — FinanceTracker (metodi non ancora coperti)
"""
from __future__ import annotations

import asyncio
import json
import time as _time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import anthropic
import pytest

from apps.backend.core._wiki._io_mixin import _IOMixin
from apps.backend.core._wiki._query_mixin import _QueryMixin
from apps.backend.core._wiki._compile_mixin import _CompileMixin
from apps.backend.core._wiki._maintenance_mixin import _MaintenanceMixin
from apps.backend.core.storage import StorageManager
from apps.backend.core.task_registry import TaskRegistry
from apps.backend.core.finance_tracker import FinanceTracker

# ─────────────────────────────────────────────────────────────────────────────
# Helpers comuni
# ─────────────────────────────────────────────────────────────────────────────

class FakeWiki(_IOMixin, _QueryMixin, _CompileMixin, _MaintenanceMixin):
    pass


@pytest.fixture
def wiki(tmp_path):
    obj = FakeWiki()
    obj.base_path = tmp_path
    obj.wiki_path = tmp_path / "wiki"
    obj.raw_path  = tmp_path / "raw"
    obj._manifest_lock = asyncio.Lock()
    obj.wiki_path.mkdir(parents=True, exist_ok=True)
    obj.raw_path.mkdir(parents=True, exist_ok=True)
    return obj


def make_llm(response: str = "mocked output"):
    """Mock LLM — prende il percorso OpenAI-compat (llm.chat.completions.create)."""
    llm = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = response
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    llm.chat.completions.create = AsyncMock(return_value=mock_resp)
    return llm


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 1 — _IOMixin  (16 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestIOMixin:

    async def test_store_raw_creates_file(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {"key": "val"}), timeout=5
        )
        assert p.exists()
        assert p.suffix == ".json"

    async def test_store_raw_returns_path_object(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {}), timeout=5
        )
        assert isinstance(p, Path)

    async def test_store_raw_file_in_correct_dir(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {}), timeout=5
        )
        assert p.parent == wiki.raw_path / "etsy" / "research"

    async def test_store_raw_updates_manifest(self, wiki):
        await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {"niche": "test"}), timeout=5
        )
        manifest = wiki._read_manifest()
        assert len(manifest) == 1
        entry = list(manifest.values())[0]
        assert entry["compiled_at"] is None
        assert entry["wiki_files_updated"] == []

    async def test_store_raw_manifest_key_is_relative(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {}), timeout=5
        )
        manifest = wiki._read_manifest()
        rel = str(p.relative_to(wiki.base_path))
        assert rel in manifest

    async def test_store_raw_without_niche_or_query_slug_raw(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {}), timeout=5
        )
        assert "_raw.json" in p.name

    async def test_store_raw_with_niche_uses_slug(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "research", {"niche": "mandala wall art"}),
            timeout=5,
        )
        assert "mandala" in p.name

    async def test_store_raw_with_query_uses_slug(self, wiki):
        p = await asyncio.wait_for(
            wiki.store_raw("etsy", "analytics", {"query": "best sellers"}),
            timeout=5,
        )
        assert "best" in p.name

    async def test_read_manifest_missing(self, wiki):
        result = wiki._read_manifest()
        assert result == {}

    async def test_read_manifest_existing(self, wiki):
        data = {"raw/etsy/r/x.json": {"compiled_at": None, "wiki_files_updated": []}}
        (wiki.base_path / ".manifest.json").write_text(json.dumps(data))
        result = wiki._read_manifest()
        assert result == data

    async def test_write_manifest_roundtrip(self, wiki):
        data = {"a/b.json": {"compiled_at": "2024-01-01", "wiki_files_updated": []}}
        wiki._write_manifest(data)
        result = wiki._read_manifest()
        assert result == data

    async def test_iter_wiki_files_empty_domain(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        assert list(wiki._iter_wiki_files("etsy")) == []

    async def test_iter_wiki_files_with_md_files(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("# Niche 1")
        (d / "niche2.md").write_text("# Niche 2")
        files = list(wiki._iter_wiki_files("etsy"))
        assert len(files) == 2

    async def test_iter_wiki_files_excludes_underscore(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "normal.md").write_text("ok")
        (d / "_index.md").write_text("index")
        files = list(wiki._iter_wiki_files("etsy"))
        assert len(files) == 1
        assert files[0].name == "normal.md"

    async def test_iter_wiki_files_missing_domain(self, wiki):
        assert list(wiki._iter_wiki_files("nonexistent")) == []

    async def test_get_stats_empty(self, wiki):
        result = await asyncio.wait_for(wiki.get_stats(), timeout=5)
        assert "etsy_niches"   in result
        assert "etsy_patterns" in result
        assert "pending_raw"   in result
        assert "total_raw"     in result
        assert result["etsy_niches"] == 0
        assert result["total_raw"]   == 0

    async def test_get_stats_after_store_raw(self, wiki):
        await asyncio.wait_for(wiki.store_raw("etsy", "research", {}), timeout=5)
        result = await asyncio.wait_for(wiki.get_stats(), timeout=5)
        assert result["total_raw"]   >= 1
        assert result["pending_raw"] >= 1

    async def test_get_stats_with_niches(self, wiki):
        niches_dir = wiki.wiki_path / "etsy" / "niches"
        niches_dir.mkdir(parents=True)
        (niches_dir / "myniche.md").write_text("# Niche")
        result = await asyncio.wait_for(wiki.get_stats(), timeout=5)
        assert result["etsy_niches"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 1 — _QueryMixin  (13 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestQueryMixin:

    async def test_get_niche_context_existing(self, wiki):
        p = wiki.wiki_path / "etsy" / "niches"
        p.mkdir(parents=True)
        (p / "my-niche.md").write_text("niche content")
        result = await asyncio.wait_for(wiki.get_niche_context("my niche"), timeout=5)
        assert result == "niche content"

    async def test_get_niche_context_missing(self, wiki):
        result = await asyncio.wait_for(wiki.get_niche_context("nonexistent niche"), timeout=5)
        assert result is None

    async def test_query_domain_not_exists(self, wiki):
        result = await asyncio.wait_for(
            wiki.query("etsy", "what sells?", make_llm()), timeout=5
        )
        assert result == ""

    async def test_query_empty_domain_dir(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        result = await asyncio.wait_for(
            wiki.query("etsy", "what sells?", make_llm()), timeout=5
        )
        assert result == ""

    async def test_query_pass1_sufficient_returns_quick_answer(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great niche\n---\n# Niche 1")
        pass1 = json.dumps({
            "relevant_files": [],
            "sufficient_from_summaries": True,
            "quick_answer": "the quick answer",
        })
        result = await asyncio.wait_for(
            wiki.query("etsy", "what sells?", make_llm(response=pass1)), timeout=5
        )
        assert result == "the quick answer"

    async def test_query_pass1_returns_string_type(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great niche\n---\n# Body")
        pass1 = json.dumps({
            "relevant_files": [],
            "sufficient_from_summaries": True,
            "quick_answer": "answer",
        })
        result = await asyncio.wait_for(
            wiki.query("etsy", "q", make_llm(response=pass1)), timeout=5
        )
        assert isinstance(result, str)

    async def test_query_pass1_not_sufficient_triggers_pass2(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great\n---\n# Niche body text")

        pass1_json = json.dumps({
            "relevant_files": ["etsy/niche1.md"],
            "sufficient_from_summaries": False,
            "quick_answer": None,
        })
        resp1 = MagicMock()
        resp1.choices = [MagicMock()]
        resp1.choices[0].message.content = pass1_json
        resp2 = MagicMock()
        resp2.choices = [MagicMock()]
        resp2.choices[0].message.content = "synthesized answer"

        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=[resp1, resp2])

        result = await asyncio.wait_for(
            wiki.query("etsy", "what sells?", llm), timeout=5
        )
        assert result == "synthesized answer"

    async def test_query_pass1_json_parse_error(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great\n---\n# Niche 1")
        result = await asyncio.wait_for(
            wiki.query("etsy", "what sells?", make_llm(response="not valid json")),
            timeout=5,
        )
        assert result == ""

    async def test_query_relevant_files_not_on_disk(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great\n---\n# Niche 1")
        pass1 = json.dumps({
            "relevant_files": ["etsy/ghost.md"],
            "sufficient_from_summaries": False,
            "quick_answer": None,
        })
        result = await asyncio.wait_for(
            wiki.query("etsy", "q", make_llm(response=pass1)), timeout=5
        )
        assert result == ""

    async def test_lint_domain_not_exists(self, wiki):
        result = await asyncio.wait_for(wiki.lint("etsy", make_llm()), timeout=5)
        assert "etsy" in result
        assert len(result) > 0

    async def test_lint_empty_domain_calls_llm(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        llm = make_llm("lint report from llm")
        result = await asyncio.wait_for(wiki.lint("etsy", llm), timeout=5)
        assert result == "lint report from llm"
        llm.chat.completions.create.assert_called_once()

    async def test_lint_with_files_calls_llm(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text(
            "---\nsummary: test\nlast_updated: 2024-01-01\nconfidence: 0.8\n---\n# Niche 1"
        )
        result = await asyncio.wait_for(
            wiki.lint("etsy", make_llm("detailed lint report")), timeout=5
        )
        assert result == "detailed lint report"

    async def test_lint_llm_exception_returns_error_string(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=RuntimeError("llm down"))
        result = await asyncio.wait_for(wiki.lint("etsy", llm), timeout=5)
        assert "Lint fallito" in result


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 1 — _CompileMixin  (17 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestCompileMixin:

    async def test_compile_niche_creates_new_file(self, wiki):
        (wiki.wiki_path / "etsy" / "niches").mkdir(parents=True)
        await asyncio.wait_for(
            wiki.compile_niche("my niche", "research", {"data": "value"}, make_llm("# New Niche")),
            timeout=5,
        )
        niche_file = wiki.wiki_path / "etsy" / "niches" / "my-niche.md"
        assert niche_file.exists()
        assert niche_file.read_text() == "# New Niche"

    async def test_compile_niche_overwrites_existing(self, wiki):
        niches_dir = wiki.wiki_path / "etsy" / "niches"
        niches_dir.mkdir(parents=True)
        (niches_dir / "my-niche.md").write_text("# Old Content")
        await asyncio.wait_for(
            wiki.compile_niche("my niche", "research", {}, make_llm("# Updated")),
            timeout=5,
        )
        assert (niches_dir / "my-niche.md").read_text() == "# Updated"

    async def test_compile_niche_llm_exception_does_not_propagate(self, wiki):
        (wiki.wiki_path / "etsy" / "niches").mkdir(parents=True)
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=RuntimeError("LLM error"))
        # Must NOT raise
        await asyncio.wait_for(
            wiki.compile_niche("my niche", "research", {}, llm), timeout=5
        )

    async def test_compile_niche_updates_manifest(self, wiki):
        (wiki.wiki_path / "etsy" / "niches").mkdir(parents=True)
        raw_rel = "raw/etsy/research/20240101T120000_my-niche.json"
        wiki._write_manifest({raw_rel: {"compiled_at": None, "wiki_files_updated": []}})
        await asyncio.wait_for(
            wiki.compile_niche("my niche", "research", {}, make_llm("# Niche")),
            timeout=5,
        )
        assert wiki._read_manifest()[raw_rel]["compiled_at"] is not None

    async def test_compile_wiki_file_creates_file(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        await asyncio.wait_for(
            wiki.compile_wiki_file("etsy", "patterns/seasonal", "new content", make_llm("# New")),
            timeout=5,
        )
        result_file = wiki.wiki_path / "etsy" / "patterns" / "seasonal.md"
        assert result_file.exists()
        assert result_file.read_text() == "# New"

    async def test_compile_wiki_file_merges_existing(self, wiki):
        target = wiki.wiki_path / "etsy" / "meta.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Existing")
        await asyncio.wait_for(
            wiki.compile_wiki_file("etsy", "meta", "new info", make_llm("# Merged")),
            timeout=5,
        )
        assert target.read_text() == "# Merged"

    async def test_compile_wiki_file_llm_exception_does_not_propagate(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=RuntimeError("fail"))
        await asyncio.wait_for(
            wiki.compile_wiki_file("etsy", "patterns", "content", llm), timeout=5
        )

    async def test_compile_wiki_file_personal_domain(self, wiki):
        (wiki.wiki_path / "personal").mkdir(parents=True)
        await asyncio.wait_for(
            wiki.compile_wiki_file("personal", "profile", "my info", make_llm("# Personal")),
            timeout=5,
        )
        assert (wiki.wiki_path / "personal" / "profile.md").exists()

    async def test_compile_wiki_file_updates_manifest(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        raw_rel = "raw/etsy/analytics/20240101T120000_raw.json"
        wiki._write_manifest({raw_rel: {"compiled_at": None, "wiki_files_updated": []}})
        await asyncio.wait_for(
            wiki.compile_wiki_file("etsy", "meta", "info", make_llm("# Meta")),
            timeout=5,
        )
        assert wiki._read_manifest()[raw_rel]["compiled_at"] is not None

    async def test_update_index_creates_index_file(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "niche1.md").write_text("---\nsummary: great niche\n---\n# Niche 1")
        await asyncio.wait_for(wiki.update_index("etsy", None), timeout=5)
        assert (d / "_index.md").exists()
        assert "niche1" in (d / "_index.md").read_text()

    async def test_update_index_empty_domain_creates_index(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        await asyncio.wait_for(wiki.update_index("etsy", None), timeout=5)
        assert (d / "_index.md").exists()
        assert "0 articoli" in (d / "_index.md").read_text()

    async def test_update_index_missing_domain_no_error(self, wiki):
        await asyncio.wait_for(wiki.update_index("nonexistent", None), timeout=5)

    async def test_cleanup_orphan_raw_empty_manifest(self, wiki):
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result["compiled"] == 0
        assert result["deleted"]  == 0
        assert result["skipped"]  == 0
        assert result["errors"]   == []

    async def test_cleanup_orphan_raw_fresh_file_is_skipped(self, wiki):
        agent_dir = wiki.raw_path / "etsy" / "research"
        agent_dir.mkdir(parents=True)
        raw_file = agent_dir / "20991231T235959_fresh.json"
        raw_file.write_text(json.dumps({"niche": "fresh niche"}))
        rel = str(raw_file.relative_to(wiki.base_path))
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result["skipped"] == 1

    async def test_cleanup_orphan_raw_missing_file_is_deleted(self, wiki):
        rel = "raw/etsy/research/20200101T000000_gone.json"
        wiki._write_manifest({rel: {"compiled_at": None, "wiki_files_updated": []}})
        result = await asyncio.wait_for(
            wiki.cleanup_orphan_raw("etsy", make_llm()), timeout=5
        )
        assert result["deleted"] == 1
        assert rel not in wiki._read_manifest()

    async def test_build_compile_niche_user_contains_niche(self, wiki):
        user_str = wiki._build_compile_niche_user("my niche", "research", {"k": "v"}, "")
        assert "my niche" in user_str
        assert len(user_str) > 0

    async def test_build_compile_niche_user_new_file_hint(self, wiki):
        user_str = wiki._build_compile_niche_user("test niche", "analytics", {}, "")
        assert "NON ESISTE" in user_str

    async def test_build_compile_niche_user_existing_file(self, wiki):
        user_str = wiki._build_compile_niche_user("n", "analytics", {}, "# existing content")
        assert "# existing content" in user_str


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 1 — _MaintenanceMixin  (8 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestMaintenanceMixin:

    async def test_compact_wiki_empty_domain_dir(self, wiki):
        (wiki.wiki_path / "etsy").mkdir(parents=True)
        result = await asyncio.wait_for(wiki.compact_wiki("etsy", make_llm()), timeout=5)
        assert result == {"compacted": [], "skipped": []}

    async def test_compact_wiki_missing_domain(self, wiki):
        result = await asyncio.wait_for(wiki.compact_wiki("nonexistent", make_llm()), timeout=5)
        assert result == {"compacted": [], "skipped": []}

    async def test_compact_wiki_short_file_is_skipped(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        (d / "short.md").write_text("# Short\nA few words only.")
        result = await asyncio.wait_for(wiki.compact_wiki("etsy", make_llm()), timeout=5)
        assert "short.md" in result["skipped"]
        assert result["compacted"] == []

    async def test_compact_wiki_long_file_calls_distill(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        # len(words) * 1.33 > 2000 requires ~1504 words → use 2000
        (d / "bigfile.md").write_text("word " * 2000)
        wiki._distill_file = AsyncMock()
        result = await asyncio.wait_for(wiki.compact_wiki("etsy", make_llm()), timeout=5)
        wiki._distill_file.assert_called_once()
        assert "bigfile.md" in result["compacted"]

    async def test_llm_call_openai_path_returns_content(self, wiki):
        llm = make_llm("openai response")
        result = await asyncio.wait_for(
            wiki._llm_call(llm, "system prompt", "user message"), timeout=5
        )
        assert result == "openai response"
        llm.chat.completions.create.assert_called_once()

    async def test_llm_call_anthropic_path_returns_text(self, wiki):
        # MagicMock(spec=anthropic.AsyncAnthropic) → isinstance(...) ritorna True
        llm = MagicMock(spec=anthropic.AsyncAnthropic)
        mock_text = MagicMock()
        mock_text.text = "anthropic response"
        mock_msg = MagicMock()
        mock_msg.content = [mock_text]
        llm.messages.create = AsyncMock(return_value=mock_msg)
        result = await asyncio.wait_for(
            wiki._llm_call(llm, "system", "user"), timeout=5
        )
        assert result == "anthropic response"

    async def test_llm_call_returns_string_type(self, wiki):
        result = await asyncio.wait_for(
            wiki._llm_call(make_llm("hello"), "sys", "usr", max_tokens=100), timeout=5
        )
        assert isinstance(result, str)

    async def test_distill_file_writes_distilled_content(self, wiki):
        d = wiki.wiki_path / "etsy"
        d.mkdir(parents=True)
        wiki_file = d / "bigfile.md"
        wiki_file.write_text("# Original\n" + "word " * 100)
        await asyncio.wait_for(
            wiki._distill_file(wiki_file, make_llm("# Distilled"), target=500), timeout=5
        )
        assert wiki_file.read_text() == "# Distilled"
        assert not wiki_file.with_suffix(".md.bak").exists()


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 2 — StorageManager  (15 test)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sm(tmp_path):
    return StorageManager(base_path=str(tmp_path))


class TestStorageManager:

    def test_ensure_dirs_creates_subdirs(self, sm, tmp_path):
        sm.ensure_dirs()
        assert (tmp_path / "pending").is_dir()
        assert (tmp_path / "uploaded").is_dir()
        assert (tmp_path / "archived").is_dir()

    def test_get_pending_path_basic(self, sm, tmp_path):
        sm.ensure_dirs()
        p = sm.get_pending_path("file.txt")
        assert p == (tmp_path / "pending" / "file.txt").resolve()

    def test_get_pending_path_traversal_raises(self, sm):
        sm.ensure_dirs()
        with pytest.raises(ValueError, match="path traversal"):
            sm.get_pending_path("../../../etc/passwd")

    def test_move_to_uploaded_returns_path(self, sm, tmp_path):
        sm.ensure_dirs()
        src = tmp_path / "pending" / "test.pdf"
        src.write_text("content")
        dest = sm.move_to_uploaded(src)
        assert dest == tmp_path / "uploaded" / "test.pdf"
        assert dest.exists()
        assert not src.exists()

    def test_move_to_archived_returns_path(self, sm, tmp_path):
        sm.ensure_dirs()
        src = tmp_path / "pending" / "test.pdf"
        src.write_text("content")
        dest = sm.move_to_archived(src)
        assert dest == tmp_path / "archived" / "test.pdf"
        assert dest.exists()
        assert not src.exists()

    def test_archive_old_files_moves_files(self, sm, tmp_path):
        sm.ensure_dirs()
        f = tmp_path / "uploaded" / "old.pdf"
        f.write_text("old content")
        # days=-1 → cutoff è domani → tutti i file risultano "vecchi"
        count = sm.archive_old_files(days=-1)
        assert count == 1
        assert (tmp_path / "archived" / "old.pdf").exists()

    def test_archive_old_files_no_old_files_returns_zero(self, sm, tmp_path):
        sm.ensure_dirs()
        (tmp_path / "uploaded" / "new.pdf").write_text("new content")
        count = sm.archive_old_files(days=9999)
        assert count == 0

    def test_base_path_property_returns_path(self, sm, tmp_path):
        assert sm.base_path == tmp_path
        assert isinstance(sm.base_path, Path)

    def test_is_available_existing_dir(self, sm):
        assert sm.is_available() is True

    def test_get_disk_usage_has_required_keys(self, sm):
        usage = sm.get_disk_usage()
        assert "total" in usage
        assert "used"  in usage
        assert "free"  in usage
        assert usage["total"] > 0

    def test_list_pending_empty(self, sm):
        sm.ensure_dirs()
        assert sm.list_pending() == []

    def test_list_pending_with_files(self, sm, tmp_path):
        sm.ensure_dirs()
        (tmp_path / "pending" / "a.pdf").write_text("a")
        (tmp_path / "pending" / "b.pdf").write_text("b")
        result = sm.list_pending()
        assert len(result) == 2
        assert all(isinstance(p, Path) for p in result)

    def test_list_uploaded_empty(self, sm):
        sm.ensure_dirs()
        assert sm.list_uploaded() == []

    def test_list_uploaded_with_file(self, sm, tmp_path):
        sm.ensure_dirs()
        (tmp_path / "uploaded" / "done.pdf").write_text("done")
        result = sm.list_uploaded()
        assert len(result) == 1
        assert result[0].name == "done.pdf"

    def test_health_check_returns_available_true(self, sm):
        result = sm.health_check()
        assert "available"     in result
        assert "free_gb"       in result
        assert "pending_count" in result
        assert result["available"] is True


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 3 — TaskRegistry  (8 test)
# ═════════════════════════════════════════════════════════════════════════════

class TestTaskRegistry:

    async def test_create_task_returns_asyncio_task(self):
        registry = TaskRegistry()
        async def dummy(): pass
        task = registry.create_task(dummy())
        assert isinstance(task, asyncio.Task)
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)

    async def test_create_task_with_name(self):
        registry = TaskRegistry()
        async def dummy(): pass
        task = registry.create_task(dummy(), name="mytask")
        assert task.get_name() == "mytask"
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)

    async def test_create_task_registers_in_set(self):
        registry = TaskRegistry()
        async def slow(): await asyncio.sleep(0.05)
        task = registry.create_task(slow())
        assert task in registry._tasks
        await asyncio.wait_for(task, timeout=5)

    async def test_on_done_removes_task_after_completion(self):
        registry = TaskRegistry()
        async def dummy(): pass
        task = registry.create_task(dummy())
        await asyncio.wait_for(task, timeout=5)
        await asyncio.sleep(0)          # pump event loop so callbacks run
        assert task not in registry._tasks

    async def test_on_done_called_on_exception(self):
        registry = TaskRegistry()
        async def failing(): raise ValueError("boom")
        task = registry.create_task(failing())
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert task not in registry._tasks

    async def test_shutdown_cancels_active_task(self):
        registry = TaskRegistry()
        async def long_running(): await asyncio.sleep(999)
        task = registry.create_task(long_running())
        await asyncio.wait_for(registry.shutdown(), timeout=5)
        assert task.cancelled() or task.done()

    async def test_shutdown_empty_registry_no_exception(self):
        registry = TaskRegistry()
        await asyncio.wait_for(registry.shutdown(), timeout=5)

    async def test_shutdown_clears_tasks(self):
        registry = TaskRegistry()
        async def long_running(): await asyncio.sleep(999)
        registry.create_task(long_running())
        await asyncio.wait_for(registry.shutdown(), timeout=5)
        assert len(registry._tasks) == 0


# ═════════════════════════════════════════════════════════════════════════════
# PARTE 4 — FinanceTracker (metodi non ancora coperti)  (11 test)
# ═════════════════════════════════════════════════════════════════════════════

_FINANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    etsy_listing_id TEXT    NOT NULL,
    order_id        TEXT    UNIQUE,
    niche           TEXT,
    product_type    TEXT,
    gross_eur       REAL    NOT NULL,
    etsy_fee_eur    REAL    NOT NULL,
    net_eur         REAL    NOT NULL,
    design_cost_eur REAL    DEFAULT 0.0,
    listing_fee_eur REAL    DEFAULT 0.18,
    sold_at         REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS pinterest_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    status         TEXT    NOT NULL DEFAULT 'planned',
    published_at   TEXT,
    cost_image_gen REAL    DEFAULT 0.0,
    cost_llm       REAL    DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS production_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    status         TEXT    NOT NULL DEFAULT 'planned',
    published_at   REAL,
    llm_cost_usd   REAL    DEFAULT 0.0,
    image_cost_usd REAL    DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL
);
"""


@pytest.fixture
async def finance_db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_FINANCE_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def ft(finance_db):
    memory = MagicMock()
    memory.get_db = AsyncMock(return_value=finance_db)
    return FinanceTracker(memory=memory)


class TestFinanceTrackerUncovered:

    async def test_pinterest_costs_month_empty_returns_zero(self, ft):
        result = await asyncio.wait_for(ft.pinterest_costs_month(), timeout=5)
        assert result == 0.0

    async def test_pinterest_costs_month_with_published_pin(self, ft, finance_db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        published_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        await finance_db.execute(
            "INSERT INTO pinterest_queue (status, published_at, cost_image_gen, cost_llm)"
            " VALUES (?,?,?,?)",
            ("published", published_at, 0.05, 0.02),
        )
        await finance_db.commit()
        result = await asyncio.wait_for(
            ft.pinterest_costs_month(year=now.year, month=now.month), timeout=5
        )
        assert abs(result - 0.07) < 0.001

    async def test_pinterest_costs_month_excludes_non_published(self, ft, finance_db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        published_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        await finance_db.execute(
            "INSERT INTO pinterest_queue (status, published_at, cost_image_gen, cost_llm)"
            " VALUES (?,?,?,?)",
            ("planned", published_at, 10.0, 5.0),   # non "published" → escluso
        )
        await finance_db.commit()
        result = await asyncio.wait_for(ft.pinterest_costs_month(), timeout=5)
        assert result == 0.0

    async def test_pinterest_costs_month_table_not_exists_returns_zero(self):
        async with aiosqlite.connect(":memory:") as conn:
            conn.row_factory = aiosqlite.Row
            # Nessuna tabella pinterest_queue → deve restituire 0.0 senza sollevare
            memory = MagicMock()
            memory.get_db = AsyncMock(return_value=conn)
            tracker = FinanceTracker(memory=memory)
            result = await asyncio.wait_for(tracker.pinterest_costs_month(), timeout=5)
            assert result == 0.0

    async def test_pinterest_costs_month_different_month_returns_zero(self, ft, finance_db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        published_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        await finance_db.execute(
            "INSERT INTO pinterest_queue (status, published_at, cost_image_gen, cost_llm)"
            " VALUES (?,?,?,?)",
            ("published", published_at, 1.0, 0.5),
        )
        await finance_db.commit()
        # Chiediamo un mese diverso (es. anno 2000) → deve restituire 0.0
        result = await asyncio.wait_for(
            ft.pinterest_costs_month(year=2000, month=1), timeout=5
        )
        assert result == 0.0

    async def test_goal_progress_has_required_keys(self, ft):
        result = await asyncio.wait_for(ft.goal_progress(goal_eur=500.0), timeout=5)
        for key in ("current_net_eur", "goal_eur", "pct", "days_elapsed",
                    "days_left", "daily_rate", "daily_needed", "on_track"):
            assert key in result

    async def test_goal_progress_with_mocked_monthly_summary(self, ft):
        ft.monthly_summary = AsyncMock(return_value={
            "year": 2024, "month": 5, "n_sales": 10,
            "gross_eur": 200.0, "etsy_fees_eur": 20.0,
            "listing_fees_eur": 2.0, "design_costs_eur": 5.0,
            "net_eur": 150.0, "margin_pct": 75.0,
        })
        result = await asyncio.wait_for(ft.goal_progress(goal_eur=500.0), timeout=5)
        assert result["current_net_eur"] == 150.0
        assert result["goal_eur"]        == 500.0
        assert result["pct"]             == 30.0
        assert "on_track" in result

    async def test_top_earners_returns_list(self, ft):
        result = await asyncio.wait_for(ft.top_earners(), timeout=5)
        assert isinstance(result, list)

    async def test_cost_per_listing_avg_returns_required_keys(self, ft):
        result = await asyncio.wait_for(ft.cost_per_listing_avg(), timeout=5)
        for key in ("n_listings", "avg_llm_usd", "avg_image_usd", "avg_total_usd", "avg_total_eur"):
            assert key in result

    async def test_break_even_price_for_avg_returns_float(self, ft):
        result = await asyncio.wait_for(ft.break_even_price_for_avg(), timeout=5)
        assert isinstance(result, float)
        assert result >= 0.0

    async def test_break_even_price_for_avg_uses_cost_data(self, ft, finance_db):
        now = _time.time()
        await finance_db.execute(
            "INSERT INTO production_queue (status, published_at, llm_cost_usd, image_cost_usd)"
            " VALUES (?,?,?,?)",
            ("published", now, 0.30, 0.20),
        )
        await finance_db.commit()
        result = await asyncio.wait_for(ft.break_even_price_for_avg(), timeout=5)
        assert result > 0.0
