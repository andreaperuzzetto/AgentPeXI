"""tests/unit/test_knowledge_bridge_coverage.py — ≥80% coverage su knowledge_bridge.py.

# MOCK CONTRACT - knowledge_bridge.py
# KnowledgeBridge.__init__(memory: MemoryManager)
#
# Metodi pubblici:
#   set_ws_broadcaster(broadcaster) → None  [sync]
#   on_new_insight(text, source_domain) → None  [async, entry point fail-safe]
#
# Metodi privati async:
#   _process(text, source_domain) → None           [pipeline 4-step]
#   _query_opposite(text, opposite_domain) → list[dict]
#   _gate_check(text_a, text_b) → bool
#   _synthesize(etsy_text, personal_text) → str
#   _haiku_call(system, user, max_tokens) → str
#
# Metodi privati sync:
#   _get_client() → anthropic.AsyncAnthropic  [lazy-init]
#   _pair_hash(text_a, text_b) → str          [staticmethod]
#
# Dipendenze esterne:
#   memory (MemoryManager): .query_chromadb(), .query_personal_memory(), .store_shared_insight()
#   anthropic.AsyncAnthropic: .messages.create()
#   apps.backend.core.config: settings.ANTHROPIC_API_KEY, MODEL_HAIKU
#
# Pattern di mock:
#   bridge._client = <MagicMock con .messages.create = AsyncMock>
#   per saltare lazy-init e non toccare settings
#
# Costanti chiave:
#   _MIN_TEXT_LEN = 80          (testo minimo per processare)
#   _GATE_PREVIEW_CHARS = 500
#   _SYNTH_PREVIEW_CHARS = 600
#   _DEDUP_CACHE_SIZE = 200
"""
from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.core.knowledge_bridge import KnowledgeBridge, _MIN_TEXT_LEN, _GATE_SYSTEM, _SYNTH_SYSTEM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_TEXT = "A" * 90   # > _MIN_TEXT_LEN (80)
_SHORT_TEXT = "A" * 40  # < _MIN_TEXT_LEN


def _make_bridge(
    *,
    query_chromadb_return=None,
    query_personal_return=None,
) -> KnowledgeBridge:
    """Crea un bridge con MemoryManager completamente mockato."""
    memory = MagicMock()
    memory.query_chromadb      = AsyncMock(return_value=query_chromadb_return or [])
    memory.query_personal_memory = AsyncMock(return_value=query_personal_return or [])
    memory.store_shared_insight  = AsyncMock()
    return KnowledgeBridge(memory=memory)


def _make_haiku_client(text_response: str = "YES") -> MagicMock:
    """Crea un finto client Anthropic con .messages.create che ritorna text_response."""
    content_block = MagicMock()
    content_block.text = text_response
    response = MagicMock()
    response.content = [content_block]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def _cross_result(doc_text: str) -> dict:
    return {"document": doc_text, "metadata": {}, "distance": 0.1}


# ---------------------------------------------------------------------------
# set_ws_broadcaster
# ---------------------------------------------------------------------------


def test_set_ws_broadcaster_assigns_attribute():
    bridge = _make_bridge()
    caster = AsyncMock()
    bridge.set_ws_broadcaster(caster)
    assert bridge._ws_broadcaster is caster


# ---------------------------------------------------------------------------
# _pair_hash — staticmethod
# ---------------------------------------------------------------------------


def test_pair_hash_deterministic():
    h1 = KnowledgeBridge._pair_hash("text_a_content", "text_b_content")
    h2 = KnowledgeBridge._pair_hash("text_a_content", "text_b_content")
    assert h1 == h2
    assert len(h1) == 24


def test_pair_hash_order_independent():
    h1 = KnowledgeBridge._pair_hash("aaa", "bbb")
    h2 = KnowledgeBridge._pair_hash("bbb", "aaa")
    assert h1 == h2


def test_pair_hash_differs_for_different_texts():
    h1 = KnowledgeBridge._pair_hash("aaa", "bbb")
    h2 = KnowledgeBridge._pair_hash("ccc", "ddd")
    assert h1 != h2


