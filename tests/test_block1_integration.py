"""
Test di integrazione Blocco 1 — mock mode end-to-end.

Verifica:
  T1. MarketDataAgent.collect_full() → MarketSignals tier=2, entry_score valido
  T2. EntryPointScoring.rank_candidates() → top-3, ordine decrescente, eligibility check
  T3. _autonomous_discovery() → scoring hook riduce N candidati a ≤3,
      sub-task contiene entry_score e market_context non vuoto
  T4. _calculate_confidence() → +0.15 con entry_point=market_signals
  T5. Cold-start → entry_score=0.4 flat, nessun crash

Eseguire da root del progetto:
    python -m pytest tests/test_block1_integration.py -v
oppure direttamente:
    python tests/test_block1_integration.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Stub dipendenze esterne (evita import di Anthropic, aiosqlite, ChromaDB, …)
# ---------------------------------------------------------------------------

def _setup_stubs():
    """Installa stub minimi nel sys.modules prima di importare i moduli."""

    for ns in [
        "anthropic", "aiosqlite", "chromadb", "voyageai",
        "pytrends", "pytrends.request",
        "apps", "apps.backend", "apps.backend.core",
        "apps.backend.agents", "apps.backend.tools",
        "apps.backend.tools.trends", "apps.backend.tools.tavily",
        "cryptography", "cryptography.fernet",
        "tenacity",
    ]:
        sys.modules.setdefault(ns, types.ModuleType(ns))

    # config
    fake_cfg = types.ModuleType("apps.backend.core.config")
    fake_cfg.settings = types.SimpleNamespace(
        ETSY_API_KEY="", USD_EUR_RATE=0.92,
        ANTHROPIC_API_KEY="", VOYAGE_API_KEY="",
        STORAGE_PATH="/tmp",
    )
    fake_cfg.MODEL_HAIKU = "claude-haiku"
    fake_cfg.MODEL_SONNET = "claude-sonnet"
    sys.modules["apps.backend.core.config"] = fake_cfg

    # memory
    class _FakeDB:
        async def execute(self, sql, params=()):
            class _FC:
                async def fetchone(self): return None
                async def fetchall(self): return []
            return _FC()
        async def commit(self): pass

    class FakeMemory:
        async def get_db(self): return _FakeDB()
        async def query_chromadb_recent(self, **kw): return []
        async def query_chromadb(self, **kw): return []
        async def log_agent_task(self, **kw): pass
        async def log_step(self, **kw): return 0
        async def log_llm_call(self, **kw): pass
        async def finalize_agent_task(self, **kw): pass
        async def log_error(self, **kw): pass
        async def log_tool_call(self, **kw): pass
        async def get_production_queue(self, **kw): return []
        async def is_duplicate_product(self, *a): return False
        async def store_insight(self, *a, **kw): pass

    fake_mem_mod = types.ModuleType("apps.backend.core.memory")
    fake_mem_mod.MemoryManager = FakeMemory
    sys.modules["apps.backend.core.memory"] = fake_mem_mod

    # models
    class TaskStatus(Enum):
        COMPLETED = "completed"
        FAILED = "failed"
        PENDING = "pending"

    @dataclass
    class AgentTask:
        agent_name: str = ""
        input_data: dict = field(default_factory=dict)
        source: str = "test"
        task_id: str = "test-task-001"

    @dataclass
    class AgentResult:
        task_id: str = ""
        agent_name: str = ""
        status: Any = TaskStatus.COMPLETED
        output_data: dict = field(default_factory=dict)
        tokens_used: int = 0
        cost_usd: float = 0.0
        duration_ms: int = 0
        confidence: float = 0.0
        missing_data: list = field(default_factory=list)
        reply_voice: str = ""

    @dataclass
    class AgentCard:
        name: str = ""
        description: str = ""
        input_schema: dict = field(default_factory=dict)
        layer: str = ""
        llm: str = ""
        requires_clarification: list = field(default_factory=list)
        confidence_threshold: float = 0.85
        pipeline_position: int = 1

    fake_models = types.ModuleType("apps.backend.core.models")
    fake_models.AgentTask = AgentTask
    fake_models.AgentResult = AgentResult
    fake_models.AgentCard = AgentCard
    fake_models.TaskStatus = TaskStatus
    sys.modules["apps.backend.core.models"] = fake_models

    # tools
    fake_tavily = types.ModuleType("apps.backend.tools.tavily")
    fake_tavily.search = AsyncMock(return_value={})
    fake_tavily.search_competitors = AsyncMock(return_value={})
    fake_tavily.search_keywords = AsyncMock(return_value={})
    fake_tavily.search_etsy_direct = AsyncMock(return_value={})
    sys.modules["apps.backend.tools.tavily"] = fake_tavily

    fake_trends = types.ModuleType("apps.backend.tools.trends")
    fake_trends.get_google_trends = AsyncMock(
        return_value={"current_value": 55, "avg_value": 50,
                      "trend_direction": "stable", "source": "google_trends"}
    )
    sys.modules["apps.backend.tools.trends"] = fake_trends

    # base agent
    fake_base = types.ModuleType("apps.backend.agents.base")
    class AgentBase:
        def __init__(self, name, model, **kw):
            self.name  = name
            self.model = model
            self.memory = FakeMemory()
            self._task_id = ""
            self._step_counter = 0
            self._llm_call_count = 0
            self._tool_call_count = 0
            self._total_cost = 0.0
        async def spawn_subagent(self, task):
            """Stub — sovrascritta da patch.object nei test."""
            return None
        async def _call_llm(self, *a, **kw): return ""
        async def _log_step(self, *a, **kw): pass
        async def _notify_telegram(self, *a, **kw): pass
        async def _call_tool(self, tool_name, action, input_params, fn, *args, **kwargs):
            """Stub — chiama fn se callable, altrimenti None."""
            try:
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)
            except Exception:
                return None
    fake_base.AgentBase = AgentBase
    sys.modules["apps.backend.agents.base"] = fake_base

    return FakeMemory


_setup_stubs()

# ---------------------------------------------------------------------------
# Import moduli reali (dopo gli stub)
# ---------------------------------------------------------------------------

import importlib.util

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_ROOT = __file__.replace("/tests/test_block1_integration.py", "")

MarketDataMod  = _load("apps.backend.agents.market_data",
                        f"{_ROOT}/apps/backend/agents/market_data.py")
EntryPointMod  = _load("apps.backend.core.entry_point_scoring",
                        f"{_ROOT}/apps/backend/core/entry_point_scoring.py")
ResearchMod    = _load("apps.backend.agents.research",
                        f"{_ROOT}/apps/backend/agents/research.py")

MarketDataAgent   = MarketDataMod.MarketDataAgent
MarketSignals     = MarketDataMod.MarketSignals
EntryPointScoring = EntryPointMod.EntryPointScoring
ScoredCandidate   = EntryPointMod.ScoredCandidate
ResearchAgent     = ResearchMod.ResearchAgent
AgentTask         = sys.modules["apps.backend.core.models"].AgentTask
AgentResult       = sys.modules["apps.backend.core.models"].AgentResult
TaskStatus        = sys.modules["apps.backend.core.models"].TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeMemory:
    """Memory con DB stub che non persiste nulla."""

    async def get_db(self):
        class _DB:
            async def execute(self, sql, params=()):
                class _C:
                    async def fetchone(self): return None
                    async def fetchall(self): return []
                return _C()
            async def commit(self): pass
        return _DB()

    async def query_chromadb_recent(self, **kw): return []
    async def query_chromadb(self, **kw): return []
    async def log_agent_task(self, **kw): pass
    async def log_step(self, **kw): return 0
    async def log_llm_call(self, **kw): pass
    async def finalize_agent_task(self, **kw): pass
    async def log_error(self, **kw): pass
    async def log_tool_call(self, **kw): pass
    async def get_production_queue(self, **kw): return []
    async def is_duplicate_product(self, *a): return False
    async def store_insight(self, *a, **kw): pass


def _make_scorer(mock_mode=True):
    mem    = _FakeMemory()
    mda    = MarketDataAgent(memory=mem, mock_mode=mock_mode)
    scorer = EntryPointScoring(memory=mem, market_data=mda)
    return scorer, mda


def _make_research_agent():
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.name   = "research"
    agent.model  = "claude-haiku"
    agent.memory = _FakeMemory()
    agent._telegram_broadcast = None
    agent._entry_scorer = None
    agent._task_id = ""
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    return agent


# ---------------------------------------------------------------------------
# T1 — MarketDataAgent.collect_full() mock
# ---------------------------------------------------------------------------

async def test_t1_market_data_collect_full():
    print("T1: MarketDataAgent.collect_full() mock")

    mda = MarketDataAgent(memory=_FakeMemory(), mock_mode=True)

    # Patch _save_signals per non toccare DB
    mda._save_signals = AsyncMock()

    signals = await mda.collect_full("boho wedding printables", "printable_pdf")

    assert isinstance(signals, MarketSignals), "Deve ritornare MarketSignals"
    assert signals.tier == 2,                  "Tier deve essere 2 dopo collect_full"
    assert signals.entry_score > 0,            "entry_score deve essere > 0"
    assert signals.entry_score <= 1.0,         "entry_score deve essere <= 1.0"
    assert signals.google_trend_score > 0,     "google_trend_score deve essere > 0 (mock)"
    assert signals.seasonal_boost >= 1.0,      "seasonal_boost >= 1.0"
    assert signals.etsy_result_count > 0,      "etsy_result_count deve essere > 0 (mock)"

    print(f"  niche={signals.niche}  tier={signals.tier}  "
          f"score={signals.entry_score}  trends={signals.google_trend_score}")
    print("  ✅ PASS")


# ---------------------------------------------------------------------------
# T2 — EntryPointScoring.rank_candidates()
# ---------------------------------------------------------------------------

async def test_t2_rank_candidates():
    print("T2: EntryPointScoring.rank_candidates()")

    scorer, mda = _make_scorer()
    mda._save_signals = AsyncMock()

    candidates = [
        {"niche": "boho wedding printables",  "product_type": "printable_pdf"},
        {"niche": "minimalist wall art",       "product_type": "digital_art_png"},
        {"niche": "baby shower invitation",    "product_type": "printable_pdf"},
        {"niche": "halloween party printable", "product_type": "printable_pdf"},
        {"niche": "resume template modern",    "product_type": "printable_pdf"},
    ]

    ranked = await scorer.rank_candidates(candidates, top_k=3)

    assert len(ranked) <= 3,                    "top_k=3: massimo 3 risultati"
    assert len(ranked) > 0,                     "Almeno 1 candidato ranked"
    assert all(r.eligible for r in ranked),     "Tutti gli eligible"
    scores = [r.final_score for r in ranked]
    assert scores == sorted(scores, reverse=True), "Ordinamento decrescente"
    assert all(0 < r.final_score <= 1.0 for r in ranked), "Score in (0, 1]"

    print(f"  {len(ranked)} candidati ranked:")
    for r in ranked:
        print(f"    {r.niche}: final={r.final_score}  "
              f"base={r.base_score}  qgf={r.quality_gap_factor}")
    print("  ✅ PASS")


# ---------------------------------------------------------------------------
# T3 — _autonomous_discovery() scoring hook
# ---------------------------------------------------------------------------

async def test_t3_autonomous_discovery_hook():
    print("T3: _autonomous_discovery() → scoring hook")

    agent = _make_research_agent()

    # 5 candidati fissi dal mining (mock)
    FAKE_CANDIDATES = [
        {"niche": "boho wedding printables",  "product_type": "printable_pdf",  "source": "seasonal"},
        {"niche": "minimalist wall art",       "product_type": "digital_art_png","source": "trends"},
        {"niche": "baby shower invitation",    "product_type": "printable_pdf",  "source": "default_pool"},
        {"niche": "halloween party printable", "product_type": "printable_pdf",  "source": "seasonal"},
        {"niche": "resume template modern",    "product_type": "printable_pdf",  "source": "default_pool"},
    ]

    # Cattura i sub-task creati durante l'analisi
    captured_subtasks: list[dict] = []

    async def fake_spawn_subagent(sub_task):
        captured_subtasks.append(dict(sub_task.input_data))
        niche = sub_task.input_data.get("niches", ["?"])[0]
        return AgentResult(
            task_id    = sub_task.task_id,
            agent_name = "research",
            status     = TaskStatus.COMPLETED,
            output_data = {
                "niches": [{
                    "name": niche, "viable": True,
                    "viability_reason": "test",
                    "demand": {"level": "high", "trend": "stable",
                               "seasonality": "n/a", "peak_months": [4],
                               "publish_timing_advice": "now"},
                    "competition": {"level": "medium", "top_sellers": [],
                                    "avg_quality": "medium",
                                    "what_top_sellers_do": "",
                                    "gap_to_exploit": ""},
                    "pricing": {"min_usd": 4.0, "max_usd": 10.0, "avg_usd": 7.0,
                                "conversion_sweet_spot_usd": 6.5,
                                "launch_price_usd": 5.9, "mature_price_usd": 7.9,
                                "price_reasoning": "test"},
                    "keywords": ["kw1", "kw2"],
                    "etsy_tags_13": [f"tag{i}" for i in range(13)],
                    "tag_strategy": "test",
                    "recommended_product_type": "printable_pdf",
                    "product_format_details": "A4",
                    "entry_difficulty": "low",
                    "selling_signals": {
                        "thumbnail_style": "mockup",
                        "conversion_triggers": ["price"],
                        "bundle_vs_single": "single",
                        "bundle_reasoning": "",
                        "first_listing_recommendation": "test listing",
                    },
                    "failure_analysis_applied": {"failures_found": 0,
                                                 "actions_taken": [],
                                                 "avoided": []},
                    "notes": "",
                }],
                "summary": "test summary",
                "recommended_next_steps": [],
                "data_quality_warning": "",
            },
        )

    # Output sintetico mock (sintesi Sonnet → scegli vincitore)
    FAKE_SYNTHESIS = """{
        "winner": {
            "niche": "boho wedding printables",
            "product_type": "printable_pdf",
            "why_winner": "top score",
            "confidence": 0.87
        },
        "summary": "boho wedding è il vincitore",
        "recommended_next_steps": ["pubblica subito"],
        "data_quality_warning": ""
    }"""

    async def fake_call_llm(*args, **kwargs):
        return FAKE_SYNTHESIS

    with (
        patch.object(agent, "_mine_opportunity_candidates",
                     AsyncMock(return_value=FAKE_CANDIDATES)),
        patch.object(agent, "spawn_subagent",   fake_spawn_subagent),
        patch.object(agent, "_call_llm",        fake_call_llm),
        patch.object(agent, "_log_step",        AsyncMock()),
        patch.object(agent, "_notify_telegram", AsyncMock()),
    ):
        # Patch _save_signals per non toccare DB
        MarketDataAgent._save_signals_orig = MarketDataAgent._save_signals
        MarketDataAgent._save_signals = AsyncMock()

        task = AgentTask(
            agent_name="research",
            input_data={"mode": "autonomous"},
            source="test",
        )
        result = await agent._autonomous_discovery(task)

        MarketDataAgent._save_signals = MarketDataAgent._save_signals_orig

    # Asserts principali
    assert len(captured_subtasks) <= 3, (
        f"Scoring deve ridurre a ≤3 sub-task, ne ha creati {len(captured_subtasks)}"
    )
    assert len(captured_subtasks) > 0, "Almeno 1 sub-task deve essere creato"

    print(f"  Mining: 5 candidati → dopo scoring: {len(captured_subtasks)} sub-task")

    for i, st in enumerate(captured_subtasks):
        entry_score = st.get("entry_score", 0.0)
        market_ctx  = st.get("market_context", "")
        assert entry_score > 0,    f"sub-task #{i}: entry_score deve essere > 0"
        assert len(market_ctx) > 0, f"sub-task #{i}: market_context non deve essere vuoto"
        assert "Dati di mercato" in market_ctx, \
            f"sub-task #{i}: market_context deve contenere 'Dati di mercato'"
        print(f"    sub-task #{i+1}: niche={st.get('niches',['?'])[0]}  "
              f"entry_score={entry_score:.3f}  "
              f"market_context={'OK' if market_ctx else 'VUOTO'}")

    print("  ✅ PASS")


# ---------------------------------------------------------------------------
# T4 — _calculate_confidence() delta +0.15
# ---------------------------------------------------------------------------

async def test_t4_confidence_delta():
    print("T4: _calculate_confidence() con/senza entry_point")

    output = {
        "niches": [{
            "viable": True,
            "etsy_tags_13": [f"t{i}" for i in range(13)],
            "selling_signals": {
                "thumbnail_style": "mockup lifestyle",
                "conversion_triggers": ["prezzo", "qualità"],
                "bundle_vs_single": "single",
                "first_listing_recommendation": "Wedding invitation A4",
            },
            "pricing": {
                "conversion_sweet_spot_usd": 6.5,
                "launch_price_usd": 5.9,
            },
            "demand": {
                "peak_months": [4, 5],
                "publish_timing_advice": "Pubblica 4 settimane prima",
            },
        }]
    }

    # Sorgenti deboli (llm_inference) per rendere il delta visibile
    ds_with = {
        "pricing": "llm_inference", "competitors": "",
        "trend": "llm_inference",   "keywords": "llm_inference",
        "entry_point": "market_signals",
    }
    ds_without = {**ds_with, "entry_point": "none"}

    score_with,    missing_with    = ResearchAgent._calculate_confidence(ds_with,    output)
    score_without, missing_without = ResearchAgent._calculate_confidence(ds_without, output)
    delta = round(score_with - score_without, 2)

    assert delta == 0.15, f"Delta atteso +0.15, got {delta}"
    print(f"  Con entry_point:   {score_with}")
    print(f"  Senza entry_point: {score_without}")
    print(f"  Delta:             +{delta}")
    print("  ✅ PASS")


# ---------------------------------------------------------------------------
# T5 — Cold-start: entry_score=0.4 flat, nessun crash
# ---------------------------------------------------------------------------

async def test_t5_cold_start():
    print("T5: Cold-start safety (nessun dato di mercato)")

    mda = MarketDataAgent(memory=_FakeMemory(), mock_mode=False)
    # In modalità non-mock ma senza API key → _real_tier1 → returns 0 counts

    # Testa direttamente _compute_entry_score con segnali vuoti
    empty = MarketSignals(niche="test", etsy_result_count=0, avg_reviews=0.0)
    score = mda._compute_entry_score(empty)
    assert score == 0.4, f"Cold-start entry_score deve essere 0.4, got {score}"
    print(f"  MarketSignals vuoti → entry_score={score}")

    # Tier 2 con google_trend_score=0 → stesso di Tier 1
    empty_t2 = MarketSignals(niche="test", etsy_result_count=0,
                              avg_reviews=0.0, google_trend_score=0.0, tier=2)
    score_t2 = mda._compute_entry_score(empty_t2)
    assert score_t2 == 0.4, f"Cold-start Tier2 deve essere 0.4, got {score_t2}"
    print(f"  MarketSignals Tier2 vuoti → entry_score={score_t2}")

    # EntryPointScoring cold-start: qgf=1.0 (avg_price_eur=0 → < 4€ ma anche result_count=0 < 50%)
    scorer, mda2 = _make_scorer()
    mda2._save_signals = AsyncMock()
    # Override collect_full per restituire segnali vuoti
    async def _empty_collect(niche, pt=None, force_refresh=False):
        s = MarketSignals(niche=niche, product_type=pt,
                         etsy_result_count=0, avg_reviews=0.0)
        s.entry_score = mda2._compute_entry_score(s)
        return s
    mda2.collect_full = _empty_collect

    sc = await scorer.score_single("nicchia sconosciuta XYZ")
    assert sc.base_score == 0.4, f"base_score cold-start deve essere 0.4, got {sc.base_score}"
    assert sc.eligible,          "Niche cold-start deve essere eligible"
    print(f"  ScoredCandidate cold-start → base={sc.base_score}  final={sc.final_score}")

    print("  ✅ PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_all():
    tests = [
        test_t1_market_data_collect_full,
        test_t2_rank_candidates,
        test_t3_autonomous_discovery_hook,
        test_t4_confidence_delta,
        test_t5_cold_start,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"  ❌ FAIL: {e}")
            import traceback; traceback.print_exc()
            failed += 1
        print()

    print("=" * 50)
    print(f"Risultato: {passed}/{len(tests)} test passati", end="")
    print(" ✅" if failed == 0 else f" — {failed} falliti ❌")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(_run_all())
    sys.exit(0 if ok else 1)
