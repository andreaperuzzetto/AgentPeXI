"""Tests for ConfidenceMixin._voice_error_phrase and LlmMixin._is_personal_intent."""
from __future__ import annotations

import pytest

from apps.backend.core._pepe._confidence import ConfidenceMixin
from apps.backend.core._pepe._llm import LlmMixin, PERSONAL_INTENT_PATTERNS


# ---------------------------------------------------------------------------
# _voice_error_phrase — pure static lookup (no LLM)
# ---------------------------------------------------------------------------

class _FakeConf(ConfidenceMixin):
    pass


@pytest.fixture
def conf():
    return _FakeConf()


def test_voice_error_when_keyword(conf):
    result = conf._voice_error_phrase("quando vuoi il reminder?")
    assert "quando" in result.lower() or "ripetere" in result.lower()


def test_voice_error_missing_field(conf):
    result = conf._voice_error_phrase("campo mancante nel task")
    assert "ripetere" in result.lower()


def test_voice_error_timeout(conf):
    result = conf._voice_error_phrase("request timed out after 30s")
    assert "troppo" in result.lower() or "riprova" in result.lower()


def test_voice_error_network(conf):
    result = conf._voice_error_phrase("network unreachable")
    assert "connessione" in result.lower()


def test_voice_error_duplicate(conf):
    result = conf._voice_error_phrase("duplicato: già un reminder simile")
    assert "simile" in result.lower() or "già" in result.lower()


def test_voice_error_notion(conf):
    result = conf._voice_error_phrase("notion sync failed")
    assert "sincronizzazione" in result.lower() or "fatto" in result.lower()


def test_voice_error_auth(conf):
    result = conf._voice_error_phrase("unauthorized: credenziali errate")
    assert "credenziali" in result.lower()


def test_voice_error_default_fallback(conf):
    result = conf._voice_error_phrase("some completely unknown error xyz123")
    assert "ripetere" in result.lower() or "riuscito" in result.lower()


def test_voice_error_returns_string(conf):
    assert isinstance(conf._voice_error_phrase("error"), str)


def test_voice_error_missing_keyword(conf):
    result = conf._voice_error_phrase("valore mancante nel body")
    assert "ripetere" in result.lower()


# ---------------------------------------------------------------------------
# LlmMixin._is_personal_intent (pure sync pattern matching)
# ---------------------------------------------------------------------------

class _FakeLlm(LlmMixin):
    pass


@pytest.fixture
def llm():
    return _FakeLlm()


def test_is_personal_intent_reminder(llm):
    assert llm._is_personal_intent("ricordami di chiamare Mario")


def test_is_personal_intent_recall(llm):
    assert llm._is_personal_intent("cosa stavo guardando ieri?")


def test_is_personal_intent_summarize(llm):
    assert llm._is_personal_intent("riassumi questo articolo")


def test_is_personal_intent_gmail(llm):
    assert llm._is_personal_intent("manda una mail a Marco")


def test_is_personal_intent_notion(llm):
    assert llm._is_personal_intent("appunta questa idea su notion")


def test_is_personal_intent_calendar(llm):
    assert llm._is_personal_intent("crea un evento in calendario per domani")


def test_is_personal_intent_false_business(llm):
    assert not llm._is_personal_intent("analizza le performance del listing")


def test_is_personal_intent_false_empty(llm):
    assert not llm._is_personal_intent("")


def test_is_personal_intent_false_create_product(llm):
    assert not llm._is_personal_intent("crea un prodotto per la nicchia planner")


def test_is_personal_intent_returns_bool(llm):
    result = llm._is_personal_intent("ricordami domani")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# PERSONAL_INTENT_PATTERNS constant checks
# ---------------------------------------------------------------------------

def test_personal_intent_patterns_nonempty():
    assert len(PERSONAL_INTENT_PATTERNS) >= 5


def test_personal_intent_patterns_are_tuples():
    for item in PERSONAL_INTENT_PATTERNS:
        assert isinstance(item, tuple)
        assert len(item) == 2
