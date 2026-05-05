"""Tests for DomainContext dataclass and DOMAIN_ETSY constant."""
from __future__ import annotations

from apps.backend.core.domains import DomainContext, DOMAIN_ETSY


def test_domain_etsy_is_domain_context():
    assert isinstance(DOMAIN_ETSY, DomainContext)


def test_domain_etsy_name():
    assert DOMAIN_ETSY.name == "etsy_store"


def test_domain_etsy_has_objective():
    assert isinstance(DOMAIN_ETSY.objective, str)
    assert len(DOMAIN_ETSY.objective) > 10


def test_domain_etsy_business_rules_non_empty():
    assert isinstance(DOMAIN_ETSY.business_rules, list)
    assert len(DOMAIN_ETSY.business_rules) > 0


def test_domain_etsy_agents_non_empty():
    assert isinstance(DOMAIN_ETSY.agents, dict)
    assert len(DOMAIN_ETSY.agents) > 0


def test_domain_etsy_confidence_threshold_in_range():
    assert 0.0 < DOMAIN_ETSY.confidence_threshold <= 1.0


def test_domain_etsy_confidence_disclaimer_in_range():
    assert 0.0 < DOMAIN_ETSY.confidence_disclaimer <= 1.0


def test_domain_etsy_pipeline_steps_is_list():
    assert isinstance(DOMAIN_ETSY.pipeline_steps, list)


def test_domain_context_custom_instance():
    d = DomainContext(
        name="test",
        objective="test objective",
        business_rules=["rule 1"],
        agents={"agent_a": "does stuff"},
    )
    assert d.name == "test"
    assert d.confidence_threshold == 0.85  # default
    assert d.confidence_disclaimer == 0.60  # default
    assert d.extra_sections == {}
    assert d.pipeline_steps == []
    assert d.learning_triggers == {}
    assert d.clarification_questions == []
