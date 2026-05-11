# MOCK CONTRACT — usato da RC2 per mock coerenti
#
# _LlmMixin._call_llm(
#     messages: list[LLMMessage],
#     system_prompt: str | None = None,
#     model_override: str | None = None,
#     max_tokens: int = 4096,
#     domain_name: str = "etsy_store",
# ) → str
#   mock: AsyncMock(return_value="response text")
#   mock_mode path: memory.mock_mode = True  → returns JSON stub string
#   ollama path: domain_name="personal"       → delegates to _call_llm_ollama
#
# _LlmMixin._call_llm_ollama(
#     messages: list[LLMMessage] | None = None,
#     system_prompt: str | None = None,
#     max_tokens: int = 4096,
#     *,
#     system: str | None = None,
#     user: str | None = None,
#     temperature: float | None = None,
# ) → str
#   mock: AsyncMock(return_value="haiku response")
#   internal: calls self._llm_with_retry(model=MODEL_HAIKU, ...)
#
# _LlmMixin._llm_with_retry(
#     model: str,
#     messages: list[LLMMessage],
#     system_prompt: str | None,
#     max_tokens: int,
#     max_retries: int = 3,
# ) → anthropic.types.Message
#   mock: client.messages.create = AsyncMock(return_value=<FakeMessage>)
#   FakeMessage: MagicMock with .usage.input_tokens, .usage.output_tokens,
#                .content[0].text = "text"
#   RateLimitError path: side_effect=[RateLimitError, RateLimitError, <response>]
#   529 overload path:   side_effect=[APIStatusError(529), <response>]
#   non-529 API error:   side_effect=APIStatusError(500) → re-raised immediately
#   exhausted retries:   side_effect=[RateLimitError]*3   → raises last_exc
#
# _get_ollama_client() → openai.AsyncOpenAI
#   module-level singleton; lazy-initialised behind asyncio.Lock
#   mock: patch openai.AsyncOpenAI
#
# _ToolsMixin._call_tool(
#     tool_name: str,
#     action: str,
#     input_params: dict | None,
#     fn: Callable,
#     *args, **kwargs,
# ) → Any
#   mock async fn: AsyncMock(return_value={"key": "val"})
#   mock sync fn:  MagicMock(return_value=42)
#   error fn:      AsyncMock(side_effect=ValueError("oops"))
#
# _ToolsMixin.spawn_subagent(task: AgentTask) → AgentResult
#   mock: patch self.__class__  (sub_agent.execute = AsyncMock(return_value=AgentResult(...)))
#
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from apps.backend.agents._base._llm_mixin import _LlmMixin, _get_ollama_client
from apps.backend.agents._base._tools_mixin import _ToolsMixin
from apps.backend.core.models import AgentResult, AgentTask, TaskStatus


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_anthropic_response(text: str = "hello", input_tok: int = 10, output_tok: int = 5):
    """Build a MagicMock that mimics anthropic.types.Message."""
    content_block = MagicMock()
    content_block.text = text

    usage = MagicMock()
    usage.input_tokens = input_tok
    usage.output_tokens = output_tok
    # No cache fields by default → getattr returns 0 fallback
    del usage.cache_read_input_tokens
    del usage.cache_creation_input_tokens

    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