# ---------------------------------------------------------------------------
# _get_client — lazy init
# ---------------------------------------------------------------------------


def test_get_client_lazy_init_creates_on_first_call():
    bridge = _make_bridge()
    assert bridge._client is None
    with patch("apps.backend.core.knowledge_bridge.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        client1 = bridge._get_client()
        assert client1 is mock_cls.return_value
        assert mock_cls.call_count == 1


def test_get_client_returns_cached_on_second_call():
    bridge = _make_bridge()
    with patch("apps.backend.core.knowledge_bridge.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        c1 = bridge._get_client()
        c2 = bridge._get_client()
        assert c1 is c2
        assert mock_cls.call_count == 1


def test_get_client_uses_settings_api_key():
    bridge = _make_bridge()
    with patch("apps.backend.core.knowledge_bridge.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        with patch("apps.backend.core.knowledge_bridge.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "test-key-123"
            bridge._get_client()
            mock_cls.assert_called_once_with(api_key="test-key-123")


# ---------------------------------------------------------------------------
# _haiku_call
# ---------------------------------------------------------------------------


async def test_haiku_call_returns_stripped_text():
    bridge = _make_bridge()
    bridge._client = _make_haiku_client("  YES  ")
    result = await asyncio.wait_for(bridge._haiku_call("sys", "user", max_tokens=5), timeout=5)
    assert result == "YES"


async def test_haiku_call_empty_content_returns_empty_string():
    bridge = _make_bridge()
    response = MagicMock()
    response.content = []
    bridge._client = MagicMock()
    bridge._client.messages.create = AsyncMock(return_value=response)
    result = await asyncio.wait_for(bridge._haiku_call("sys", "user"), timeout=5)
    assert result == ""


async def test_haiku_call_passes_correct_params():
    bridge = _make_bridge()
    client = _make_haiku_client("NO")
    bridge._client = client
    await asyncio.wait_for(bridge._haiku_call("my_system", "my_user", max_tokens=10), timeout=5)
    client.messages.create.assert_awaited_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == "my_system"
    assert kwargs["messages"] == [{"role": "user", "content": "my_user"}]
    assert kwargs["max_tokens"] == 10


# ---------------------------------------------------------------------------
# _gate_check
# ---------------------------------------------------------------------------


async def test_gate_check_yes_returns_true():
    bridge = _make_bridge()
    bridge._client = _make_haiku_client("YES related topics")
    result = await asyncio.wait_for(bridge._gate_check("text a", "text b"), timeout=5)
    assert result is True


async def test_gate_check_no_returns_false():
    bridge = _make_bridge()
    bridge._client = _make_haiku_client("NO")
    result = await asyncio.wait_for(bridge._gate_check("text a", "text b"), timeout=5)
    assert result is False


async def test_gate_check_yes_case_insensitive():
    bridge = _make_bridge()
    bridge._client = _make_haiku_client("yes")
    result = await asyncio.wait_for(bridge._gate_check("text a", "text b"), timeout=5)
    assert result is True


async def test_gate_check_raises_returns_false():
    bridge = _make_bridge()
    bridge._client = MagicMock()
    bridge._client.messages.create = AsyncMock(side_effect=Exception("Anthropic down"))
    result = await asyncio.wait_for(bridge._gate_check("text a", "text b"), timeout=5)
    assert result is False  # fail-closed


# ---------------------------------------------------------------------------
# _synthesize
# ---------------------------------------------------------------------------


async def test_synthesize_returns_text_from_haiku():
    bridge = _make_bridge()
    bridge._client = _make_haiku_client("Connessione rilevata: marketing. Usare storytelling.")
    result = await asyncio.wait_for(bridge._synthesize("etsy text", "personal text"), timeout=5)
    assert "Connessione" in result


async def test_synthesize_raises_returns_empty_string():
    bridge = _make_bridge()
    bridge._client = MagicMock()
    bridge._client.messages.create = AsyncMock(side_effect=Exception("timeout"))
    result = await asyncio.wait_for(bridge._synthesize("etsy text", "personal text"), timeout=5)
    assert result == ""


# ---------------------------------------------------------------------------
# _query_opposite
# ---------------------------------------------------------------------------


async def test_query_opposite_etsy_calls_query_chromadb():
    bridge = _make_bridge(query_chromadb_return=[_cross_result(_LONG_TEXT)])
    results = await asyncio.wait_for(bridge._query_opposite(_LONG_TEXT, "etsy"), timeout=5)
    assert len(results) == 1
    bridge.memory.query_chromadb.assert_awaited_once()
    bridge.memory.query_personal_memory.assert_not_awaited()


async def test_query_opposite_personal_calls_query_personal_memory():
    bridge = _make_bridge(query_personal_return=[_cross_result(_LONG_TEXT)])
    results = await asyncio.wait_for(bridge._query_opposite(_LONG_TEXT, "personal"), timeout=5)
    assert len(results) == 1
    bridge.memory.query_personal_memory.assert_awaited_once()
    bridge.memory.query_chromadb.assert_not_awaited()


async def test_query_opposite_raises_returns_empty_list():
    bridge = _make_bridge()
    bridge.memory.query_chromadb = AsyncMock(side_effect=Exception("chroma error"))
    results = await asyncio.wait_for(bridge._query_opposite(_LONG_TEXT, "etsy"), timeout=5)
    assert results == []


# ---------------------------------------------------------------------------
# _process — tutti i branch dell'early-exit
# ---------------------------------------------------------------------------


async def test_process_short_text_skips_without_query():
    bridge = _make_bridge()
    await asyncio.wait_for(bridge._process(_SHORT_TEXT, "etsy"), timeout=5)
    bridge.memory.query_chromadb.assert_not_awaited()
    bridge.memory.query_personal_memory.assert_not_awaited()


async def test_process_empty_cross_results_returns_early():
    bridge = _make_bridge(query_personal_return=[])  # source=etsy → opposite=personal
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    bridge.memory.store_shared_insight.assert_not_awaited()


async def test_process_cross_text_too_short_returns_early():
    bridge = _make_bridge(query_personal_return=[_cross_result(_SHORT_TEXT)])
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    bridge.memory.store_shared_insight.assert_not_awaited()


async def test_process_dedup_second_call_skips():
    cross_doc = _LONG_TEXT + "CROSS"
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    bridge._client = _make_haiku_client("YES")

    # Prima chiamata: elabora normalmente
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    first_count = bridge.memory.store_shared_insight.await_count

    # Seconda chiamata: stessa coppia → skip dedup
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    assert bridge.memory.store_shared_insight.await_count == first_count  # non incrementa


async def test_process_gate_check_false_skips_store():
    cross_doc = _LONG_TEXT + "CROSS"
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    bridge._client = _make_haiku_client("NO")  # gate fail

    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    bridge.memory.store_shared_insight.assert_not_awaited()


async def test_process_synthesis_empty_skips_store():
    cross_doc = _LONG_TEXT + "CROSS"
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    # gate: YES, synthesis: ""
    responses = iter(["YES", ""])
    def _next_response(*_, **__):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block] if txt else []
        return resp

    bridge._client = MagicMock()
    bridge._client.messages.create = AsyncMock(side_effect=lambda **kw: _next_response())
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    bridge.memory.store_shared_insight.assert_not_awaited()


async def test_process_synthesis_too_short_skips_store():
    cross_doc = _LONG_TEXT + "CROSS"
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    # gate YES, synthesis < 20 chars
    responses = iter(["YES", "Short."])
    bridge._client = MagicMock()
    async def _create(**kw):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block]
        return resp
    bridge._client.messages.create = AsyncMock(side_effect=_create)
    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    bridge.memory.store_shared_insight.assert_not_awaited()


# ---------------------------------------------------------------------------
# _process — happy path completo
# ---------------------------------------------------------------------------


async def test_process_happy_path_stores_insight():
    cross_doc = "B" * 90
    synthesis = "Connessione rilevata: vendita. La strategia Etsy funziona anche offline."
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    responses = iter(["YES", synthesis])
    bridge._client = MagicMock()
    async def _create(**kw):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block]
        return resp
    bridge._client.messages.create = AsyncMock(side_effect=_create)

    await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)

    bridge.memory.store_shared_insight.assert_awaited_once()
    args, kwargs = bridge.memory.store_shared_insight.call_args
    stored_text = args[0]
    assert stored_text == synthesis
    metadata = kwargs["metadata"]
    assert "source_etsy" in metadata
    assert metadata["bridge_version"] == "1.0"


