"""Tests for PublicationPolicy and _parse_hhmm using real in-memory SQLite."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from apps.backend.core.publication_policy import PublicationPolicy, _parse_hhmm

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS production_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL DEFAULT (hex(randomblob(8))),
    product_type TEXT NOT NULL DEFAULT 'printable_pdf',
    niche TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned',
    published_at REAL,
    scheduled_publish_at REAL
);
"""


@pytest.fixture
async def db():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn


@pytest.fixture
async def policy(db):
    p = PublicationPolicy(db)
    await p.ensure_defaults()
    return p


# ---------------------------------------------------------------------------
# _parse_hhmm — pure function
# ---------------------------------------------------------------------------

def test_parse_hhmm_valid():
    assert _parse_hhmm("08:30") == (8, 30)


def test_parse_hhmm_midnight():
    assert _parse_hhmm("00:00") == (0, 0)


def test_parse_hhmm_end_of_day():
    assert _parse_hhmm("23:59") == (23, 59)


def test_parse_hhmm_invalid_returns_0_0():
    assert _parse_hhmm("bad") == (0, 0)


def test_parse_hhmm_wraps_hour():
    assert _parse_hhmm("25:00") == (1, 0)  # 25 % 24 == 1


def test_parse_hhmm_wraps_minute():
    assert _parse_hhmm("10:65") == (10, 5)  # 65 % 60 == 5


# ---------------------------------------------------------------------------
# ensure_defaults
# ---------------------------------------------------------------------------

async def test_ensure_defaults_inserts_policy_keys(db):
    p = PublicationPolicy(db)
    await p.ensure_defaults()
    cursor = await db.execute("SELECT COUNT(*) FROM config WHERE key LIKE 'policy.%'")
    row = await cursor.fetchone()
    assert row[0] >= 5


async def test_ensure_defaults_idempotent(db):
    p = PublicationPolicy(db)
    await p.ensure_defaults()
    await p.ensure_defaults()
    cursor = await db.execute("SELECT COUNT(*) FROM config WHERE key='policy.max_per_day'")
    row = await cursor.fetchone()
    assert row[0] == 1


# ---------------------------------------------------------------------------
# published_today_count / can_publish_today
# ---------------------------------------------------------------------------

async def test_published_today_count_zero_when_empty(policy, db):
    assert await policy.published_today_count() == 0


async def test_published_today_count_counts_todays_records(policy, db):
    now = time.time()
    await db.execute(
        "INSERT INTO production_queue (niche, status, published_at) VALUES (?,?,?)",
        ("planner", "published", now),
    )
    await db.commit()
    assert await policy.published_today_count() == 1


async def test_published_today_count_ignores_old_records(policy, db):
    old = time.time() - 2 * 86400
    await db.execute(
        "INSERT INTO production_queue (niche, status, published_at) VALUES (?,?,?)",
        ("planner", "published", old),
    )
    await db.commit()
    assert await policy.published_today_count() == 0


async def test_can_publish_today_true_when_under_limit(policy):
    assert await policy.can_publish_today() is True


async def test_can_publish_today_false_when_at_limit(policy, db):
    now = time.time()
    for i in range(5):
        await db.execute(
            "INSERT INTO production_queue (niche, status, published_at) VALUES (?,?,?)",
            (f"niche{i}", "published", now),
        )
    await db.commit()
    assert await policy.can_publish_today() is False


# ---------------------------------------------------------------------------
# is_in_availability_window
# ---------------------------------------------------------------------------

async def test_in_window_midday(policy):
    dt = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    assert await policy.is_in_availability_window(dt) is True


async def test_out_of_window_before_start(policy):
    dt = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
    assert await policy.is_in_availability_window(dt) is False


async def test_custom_window_respected(policy, db):
    await db.execute(
        "UPDATE config SET value='10:00' WHERE key='policy.availability_start'"
    )
    await db.execute(
        "UPDATE config SET value='12:00' WHERE key='policy.availability_end'"
    )
    await db.commit()
    dt_in = datetime.now().replace(hour=11, minute=0)
    dt_out = datetime.now().replace(hour=13, minute=0)
    assert await policy.is_in_availability_window(dt_in) is True
    assert await policy.is_in_availability_window(dt_out) is False


# ---------------------------------------------------------------------------
# niche_on_cooldown
# ---------------------------------------------------------------------------

async def test_niche_not_on_cooldown_when_empty(policy):
    assert await policy.niche_on_cooldown("planner") is False


async def test_niche_on_cooldown_recent_publish(policy, db):
    await db.execute(
        "INSERT INTO production_queue (niche, status, published_at) VALUES (?,?,?)",
        ("planner", "published", time.time() - 86400),
    )
    await db.commit()
    assert await policy.niche_on_cooldown("planner") is True


async def test_niche_not_on_cooldown_expired(policy, db):
    old = time.time() - 10 * 86400
    await db.execute(
        "INSERT INTO production_queue (niche, status, published_at) VALUES (?,?,?)",
        ("planner", "published", old),
    )
    await db.commit()
    assert await policy.niche_on_cooldown("planner") is False


# ---------------------------------------------------------------------------
# ads_enabled / ads_daily_budget
# ---------------------------------------------------------------------------

async def test_ads_disabled_by_default(policy):
    assert await policy.ads_enabled() is False


async def test_ads_enabled_after_config(policy, db):
    await db.execute(
        "UPDATE config SET value='true' WHERE key='policy.etsy_ads_on_publish'"
    )
    await db.commit()
    assert await policy.ads_enabled() is True


async def test_ads_daily_budget_default(policy):
    budget = await policy.ads_daily_budget()
    assert budget == 1.00


# ---------------------------------------------------------------------------
# get_all / set_config
# ---------------------------------------------------------------------------

async def test_get_all_returns_policy_keys(policy):
    all_config = await policy.get_all()
    assert "policy.max_per_day" in all_config
    assert "policy.min_gap_hours" in all_config


async def test_set_config_updates_value(policy):
    await policy.set_config("max_per_day", "10")
    val = await policy._get_int("policy.max_per_day", 5)
    assert val == 10


async def test_set_config_with_full_key(policy):
    await policy.set_config("policy.max_per_day", "3")
    val = await policy._get_int("policy.max_per_day", 5)
    assert val == 3


# ---------------------------------------------------------------------------
# next_available_slot
# ---------------------------------------------------------------------------

async def test_next_available_slot_returns_datetime(policy):
    slot = await policy.next_available_slot()
    assert isinstance(slot, datetime)


async def test_next_available_slot_after_from_dt(policy):
    from_dt = datetime.now().replace(hour=8, minute=0)
    slot = await policy.next_available_slot(from_dt)
    assert slot >= from_dt
