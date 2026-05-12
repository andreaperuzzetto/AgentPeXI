# MOCK CONTRACT — _confidence.py
# Classe principale: ConfidenceMixin
# Metodi pubblici:
#   _apply_confidence_gate(user_message, agent_name, result, session_id, source) -> str [async]
#   _compile_wiki_entry(agent_name, result, session_id) -> None [async]
#   _voice_error_phrase(error_msg) -> str [staticmethod]
#   _synthesize_error(agent_name, error_message, context_data, missing_data) -> str [async]
#   _evaluate_and_gate_pattern(signal, pattern_value, metric_type, current_metric) -> bool [async]
#   _store_design_winner(niche, template, color_scheme, views, sales) -> None [async]
#   _handle_learning_loop(analytics_output) -> None [async]
#
# Dipendenze:
#   memory: save_message, store_insight, get_baseline_metric,
#           save_learning_evaluation, save_pending_action
#   _ws_broadcast: Callable[[dict], Coroutine] | None
#   _agent_cards: dict[str, AgentCard]
#   _personal_layer: PersonalLayer  (confidence_threshold=0.90, confidence_disclaimer=0.60)
#   _business_domain: DomainContext | None
#   wiki: None or object with compile_niche, store_raw, compile_wiki_file (all async)
#   _has_business_domain() -> bool
#   _fire(coro, name="") -> asyncio.Task
#   _synthesize_reply(...) -> str [async]
#   _broadcast_context_update(...) -> None [async]
#   _advance_pipeline_if_autonomous(...) -> None [async]
#   _llm_simple_call(...) -> str [async]
#   notify_telegram(...) -> None [async]
#   domain: has learning_triggers dict
#   _queue: asyncio.Queue
#
# Pattern di istanziazione:
#   class FakeConf(ConfidenceMixin) con tutti gli attributi come mock manuali;
#   fixture `conf` crea un'istanza fresca per ogni test.
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.backend.core._pepe._confidence import ConfidenceMixin
from apps.backend.core.config import settings
from apps.backend.core.domains import PersonalLayer, PERSONAL_LAYER
from apps.backend.core.models import AgentCard, AgentResult, AgentTask, TaskStatus


# ---------------------------------------------------------------------------
# FakeConf — concrete stub for ConfidenceMixin
# ---------------------------------------------------------------------------

class FakeConf(ConfidenceMixin):
    """Minimal stub: provides all attributes/methods that ConfidenceMixin calls on self."""

    def __init__(self) -> None:
        self.memory = AsyncMock()
        self._ws_broadcast = AsyncMock()
        self._agent_cards: dict[str, AgentCard] = {}
        self._personal_layer = PERSONAL_LAYER
        self._business_domain = None
        self.wiki = None

        self.domain = MagicMock()
        self.domain.learning_triggers = {}
        self.domain.name = "etsy_store"

        self._queue: asyncio.Queue = asyncio.Queue()

        # Sync helpers
        self._has_business_domain = MagicMock(return_value=False)

        def _fire_close(coro, name: str = "") -> None:
            """Close any coroutine passed to _fire to avoid 'never awaited' warnings."""
            if asyncio.iscoroutine(coro):
                coro.close()
        self._fire = MagicMock(side_effect=_fire_close)

        # Async helpers
        self._synthesize_reply = AsyncMock(return_value="Synth reply")
        self._broadcast_context_update = AsyncMock()
        self._advance_pipeline_if_autonomous = AsyncMock()
        self._llm_simple_call = AsyncMock(return_value="LLM response text")
        self.notify_telegram = AsyncMock()

        # LLM clients (not used by ConfidenceMixin directly, but referenced)
        self.client = MagicMock()
        self._local_client = MagicMock()


@pytest.fixture
def conf() -> FakeConf:
    return FakeConf()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    status: TaskStatus = TaskStatus.COMPLETED,
    output: dict | None = None,
    reply_voice: str = "",
) -> AgentResult:
    return AgentResult(
        task_id="t1",
        agent_name="test_agent",
        status=status,
        output_data=output if output is not None else {},
        reply_voice=reply_voice,
    )


def _card(
    name: str = "research",
    layer: str = "personal",
    threshold: float = 0.85,
) -> AgentCard:
    return AgentCard(
        name=name,
        description="test card",
        input_schema={"x": "str"},
        layer=layer,
        llm="haiku",
        confidence_threshold=threshold,
    )