async def test_process_happy_path_source_personal():
    """source_domain='personal' → opposite='etsy' → usa query_chromadb."""
    cross_doc = "C" * 90
    synthesis = "Connessione rilevata: creatività. L'approccio usato personalmente migliora il prodotto."
    bridge = _make_bridge(query_chromadb_return=[_cross_result(cross_doc)])
    responses = iter(["YES", synthesis])
    bridge._client = MagicMock()
    async def _create(**kw):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block]
        return resp
    bridge._client.messages.create = AsyncMock(side_effect=_create)

    await asyncio.wait_for(bridge._process(_LONG_TEXT, "personal"), timeout=5)

    bridge.memory.query_chromadb.assert_awaited_once()
    bridge.memory.store_shared_insight.assert_awaited_once()


# ---------------------------------------------------------------------------
# _process — ws_broadcaster
# ---------------------------------------------------------------------------


async def test_process_ws_broadcaster_called_on_success():
    cross_doc = "D" * 90
    synthesis = "Connessione rilevata: automazione. Il processo si ottimizza con dati cross-domain."
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    responses = iter(["YES", synthesis])
    bridge._client = MagicMock()
    async def _create(**kw):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block]
        return resp
    bridge._client.messages.create = AsyncMock(side_effect=_create)

    caster = AsyncMock()
    bridge._ws_broadcaster = caster

    # create_task chiama il broadcaster; lo mocchiamo a livello asyncio
    with patch("asyncio.create_task") as mock_create_task:
        await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
        mock_create_task.assert_called_once()


