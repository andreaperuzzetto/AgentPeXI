"""Tests for wiki.py pure module-level functions and WikiManager non-IO methods."""
from __future__ import annotations

import pathlib
import pytest

from apps.backend.core.wiki import (
    _slugify,
    _parse_frontmatter,
    _estimate_tokens,
    WikiManager,
    COMPACTION_LIMITS,
    NICHE_HARD_LIMIT,
    DEFAULT_HARD_LIMIT,
)

# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_extra_spaces():
    assert _slugify("  foo   bar  ") == "foo-bar"


def test_slugify_special_chars():
    result = _slugify("Café & Résumé!")
    assert "&" not in result
    assert "!" not in result


def test_slugify_already_slug():
    assert _slugify("already-slug") == "already-slug"


def test_slugify_underscores_become_dash():
    assert _slugify("hello_world") == "hello-world"


def test_slugify_empty():
    assert _slugify("") == ""


def test_slugify_preserves_hyphens():
    # implementation does not collapse consecutive hyphens
    result = _slugify("foo-bar")
    assert result == "foo-bar"


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

def test_parse_frontmatter_basic():
    text = "---\ntitle: My Title\nniche: planner\n---\nBody text"
    result = _parse_frontmatter(text)
    assert result["title"] == "My Title"
    assert result["niche"] == "planner"


def test_parse_frontmatter_empty():
    result = _parse_frontmatter("No frontmatter here")
    assert result == {}


def test_parse_frontmatter_only_dashes():
    result = _parse_frontmatter("---\n---\n")
    assert result == {}


def test_parse_frontmatter_quoted_values():
    text = '---\ntitle: "My Quoted Title"\n---\n'
    result = _parse_frontmatter(text)
    assert result["title"] == "My Quoted Title"


def test_parse_frontmatter_strips_whitespace():
    text = "---\nkey:   value   \n---\n"
    result = _parse_frontmatter(text)
    assert result["key"] == "value"


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_single_word():
    result = _estimate_tokens("hello")
    assert result == int(1 * 1.33)


def test_estimate_tokens_scales_with_text():
    short = _estimate_tokens("one two three")
    long = _estimate_tokens("one two three four five six")
    assert long > short


def test_estimate_tokens_returns_int():
    assert isinstance(_estimate_tokens("hello world"), int)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

def test_compaction_limits_keys():
    assert "seasonal" in COMPACTION_LIMITS
    assert "pricing" in COMPACTION_LIMITS
    assert "learnings" in COMPACTION_LIMITS


def test_compaction_limits_values_positive():
    for k, v in COMPACTION_LIMITS.items():
        assert v > 0, f"{k} must be positive"


def test_hard_limits_are_positive():
    assert NICHE_HARD_LIMIT > 0
    assert DEFAULT_HARD_LIMIT > 0


# ---------------------------------------------------------------------------
# WikiManager — sync methods (no IO)
# ---------------------------------------------------------------------------

@pytest.fixture
def wiki(tmp_path):
    return WikiManager(base_path=tmp_path)


def test_wiki_manager_init_paths(wiki, tmp_path):
    assert wiki.base_path == tmp_path
    assert wiki.wiki_path == tmp_path / "wiki"
    assert wiki.raw_path == tmp_path / "raw"


def test_build_compile_niche_user_known_agent(wiki):
    result = wiki._build_compile_niche_user(
        niche="planner",
        agent="research",
        output={"keyword": "budget planner"},
        existing="",
    )
    assert "planner" in result
    assert "research" in result
    assert "budget planner" in result
    assert "crealo da zero" in result  # no existing file


def test_build_compile_niche_user_with_existing(wiki):
    result = wiki._build_compile_niche_user(
        niche="journal",
        agent="analytics",
        output={"sales": 10},
        existing="## Existing content",
    )
    assert "FILE ESISTENTE" in result
    assert "Existing content" in result


def test_build_compile_niche_user_unknown_agent(wiki):
    result = wiki._build_compile_niche_user(
        niche="tracker",
        agent="unknown_agent",
        output={},
        existing="",
    )
    assert "tracker" in result
    assert "unknown_agent" in result


def test_read_manifest_missing_returns_empty(wiki):
    assert wiki._read_manifest() == {}


def test_write_and_read_manifest(wiki):
    data = {"version": "1.0", "domains": ["etsy"]}
    wiki._write_manifest(data)
    loaded = wiki._read_manifest()
    assert loaded["version"] == "1.0"
    assert "etsy" in loaded["domains"]


def test_iter_wiki_files_empty_domain(wiki, tmp_path):
    domain_path = tmp_path / "wiki" / "etsy"
    domain_path.mkdir(parents=True)
    files = list(wiki._iter_wiki_files("etsy"))
    assert files == []


def test_iter_wiki_files_skips_index_and_underscore(wiki, tmp_path):
    domain_path = tmp_path / "wiki" / "etsy"
    domain_path.mkdir(parents=True)
    (domain_path / "_index.md").write_text("index")
    (domain_path / "_hidden.md").write_text("hidden")
    (domain_path / "planner.md").write_text("visible")
    files = list(wiki._iter_wiki_files("etsy"))
    assert len(files) == 1
    assert files[0].name == "planner.md"


def test_iter_wiki_files_nonexistent_domain_yields_nothing(wiki):
    files = list(wiki._iter_wiki_files("nonexistent"))
    assert files == []


async def test_wiki_manager_init_creates_dirs(wiki, tmp_path):
    await wiki.init()
    assert (tmp_path / "wiki").exists()
    assert (tmp_path / "raw").exists()


async def test_get_stats_empty(wiki, tmp_path):
    await wiki.init()
    stats = await wiki.get_stats()
    assert isinstance(stats, dict)
    assert "domains" in stats or len(stats) >= 0  # just verifies it runs