def _mock_wiki() -> MagicMock:
    wiki = MagicMock()
    wiki.compile_niche = AsyncMock()
    wiki.store_raw = AsyncMock()
    wiki.compile_wiki_file = AsyncMock()
    return wiki


# ===========================================================================
# _voice_error_phrase (staticmethod — sync)
# ===========================================================================

class TestVoiceErrorPhrase:

    def test_quando_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("errore: quando arriva?")
        assert "quando" in result.lower() or "capito" in result.lower()
        assert result == "Non ho capito quando, puoi ripetere?"

    def test_when_keyword_english(self):
        result = ConfidenceMixin._voice_error_phrase("error: when does it start?")
        assert result == "Non ho capito quando, puoi ripetere?"

    def test_missing_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("campo obbligatorio missing")
        assert result == "Non ho capito bene, puoi ripetere?"

    def test_testo_mancante_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("testo mancante nel messaggio")
        assert result == "Non ho capito bene, puoi ripetere?"

    def test_timeout_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("timeout after 30s")
        assert result == "Ci ho messo troppo, riprova."

    def test_network_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("network unreachable")
        assert result == "C'è un problema di connessione, riprova tra un momento."

    def test_connessione_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("problemi di connessione")
        assert result == "C'è un problema di connessione, riprova tra un momento."

    def test_duplicat_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("già un reminder duplicato")
        assert result == "Hai già qualcosa di simile, vuoi aggiungerlo lo stesso?"

    def test_notion_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("errore notion sincronizzazione")
        assert result == "Fatto, anche se la sincronizzazione esterna non è riuscita."

    def test_auth_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("unauthorized: api key invalid")
        assert result == "C'è un problema con le credenziali, controlla la configurazione."

    def test_credenziali_keyword(self):
        result = ConfidenceMixin._voice_error_phrase("credenziali non valide")
        assert result == "C'è un problema con le credenziali, controlla la configurazione."

    def test_unknown_error(self):
        result = ConfidenceMixin._voice_error_phrase("generic failure xyz")
        assert result == "Non sono riuscito, puoi ripetere?"

    def test_empty_string(self):
        result = ConfidenceMixin._voice_error_phrase("")
        assert result == "Non sono riuscito, puoi ripetere?"


# ===========================================================================
# _evaluate_and_gate_pattern
# ===========================================================================

class TestEvaluateAndGatePattern:

    async def test_cold_start_no_baseline(self, conf: FakeConf):
        conf.memory.get_baseline_metric = AsyncMock(return_value=None)
        conf.memory.save_learning_evaluation = AsyncMock()

        result = await asyncio.wait_for(
            conf._evaluate_and_gate_pattern("bestseller", "planners", "sales_delta", 15.0),
            timeout=5,
        )
        assert result is True
        conf.memory.save_learning_evaluation.assert_not_called()

    async def test_delta_above_threshold_accepted(self, conf: FakeConf):
        conf.memory.get_baseline_metric = AsyncMock(return_value=10.0)
        conf.memory.save_learning_evaluation = AsyncMock()

        # delta = 15 - 10 = 5 >> threshold (0.02) → accepted
        result = await asyncio.wait_for(
            conf._evaluate_and_gate_pattern("bestseller", "planners", "sales_delta", 15.0),
            timeout=5,
        )
        assert result is True
        conf.memory.save_learning_evaluation.assert_awaited_once()
        call_kwargs = conf.memory.save_learning_evaluation.call_args.kwargs
        assert call_kwargs["accepted"] is True

    async def test_delta_below_threshold_rejected(self, conf: FakeConf):
        conf.memory.get_baseline_metric = AsyncMock(return_value=10.0)
        conf.memory.save_learning_evaluation = AsyncMock()

        # delta = 10.005 - 10.0 = 0.005 < threshold (0.02) → rejected
        result = await asyncio.wait_for(
            conf._evaluate_and_gate_pattern("no_views", "planners", "views_delta", 10.005),
            timeout=5,
        )
        assert result is False
        conf.memory.save_learning_evaluation.assert_awaited_once()
        call_kwargs = conf.memory.save_learning_evaluation.call_args.kwargs
        assert call_kwargs["accepted"] is False

    async def test_delta_exactly_at_threshold_accepted(self, conf: FakeConf):
        conf.memory.get_baseline_metric = AsyncMock(return_value=0.0)
        conf.memory.save_learning_evaluation = AsyncMock()

        # Use baseline=0 to avoid float subtraction drift
        current = settings.LEARNING_ACCEPTANCE_THRESHOLD  # delta == threshold → accepted
        result = await asyncio.wait_for(
            conf._evaluate_and_gate_pattern("bestseller", "x", "sales_delta", current),
            timeout=5,
        )
        assert result is True

    async def test_pattern_id_format(self, conf: FakeConf):
        conf.memory.get_baseline_metric = AsyncMock(return_value=5.0)
        conf.memory.save_learning_evaluation = AsyncMock()

        await asyncio.wait_for(
            conf._evaluate_and_gate_pattern("no_conversion", "mugs", "task_success_rate", 0.0),
            timeout=5,
        )
        call_kwargs = conf.memory.save_learning_evaluation.call_args.kwargs
        assert call_kwargs["pattern_id"] == "no_conversion:mugs"