async def test_process_ws_broadcaster_raises_no_crash():
    """Se asyncio.create_task solleva, la pipeline non deve sollevare."""
    cross_doc = "E" * 90
    synthesis = "Connessione rilevata: pricing. Dati storici guidano la scelta del prezzo."
    bridge = _make_bridge(query_personal_return=[_cross_result(cross_doc)])
    responses = iter(["YES", synthesis])
    bridge._client = MagicMock()
    async def _create(**kw):
        txt = next(responses)
        block = MagicMock()
        block.text = txt
        resp = MagicMock()
        resp.content = [block]
        return resp
    bridge._client.messages.create = AsyncMock(side_effect=_create)
    bridge._ws_broadcaster = AsyncMock()

    with patch("asyncio.create_task", side_effect=RuntimeError("loop closed")):
        await asyncio.wait_for(bridge._process(_LONG_TEXT, "etsy"), timeout=5)
    # non deve sollevare — fail-safe


# ---------------------------------------------------------------------------
# on_new_insight — fail-safe wrapper
# ---------------------------------------------------------------------------


async def test_on_new_insight_fail_safe_swallows_exception():
    bridge = _make_bridge()
    # _process solleva internamente — on_new_insight non deve propagare
    with patch.object(bridge, "_process", new=AsyncMock(side_effect=Exception("boom"))):
        await asyncio.wait_for(bridge.on_new_insight(_LONG_TEXT, "etsy"), timeout=5)
    # nessun raise — il test passa


async def test_on_new_insight_delegates_to_process():
    bridge = _make_bridge()
    with patch.object(bridge, "_process", new=AsyncMock()) as mock_proc:
        await asyncio.wait_for(bridge.on_new_insight(_LONG_TEXT, "personal"), timeout=5)
        mock_proc.assert_awaited_once_with(_LONG_TEXT, "personal")