def _make_agent(mixin_class, extra_attrs: dict | None = None):
    """Compose a minimal agent instance with the given mixin."""

    class _Agent(mixin_class):
        pass

    agent = _Agent()
    agent.name = "test_agent"
    agent.model = "claude-sonnet-test"
    agent._task_id = "task-001"
    agent._step_counter = 0
    agent._llm_call_count = 0
    agent._tool_call_count = 0
    agent._total_cost = 0.0
    agent._total_tokens = 0
    agent._counters_lock = asyncio.Lock()
    agent._ws_broadcast = None

    # Mock memory
    agent.memory = MagicMock()
    agent.memory.mock_mode = False
    agent.memory.log_llm_call = AsyncMock()
    agent.memory.log_tool_call = AsyncMock()
    agent.memory.log_step = AsyncMock()

    # Mock Anthropic client
    agent.client = MagicMock()
    agent.client.messages = MagicMock()
    agent.client.messages.create = AsyncMock(return_value=_fake_anthropic_response())

    # Mock cross-mixin helpers
    agent._log_step = AsyncMock(return_value=42)
    agent._broadcast = AsyncMock()
    agent._estimate_cost = MagicMock(return_value=0.001)
    agent._extra_init_kwargs = MagicMock(return_value={})

    if extra_attrs:
        for k, v in extra_attrs.items():
            setattr(agent, k, v)

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# class TestLLMMixin
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMMixin:
    # ── _call_llm: mock_mode path ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_llm_mock_mode_returns_json_stub(self):
        agent = _make_agent(_LlmMixin)
        agent.memory.mock_mode = True

        result = await asyncio.wait_for(
            agent._call_llm(messages=[{"role": "user", "content": "hi"}]),
            timeout=5,
        )

        parsed = json.loads(result)
        assert parsed["mock"] is True
        assert parsed["viability"] == pytest.approx(0.75)
        # No real LLM call should have been made
        agent.client.messages.create.assert_not_called()

    # ── _call_llm: personal domain → ollama path ─────────────────────────────

    @pytest.mark.asyncio
    async def test_call_llm_personal_domain_delegates_to_ollama(self):
        agent = _make_agent(_LlmMixin)
        agent._call_llm_ollama = AsyncMock(return_value="ollama reply")

        result = await asyncio.wait_for(
            agent._call_llm(
                messages=[{"role": "user", "content": "ciao"}],
                domain_name="personal",
            ),
            timeout=5,
        )

        assert result == "ollama reply"
        agent._call_llm_ollama.assert_called_once()

    # ── _call_llm: Anthropic happy path ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_llm_anthropic_happy_path(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response(text="great answer", input_tok=20, output_tok=8)
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        result = await asyncio.wait_for(
            agent._call_llm(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="You are helpful.",
                domain_name="etsy_store",
            ),
            timeout=5,
        )

        assert result == "great answer"
        agent._log_step.assert_called_once()
        agent.memory.log_llm_call.assert_called_once()
        agent._broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_llm_anthropic_updates_counters(self):
        agent = _make_agent(_LlmMixin)
        agent.client.messages.create = AsyncMock(
            return_value=_fake_anthropic_response(input_tok=10, output_tok=5)
        )
        agent._estimate_cost.return_value = 0.002

        await asyncio.wait_for(
            agent._call_llm(messages=[{"role": "user", "content": "x"}]),
            timeout=5,
        )

        assert agent._llm_call_count == 1
        assert agent._total_cost == pytest.approx(0.002)
        assert agent._total_tokens == 15

    @pytest.mark.asyncio
    async def test_call_llm_empty_content_returns_empty_string(self):
        """response.content == [] → response_text should be empty string."""
        agent = _make_agent(_LlmMixin)
        fake_resp = MagicMock()
        fake_resp.content = []
        fake_resp.usage = MagicMock(input_tokens=1, output_tokens=0)
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        result = await asyncio.wait_for(
            agent._call_llm(messages=[{"role": "user", "content": "x"}]),
            timeout=5,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_call_llm_model_override_is_used(self):
        agent = _make_agent(_LlmMixin)
        await asyncio.wait_for(
            agent._call_llm(
                messages=[{"role": "user", "content": "x"}],
                model_override="claude-opus-test",
            ),
            timeout=5,
        )
        call_kwargs = agent.client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus-test"

    # ── _call_llm_ollama ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_llm_ollama_with_messages_and_system_prompt(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response(text="haiku reply")
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        result = await asyncio.wait_for(
            agent._call_llm_ollama(
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="Be concise.",
            ),
            timeout=5,
        )

        assert result == "haiku reply"
        agent.memory.log_llm_call.assert_called_once()
        call_kwargs = agent.memory.log_llm_call.call_args[1]
        assert call_kwargs["provider"] == "haiku"

    @pytest.mark.asyncio
    async def test_call_llm_ollama_convenience_user_kwarg(self):
        """system/user kwargs → converted to messages/system_prompt."""
        agent = _make_agent(_LlmMixin)
        agent.client.messages.create = AsyncMock(
            return_value=_fake_anthropic_response(text="ok")
        )

        result = await asyncio.wait_for(
            agent._call_llm_ollama(system="Sys prompt", user="User question"),
            timeout=5,
        )

        assert result == "ok"
        call_kwargs = agent.client.messages.create.call_args[1]
        assert call_kwargs["messages"] == [{"role": "user", "content": "User question"}]
        assert call_kwargs["system"][0]["text"] == "Sys prompt"

    @pytest.mark.asyncio
    async def test_call_llm_ollama_no_messages_no_user_gives_empty_messages(self):
        agent = _make_agent(_LlmMixin)
        agent.client.messages.create = AsyncMock(
            return_value=_fake_anthropic_response(text="empty")
        )

        result = await asyncio.wait_for(
            agent._call_llm_ollama(),
            timeout=5,
        )

        assert result == "empty"
        call_kwargs = agent.client.messages.create.call_args[1]
        assert call_kwargs["messages"] == []

    @pytest.mark.asyncio
    async def test_call_llm_ollama_updates_counters(self):
        agent = _make_agent(_LlmMixin)
        agent.client.messages.create = AsyncMock(
            return_value=_fake_anthropic_response(input_tok=5, output_tok=3)
        )
        agent._estimate_cost.return_value = 0.001

        await asyncio.wait_for(
            agent._call_llm_ollama(user="hi"),
            timeout=5,
        )

        assert agent._llm_call_count == 1
        assert agent._total_tokens == 8

    @pytest.mark.asyncio
    async def test_call_llm_ollama_empty_content_returns_empty_string(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = MagicMock()
        fake_resp.content = []
        fake_resp.usage = MagicMock(input_tokens=1, output_tokens=0)
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        result = await asyncio.wait_for(
            agent._call_llm_ollama(user="x"),
            timeout=5,
        )
        assert result == ""

    # ── _llm_with_retry ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_with_retry_success_first_attempt(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response()
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        result = await asyncio.wait_for(
            agent._llm_with_retry(
                model="claude-test",
                messages=[{"role": "user", "content": "hi"}],
                system_prompt=None,
                max_tokens=1024,
            ),
            timeout=5,
        )
        assert result is fake_resp
        agent.client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_with_retry_system_prompt_injects_cache_block(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response()
        agent.client.messages.create = AsyncMock(return_value=fake_resp)

        await asyncio.wait_for(
            agent._llm_with_retry(
                model="m",
                messages=[],
                system_prompt="important context",
                max_tokens=100,
            ),
            timeout=5,
        )

        call_kwargs = agent.client.messages.create.call_args[1]
        assert "system" in call_kwargs
        assert call_kwargs["system"][0]["text"] == "important context"
        assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_llm_with_retry_rate_limit_retries_and_succeeds(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response(text="retried ok")

        rate_err = anthropic.RateLimitError(
            message="rate limit", response=MagicMock(status_code=429), body={}
        )
        agent.client.messages.create = AsyncMock(
            side_effect=[rate_err, fake_resp]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await asyncio.wait_for(
                agent._llm_with_retry(
                    model="m", messages=[], system_prompt=None, max_tokens=100
                ),
                timeout=5,
            )

        assert result is fake_resp
        assert agent.client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_llm_with_retry_529_overloaded_retries_and_succeeds(self):
        agent = _make_agent(_LlmMixin)
        fake_resp = _fake_anthropic_response(text="after overload")

        overload_err = anthropic.APIStatusError(
            message="overloaded",
            response=MagicMock(status_code=529),
            body={},
        )
        overload_err.status_code = 529
        agent.client.messages.create = AsyncMock(
            side_effect=[overload_err, fake_resp]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await asyncio.wait_for(
                agent._llm_with_retry(
                    model="m", messages=[], system_prompt=None, max_tokens=100
                ),
                timeout=5,
            )

        assert result is fake_resp

    @pytest.mark.asyncio
    async def test_llm_with_retry_non_529_api_error_raises_immediately(self):
        agent = _make_agent(_LlmMixin)

        api_err = anthropic.APIStatusError(
            message="server error",
            response=MagicMock(status_code=500),
            body={},
        )
        api_err.status_code = 500
        agent.client.messages.create = AsyncMock(side_effect=api_err)

        with pytest.raises(anthropic.APIStatusError):
            await asyncio.wait_for(
                agent._llm_with_retry(
                    model="m", messages=[], system_prompt=None, max_tokens=100
                ),
                timeout=5,
            )

        agent.client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_with_retry_exhausted_raises_last_exception(self):
        agent = _make_agent(_LlmMixin)

        rate_err = anthropic.RateLimitError(
            message="always rate limited",
            response=MagicMock(status_code=429),
            body={},
        )
        agent.client.messages.create = AsyncMock(side_effect=rate_err)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(anthropic.RateLimitError):
                await asyncio.wait_for(
                    agent._llm_with_retry(
                        model="m",
                        messages=[],
                        system_prompt=None,
                        max_tokens=100,
                        max_retries=3,
                    ),
                    timeout=5,
                )

        assert agent.client.messages.create.call_count == 3

    # ── _get_ollama_client (module-level singleton) ───────────────────────────

    @pytest.mark.asyncio
    async def test_get_ollama_client_creates_instance_once(self):
        """First call creates the singleton; second call returns the same object."""
        import apps.backend.agents._base._llm_mixin as llm_module

        original_client = llm_module._OLLAMA_CLIENT
        llm_module._OLLAMA_CLIENT = None

        try:
            with patch("openai.AsyncOpenAI") as mock_cls:
                mock_instance = MagicMock()
                mock_cls.return_value = mock_instance

                c1 = await asyncio.wait_for(_get_ollama_client(), timeout=5)
                c2 = await asyncio.wait_for(_get_ollama_client(), timeout=5)

            assert c1 is c2
            mock_cls.assert_called_once()
        finally:
            llm_module._OLLAMA_CLIENT = original_client

    @pytest.mark.asyncio
    async def test_get_ollama_client_returns_existing_when_set(self):
        """If _OLLAMA_CLIENT is already set, no new instance is created."""
        import apps.backend.agents._base._llm_mixin as llm_module

        sentinel = MagicMock()
        original_client = llm_module._OLLAMA_CLIENT
        llm_module._OLLAMA_CLIENT = sentinel

        try:
            with patch("openai.AsyncOpenAI") as mock_cls:
                result = await asyncio.wait_for(_get_ollama_client(), timeout=5)
            assert result is sentinel
            mock_cls.assert_not_called()
        finally:
            llm_module._OLLAMA_CLIENT = original_client


# ─────────────────────────────────────────────────────────────────────────────
# class TestToolsMixin
# ─────────────────────────────────────────────────────────────────────────────

class TestToolsMixin:
    # ── _call_tool: async fn happy path ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_tool_async_fn_success(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value={"result": "ok"})

        result = await asyncio.wait_for(
            agent._call_tool(
                tool_name="search",
                action="query",
                input_params={"q": "test"},
                fn=async_fn,
            ),
            timeout=5,
        )

        assert result == {"result": "ok"}
        async_fn.assert_called_once()
        agent._log_step.assert_called_once()
        agent.memory.log_tool_call.assert_called_once()
        agent._broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_tool_async_fn_increments_counter(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value=42)

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn),
            timeout=5,
        )

        assert agent._tool_call_count == 1

    # ── _call_tool: sync fn ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_tool_sync_fn_success(self):
        agent = _make_agent(_ToolsMixin)
        sync_fn = MagicMock(return_value=99)

        result = await asyncio.wait_for(
            agent._call_tool("calc", "add", {"a": 1}, sync_fn),
            timeout=5,
        )

        assert result == 99
        sync_fn.assert_called_once()

    # ── _call_tool: output serialisation variants ─────────────────────────────

    @pytest.mark.asyncio
    async def test_call_tool_dict_result_logged_as_is(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value={"key": "value"})

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn),
            timeout=5,
        )

        log_kwargs = agent.memory.log_tool_call.call_args[1]
        assert log_kwargs["output_result"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_call_tool_list_result_logged_as_is(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value=[1, 2, 3])

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn),
            timeout=5,
        )

        log_kwargs = agent.memory.log_tool_call.call_args[1]
        assert log_kwargs["output_result"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_call_tool_scalar_result_truncated_to_string(self):
        agent = _make_agent(_ToolsMixin)
        long_str = "x" * 3000
        async_fn = AsyncMock(return_value=long_str)

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn),
            timeout=5,
        )

        log_kwargs = agent.memory.log_tool_call.call_args[1]
        assert len(log_kwargs["output_result"]) == 2000

    @pytest.mark.asyncio
    async def test_call_tool_none_result_logs_none(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value=None)

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn),
            timeout=5,
        )

        log_kwargs = agent.memory.log_tool_call.call_args[1]
        assert log_kwargs["output_result"] is None

    # ── _call_tool: error path ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_call_tool_exception_sets_status_error_and_reraises(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(side_effect=ValueError("bad input"))

        with pytest.raises(ValueError, match="bad input"):
            await asyncio.wait_for(
                agent._call_tool("t", "a", None, async_fn),
                timeout=5,
            )

        # Even on error, logging still runs (finally block)
        agent._log_step.assert_called_once()
        log_call = agent._log_step.call_args[1]
        assert "error" in log_call["description"]
        agent.memory.log_tool_call.assert_called_once()
        mem_kwargs = agent.memory.log_tool_call.call_args[1]
        assert mem_kwargs["status"] == "error"

    @pytest.mark.asyncio
    async def test_call_tool_exception_counter_still_incremented(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(
                agent._call_tool("t", "a", None, async_fn),
                timeout=5,
            )

        assert agent._tool_call_count == 1

    @pytest.mark.asyncio
    async def test_call_tool_passes_args_and_kwargs_to_fn(self):
        agent = _make_agent(_ToolsMixin)
        async_fn = AsyncMock(return_value="done")

        await asyncio.wait_for(
            agent._call_tool("t", "a", None, async_fn, "arg1", key="val"),
            timeout=5,
        )

        async_fn.assert_called_once_with("arg1", key="val")

    # ── spawn_subagent ────────────────────────────────────────────────────────

    def _make_spawnable_agent(self, execute_result: AgentResult):
        """Return an agent whose __class__.__init__ creates a sub-agent with execute mocked."""
        execute_mock = AsyncMock(return_value=execute_result)

        class _SpawnableAgent(_ToolsMixin):
            """Real named class so patch.object can target its __init__."""

            def __init__(self_inner, **kwargs):  # noqa: N805
                # populate the sub-agent with required execute
                self_inner.execute = execute_mock

        # Build parent agent as instance of this class
        agent = _SpawnableAgent()
        agent.name = "test_agent"
        agent.model = "claude-test"
        agent._task_id = "task-001"
        agent._step_counter = 0
        agent._llm_call_count = 0
        agent._tool_call_count = 0
        agent._total_cost = 0.0
        agent._total_tokens = 0
        agent._counters_lock = asyncio.Lock()
        agent._ws_broadcast = None
        agent.memory = MagicMock()
        agent.memory.log_tool_call = AsyncMock()
        agent.memory.log_step = AsyncMock()
        agent.client = MagicMock()
        agent._log_step = AsyncMock(return_value=42)
        agent._broadcast = AsyncMock()
        agent._estimate_cost = MagicMock(return_value=0.001)
        agent._extra_init_kwargs = MagicMock(return_value={})
        return agent

    @pytest.mark.asyncio
    async def test_spawn_subagent_happy_path(self):
        task = AgentTask(
            agent_name="test_agent",
            input_data={"prompt": "do something"},
        )
        expected_result = AgentResult(
            task_id=task.task_id,
            agent_name="test_agent",
            status=TaskStatus.COMPLETED,
            output_data={"done": True},
            tokens_used=100,
            cost_usd=0.005,
        )
        agent = self._make_spawnable_agent(expected_result)

        result = await asyncio.wait_for(
            agent.spawn_subagent(task),
            timeout=5,
        )

        assert result is expected_result
        agent._log_step.assert_called()
        agent._broadcast.assert_called()
        agent.memory.log_step.assert_called_once()
        assert agent._total_cost == pytest.approx(0.005)
        assert agent._total_tokens == 100

    @pytest.mark.asyncio
    async def test_spawn_subagent_updates_cost_and_tokens(self):
        task = AgentTask(agent_name="test_agent", input_data={})
        result_obj = AgentResult(
            task_id=task.task_id,
            agent_name="test_agent",
            status=TaskStatus.COMPLETED,
            tokens_used=200,
            cost_usd=0.01,
        )
        agent = self._make_spawnable_agent(result_obj)
        agent._total_cost = 1.0
        agent._total_tokens = 50

        await asyncio.wait_for(agent.spawn_subagent(task), timeout=5)

        assert agent._total_cost == pytest.approx(1.01)
        assert agent._total_tokens == 250