# ===========================================================================
# _store_design_winner
# ===========================================================================

class TestStoreDesignWinner:

    async def test_happy_path_calls_store_insight(self, conf: FakeConf):
        conf.memory.store_insight = AsyncMock()

        await asyncio.wait_for(
            conf._store_design_winner("planners", "weekly_planner", "navy", views=200, sales=15),
            timeout=5,
        )
        conf.memory.store_insight.assert_awaited_once()
        text, meta = conf.memory.store_insight.call_args.args
        assert "planners" in text
        assert "weekly_planner" in text
        assert meta["type"] == "design_winner"
        assert meta["sales"] == "15"

    async def test_exception_is_caught(self, conf: FakeConf):
        conf.memory.store_insight = AsyncMock(side_effect=RuntimeError("DB down"))

        # Must not raise — exception is caught and logged
        await asyncio.wait_for(
            conf._store_design_winner("planners", "template_a", "red", views=50, sales=5),
            timeout=5,
        )

    async def test_empty_color_scheme_stored_as_empty_string(self, conf: FakeConf):
        conf.memory.store_insight = AsyncMock()

        await asyncio.wait_for(
            conf._store_design_winner("mugs", "t1", "", views=20, sales=2),
            timeout=5,
        )
        _, meta = conf.memory.store_insight.call_args.args
        assert meta["color_scheme"] == ""


# ===========================================================================
# _synthesize_error
# ===========================================================================

class TestSynthesizeError:

    async def test_returns_llm_response(self, conf: FakeConf):
        conf._llm_simple_call = AsyncMock(return_value="L'agente research ha avuto un errore di rete.")

        reply = await asyncio.wait_for(
            conf._synthesize_error("research", "connection refused", {}, []),
            timeout=5,
        )
        assert reply == "L'agente research ha avuto un errore di rete."

    async def test_fallback_when_llm_empty(self, conf: FakeConf):
        conf._llm_simple_call = AsyncMock(return_value="")

        reply = await asyncio.wait_for(
            conf._synthesize_error("design", "timeout error", None, None),
            timeout=5,
        )
        assert "design" in reply
        assert "timeout error" in reply

    async def test_with_business_domain_name_in_system(self, conf: FakeConf):
        conf._llm_simple_call = AsyncMock(return_value="OK")
        bd = MagicMock()
        bd.name = "etsy_store"
        conf._business_domain = bd

        await asyncio.wait_for(
            conf._synthesize_error("publisher", "file not found", {"niche": "planners"}, ["tag"]),
            timeout=5,
        )
        call_args = conf._llm_simple_call.call_args
        system = call_args.args[0]
        assert "etsy_store" in system


# ===========================================================================
# _apply_confidence_gate
# ===========================================================================

class TestApplyConfidenceGate:

    async def test_failed_non_voice_calls_synthesize_error(self, conf: FakeConf):
        conf._synthesize_error = AsyncMock(return_value="Errore sintetizzato")
        r = _result(
            status=TaskStatus.FAILED,
            output={"error": "DB timeout"},
        )

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert reply == "Errore sintetizzato"
        conf._synthesize_error.assert_awaited_once()
        conf.memory.save_message.assert_awaited_once()

    async def test_failed_orb_voice_uses_reply_voice(self, conf: FakeConf):
        r = _result(
            status=TaskStatus.FAILED,
            output={"error": "timeout"},
            reply_voice="Ci ho messo troppo, riprova.",
        )

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        assert reply == "Ci ho messo troppo, riprova."
        conf._ws_broadcast.assert_awaited_once()

    async def test_failed_orb_voice_no_reply_voice_uses_fallback(self, conf: FakeConf):
        r = _result(
            status=TaskStatus.FAILED,
            output={"error": "timeout"},
            reply_voice="",
        )

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        assert reply == "Non sono riuscito, puoi ripetere?"

    async def test_failed_orb_voice_ws_broadcast_exception_handled(self, conf: FakeConf):
        conf._ws_broadcast = AsyncMock(side_effect=Exception("ws error"))
        r = _result(status=TaskStatus.FAILED, output={"error": "err"}, reply_voice="Riprova.")

        # Must not raise
        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        assert reply == "Riprova."

    async def test_confidence_none_proceeds_autonomously(self, conf: FakeConf):
        r = _result(output={"confidence": None})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert reply == "Synth reply"
        conf._synthesize_reply.assert_awaited_once()
        conf._advance_pipeline_if_autonomous.assert_awaited_once()

    async def test_confidence_high_with_card_autonomous(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        r = _result(output={"confidence": 0.95})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert reply == "Synth reply"
        conf._advance_pipeline_if_autonomous.assert_awaited_once()

    async def test_confidence_in_disclaimer_range_appends_warning(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        # personal layer: threshold=0.90, disclaimer=0.60
        # confidence 0.70: >= 0.60 but < 0.90 → disclaimer branch
        r = _result(output={"confidence": 0.70, "missing_data": ["price_data", "tags"]})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert "⚠️" in reply
        assert "70%" in reply
        conf._advance_pipeline_if_autonomous.assert_not_called()

    async def test_confidence_low_blocks_with_message(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        # confidence 0.30 < disclaimer 0.60 → block
        r = _result(output={"confidence": 0.30, "missing_data": ["price", "tags"]})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert "❌" in reply
        assert "30%" in reply
        conf._advance_pipeline_if_autonomous.assert_not_called()
        conf.memory.save_message.assert_awaited_once()

    async def test_orb_voice_high_confidence_uses_reply_voice(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        r = _result(output={"confidence": 0.95}, reply_voice="Fatto!")

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        assert reply == "Fatto!"
        conf._synthesize_reply.assert_not_called()

    async def test_orb_voice_disclaimer_uses_reply_voice(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        # 0.65: between disclaimer (0.60) and threshold (0.90)
        r = _result(output={"confidence": 0.65}, reply_voice="Risposta vocale disclaimer.")

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        assert reply == "Risposta vocale disclaimer."
        conf._synthesize_reply.assert_not_called()

    async def test_wiki_hook_fired_when_wiki_present(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        conf.wiki = _mock_wiki()
        r = _result(output={"confidence": 0.95})

        await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        conf._fire.assert_called_once()
        call_kwargs = conf._fire.call_args.kwargs
        assert call_kwargs.get("name") == "wiki_compile"

    async def test_wiki_hook_not_fired_when_wiki_none(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", threshold=0.85)
        conf.wiki = None
        r = _result(output={"confidence": 0.95})

        await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        conf._fire.assert_not_called()

    async def test_no_card_no_business_domain_uses_personal_layer(self, conf: FakeConf):
        conf._has_business_domain = MagicMock(return_value=False)
        # personal layer threshold=0.90; confidence 0.95 → autonomous
        r = _result(output={"confidence": 0.95})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert reply == "Synth reply"

    async def test_no_card_business_domain_uses_domain_threshold(self, conf: FakeConf):
        conf._has_business_domain = MagicMock(return_value=True)
        bd = MagicMock()
        bd.confidence_threshold = 0.85
        bd.confidence_disclaimer = 0.60
        conf._business_domain = bd
        # confidence 0.87 >= 0.85 → autonomous
        r = _result(output={"confidence": 0.87})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert reply == "Synth reply"

    async def test_card_business_layer_uses_business_domain_disclaimer(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "business", threshold=0.85)
        bd = MagicMock()
        bd.confidence_disclaimer = 0.60
        conf._business_domain = bd
        # confidence 0.70: < threshold 0.85, >= disclaimer 0.60 → disclaimer branch
        r = _result(output={"confidence": 0.70})

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        assert "⚠️" in reply

    async def test_output_not_dict_confidence_none(self, conf: FakeConf):
        r = _result(output=None)
        r.output_data = "raw string output"  # not a dict

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "web"),
            timeout=5,
        )
        # confidence is None → autonomous
        assert reply == "Synth reply"

    async def test_failed_no_ws_broadcast(self, conf: FakeConf):
        conf._ws_broadcast = None
        conf._synthesize_error = AsyncMock(return_value="Errore")
        r = _result(status=TaskStatus.FAILED, output={"error": "fail"}, reply_voice="Riprova.")

        reply = await asyncio.wait_for(
            conf._apply_confidence_gate("msg", "research", r, "s1", "orb_voice"),
            timeout=5,
        )
        # orb_voice + no ws_broadcast: reply = reply_voice, ws_broadcast not called
        assert reply == "Riprova."


# ===========================================================================
# _compile_wiki_entry
# ===========================================================================

class TestCompileWikiEntry:

    async def test_recall_returns_early(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result()

        await asyncio.wait_for(
            conf._compile_wiki_entry("recall", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_not_called()
        conf.wiki.store_raw.assert_not_called()

    async def test_remind_returns_early(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result()

        await asyncio.wait_for(
            conf._compile_wiki_entry("remind", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_not_called()

    async def test_research_with_niche_calls_compile_and_store(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"niche": "planners", "niches": []})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_awaited_once()
        conf.wiki.store_raw.assert_awaited_once()
        call_args = conf.wiki.compile_niche.call_args.args
        assert "planners" in call_args

    async def test_research_with_winner_niche(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"winner": {"niche": "mugs"}, "niches": []})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_awaited_once()
        call_args = conf.wiki.compile_niche.call_args.args
        assert "mugs" in call_args

    async def test_research_no_niche_only_store_raw(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"niches": []})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_not_called()
        conf.wiki.store_raw.assert_awaited_once()

    async def test_analytics_with_niche(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"niche": "stickers"})

        await asyncio.wait_for(
            conf._compile_wiki_entry("analytics", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_awaited_once()
        call_args = conf.wiki.compile_niche.call_args.args
        assert "stickers" in call_args

    async def test_analytics_no_niche_skip(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"niche": ""})

        await asyncio.wait_for(
            conf._compile_wiki_entry("analytics", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_niche.assert_not_called()

    async def test_publisher_with_listing_id_calls_compile_and_store_insight(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        conf.memory.store_insight = AsyncMock()
        r = _result(output={
            "publish_details": [{
                "niche": "planners",
                "listing_id": "123",
                "file_type": "pdf",
                "color_scheme": "navy",
                "status": "published",
            }],
        })

        await asyncio.wait_for(
            conf._compile_wiki_entry("publisher", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_awaited_once()
        conf.wiki.compile_niche.assert_awaited_once()
        conf.memory.store_insight.assert_awaited_once()
        text, meta = conf.memory.store_insight.call_args.args
        assert meta["type"] == "publish_success"

    async def test_publisher_with_failure_stores_failure_insight(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        conf.memory.store_insight = AsyncMock()
        r = _result(output={
            "publish_details": [{
                "niche": "mugs",
                "listing_id": None,
                "file_type": "pdf",
                "status": "skipped_file_too_large",
            }],
        })

        await asyncio.wait_for(
            conf._compile_wiki_entry("publisher", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_awaited_once()
        conf.wiki.compile_niche.assert_not_called()
        conf.memory.store_insight.assert_awaited_once()
        _, meta = conf.memory.store_insight.call_args.args
        assert meta["type"] == "publish_failure"
        assert meta["failure_type"] == "skipped_file_too_large"

    async def test_publisher_no_niche_skipped(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        conf.memory.store_insight = AsyncMock()
        r = _result(output={"publish_details": [{"niche": "", "listing_id": None, "status": "error"}]})

        await asyncio.wait_for(
            conf._compile_wiki_entry("publisher", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_not_called()
        conf.wiki.compile_niche.assert_not_called()

    async def test_design_with_variants_stores_each(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        conf.memory.store_insight = AsyncMock()
        r = _result(output={
            "niche": "planners",
            "preset": "minimalist",
            "template": "weekly_layout",
            "variants": [
                {"color_scheme": "navy", "colors": {}, "validation": {"valid": True, "file_size_kb": 500}, "pages": 12},
                {"color_scheme": "earth", "colors": {}, "validation": {"valid": False, "file_size_kb": 0}, "pages": 0},
            ],
        })

        await asyncio.wait_for(
            conf._compile_wiki_entry("design", r, "s1"),
            timeout=5,
        )
        assert conf.wiki.store_raw.await_count == 2
        assert conf.memory.store_insight.await_count == 2
        text, meta = conf.memory.store_insight.call_args_list[0].args
        assert meta["type"] == "design_outcome"
        assert meta["niche"] == "planners"

    async def test_design_missing_niche_skips_insight(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        conf.memory.store_insight = AsyncMock()
        r = _result(output={
            "niche": "",
            "preset": "",
            "template": "",
            "variants": [{"color_scheme": "navy", "validation": {}, "pages": 0}],
        })

        await asyncio.wait_for(
            conf._compile_wiki_entry("design", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_awaited_once()  # raw always
        conf.memory.store_insight.assert_not_called()

    async def test_finance_calls_compile_wiki_file(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"revenue": 1000, "costs": 200})

        await asyncio.wait_for(
            conf._compile_wiki_entry("finance", r, "s1"),
            timeout=5,
        )
        conf.wiki.compile_wiki_file.assert_awaited_once()
        call_args = conf.wiki.compile_wiki_file.call_args.args
        assert "etsy" in call_args
        assert "patterns/pricing" in call_args

    async def test_research_personal_calls_store_raw(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"query": "test", "result": "summary"})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research_personal", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_awaited_once()
        call_args = conf.wiki.store_raw.call_args.args
        assert "personal" in call_args

    async def test_summarize_calls_compile_wiki_file(self, conf: FakeConf):
        conf.wiki = _mock_wiki()
        r = _result(output={"summary": "A concise summary."})

        await asyncio.wait_for(
            conf._compile_wiki_entry("summarize", r, "s1"),
            timeout=5,
        )
        conf.wiki.store_raw.assert_awaited_once()
        conf.wiki.compile_wiki_file.assert_awaited_once()

    async def test_exception_does_not_propagate(self, conf: FakeConf):
        wiki = _mock_wiki()
        wiki.store_raw = AsyncMock(side_effect=RuntimeError("DB error"))
        conf.wiki = wiki
        r = _result(output={"niches": [], "niche": "x"})

        # Must not raise
        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )

    async def test_uses_personal_llm_client_for_personal_card(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "personal", 0.85)
        conf.wiki = _mock_wiki()
        r = _result(output={"niche": "planners", "niches": []})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )
        # compile_niche should be called with conf._local_client
        call_args = conf.wiki.compile_niche.call_args.args
        assert conf._local_client in call_args

    async def test_uses_business_llm_client_for_business_card(self, conf: FakeConf):
        conf._agent_cards["research"] = _card("research", "business", 0.85)
        conf.wiki = _mock_wiki()
        r = _result(output={"niche": "planners", "niches": []})

        await asyncio.wait_for(
            conf._compile_wiki_entry("research", r, "s1"),
            timeout=5,
        )
        call_args = conf.wiki.compile_niche.call_args.args
        assert conf.client in call_args


# ===========================================================================
# _handle_learning_loop
# ===========================================================================

class TestHandleLearningLoop:

    async def test_empty_listings_does_nothing(self, conf: FakeConf):
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": []}),
            timeout=5,
        )
        conf.notify_telegram.assert_not_called()
        conf.memory.save_pending_action.assert_not_called()

    async def test_no_listings_key_does_nothing(self, conf: FakeConf):
        await asyncio.wait_for(
            conf._handle_learning_loop({}),
            timeout=5,
        )
        conf.notify_telegram.assert_not_called()

    async def test_design_winner_stored_when_criteria_met(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"bestseller": "propose_variant"}
        conf.memory.save_pending_action = AsyncMock()

        listing = {
            "listing_id": "L1",
            "niche": "planners",
            "views": 50,
            "sales": 15,
            "days_live": 30,
            "template": "weekly",
            "color_scheme": "navy",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf._store_design_winner.assert_awaited_once_with("planners", "weekly", "navy", 50, 15)

    async def test_bestseller_accepted_sends_telegram_and_saves_pending_action(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"bestseller": "propose_variant"}
        conf.memory.save_pending_action = AsyncMock()

        listing = {
            "listing_id": "L1",
            "title": "Weekly Planner 2025",
            "niche": "planners",
            "views": 200,
            "sales": 12,
            "days_live": 30,
            "template": "weekly",
            "color_scheme": "navy",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf.notify_telegram.assert_awaited_once()
        msg = conf.notify_telegram.call_args.args[0]
        assert "Bestseller" in msg or "bestseller" in msg.lower()
        conf.memory.save_pending_action.assert_awaited_once()
        kwargs = conf.memory.save_pending_action.call_args.kwargs
        assert kwargs["action_type"] == "bestseller_variant_proposal"

    async def test_bestseller_rejected_gate_skips_proposal(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=False)
        conf.domain.learning_triggers = {"bestseller": "propose_variant"}
        conf.memory.save_pending_action = AsyncMock()

        listing = {
            "listing_id": "L1",
            "niche": "planners",
            "views": 10,
            "sales": 10,
            "days_live": 10,
            "template": "t",
            "color_scheme": "",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf.notify_telegram.assert_not_called()
        conf.memory.save_pending_action.assert_not_called()

    async def test_no_views_signal_enqueues_fix_tags_task(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"no_views": "fix_tags"}

        listing = {
            "listing_id": "L2",
            "title": "Sticker Pack",
            "niche": "stickers",
            "views": 0,
            "sales": 0,
            "days_live": 10,
            "failure_type": "no_views",
            "template": "",
            "color_scheme": "",
            "tags": ["fun", "cute"],
            "delta_views_vs_yesterday": -5.0,
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        assert not conf._queue.empty()
        task: AgentTask = conf._queue.get_nowait()
        assert task.agent_name == "research"
        assert task.input_data["task_type"] == "fix_tags"
        conf.notify_telegram.assert_awaited_once()

    async def test_no_conversion_signal_enqueues_fix_pricing_task(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"no_conversion": "fix_pricing"}

        listing = {
            "listing_id": "L3",
            "title": "Mug Design",
            "niche": "mugs",
            "views": 300,
            "sales": 0,
            "days_live": 50,
            "failure_type": "no_conversion",
            "template": "",
            "color_scheme": "",
            "price_usd": 9.99,
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        assert not conf._queue.empty()
        task: AgentTask = conf._queue.get_nowait()
        assert task.agent_name == "research"
        assert task.input_data["task_type"] == "fix_pricing"
        conf.notify_telegram.assert_awaited_once()

    async def test_signal_none_continues_without_action(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {}

        # sales=5 (< 10), no failure_type → signal is None
        listing = {
            "listing_id": "L4",
            "niche": "cards",
            "views": 50,
            "sales": 5,
            "days_live": 10,
            "template": "",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf.notify_telegram.assert_not_called()
        assert conf._queue.empty()

    async def test_unknown_action_falls_through_to_debug(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"bestseller": "unknown_action_xyz"}

        listing = {
            "listing_id": "L5",
            "niche": "journals",
            "views": 100,
            "sales": 10,
            "days_live": 20,
            "template": "journal_template",
        }
        # Should not raise even with unknown action
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf.notify_telegram.assert_not_called()

    async def test_no_conversion_rejected_gate_skips_fix_pricing(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=False)
        conf.domain.learning_triggers = {"no_conversion": "fix_pricing"}

        listing = {
            "listing_id": "L6",
            "niche": "mugs",
            "views": 100,
            "sales": 0,
            "days_live": 50,
            "failure_type": "no_conversion",
            "template": "",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        assert conf._queue.empty()
        conf.notify_telegram.assert_not_called()

    async def test_design_winner_not_stored_when_sales_zero(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf.domain.learning_triggers = {}

        listing = {
            "listing_id": "L7",
            "niche": "planners",
            "views": 100,
            "sales": 0,  # < 1: criteria not met
            "days_live": 5,
            "template": "weekly",
        }
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": [listing]}),
            timeout=5,
        )
        conf._store_design_winner.assert_not_called()

    async def test_multiple_listings_processed_independently(self, conf: FakeConf):
        conf._store_design_winner = AsyncMock()
        conf._evaluate_and_gate_pattern = AsyncMock(return_value=True)
        conf.domain.learning_triggers = {"bestseller": "propose_variant"}
        conf.memory.save_pending_action = AsyncMock()

        listings = [
            {"listing_id": "A", "niche": "planners", "views": 200, "sales": 15, "days_live": 30, "template": "t1"},
            {"listing_id": "B", "niche": "stickers", "views": 0, "sales": 0, "days_live": 3, "template": ""},
        ]
        await asyncio.wait_for(
            conf._handle_learning_loop({"listings_analyzed": listings}),
            timeout=5,
        )
        # Only listing A matches bestseller
        conf.notify_telegram.assert_awaited_once()
