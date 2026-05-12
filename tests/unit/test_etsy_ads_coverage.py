"""tests/unit/test_etsy_ads_coverage.py — ≥80% coverage su etsy_ads.py.

# MOCK CONTRACT - etsy_ads.py
# EtsyAdsManager.__init__(etsy_client, production_queue, publication_policy,
#                          telegram_broadcaster=None, mock_mode=False)
#
# Metodi pubblici async:
#   activate_ad(listing_id, daily_budget_eur) → bool
#   pause_ad(listing_id, item_id=None) → bool
#   get_ad_stats(listing_id) → dict
#   auto_manage_ads() → {activated, paused, errors, mock}
#
# Metodo privato async:
#   _send_summary(activated, paused, activated_titles, paused_titles) → None
#
# Dipendenze esterne (tutte iniettate via costruttore — nessuna HTTP diretta):
#   etsy_client  (EtsyAPI):            .create_ad_campaign(), .pause_ad_campaign(), .get_listing_ad_stats()
#   production_queue (ProductionQueueService): .get_recent(), .set_ads_activated(), .set_ads_paused()
#   publication_policy (PublicationPolicy):    .ads_enabled(), .ads_daily_budget()
#   telegram_broadcaster: callable async
#
# Costanti chiave (usate negli assert):
#   _ADS_CTR_PAUSE_THRESHOLD = 0.015   (1.5%)
#   _ADS_EVAL_DAYS = 7
#   _ADS_ACTIVATE_WINDOW_DAYS = 14
#   _ADS_HISTORY_DAYS = 30
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from apps.backend.core.etsy_ads import EtsyAdsManager

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    etsy_client=None,
    production_queue=None,
    publication_policy=None,
    telegram_broadcaster=None,
    mock_mode: bool = False,
) -> EtsyAdsManager:
    return EtsyAdsManager(
        etsy_client=etsy_client,
        production_queue=production_queue,
        publication_policy=publication_policy,
        telegram_broadcaster=telegram_broadcaster,
        mock_mode=mock_mode,
    )


def _make_etsy_client() -> MagicMock:
    client = MagicMock()
    client.create_ad_campaign = AsyncMock()
    client.pause_ad_campaign  = AsyncMock()
    client.get_listing_ad_stats = AsyncMock(return_value={
        "listing_id": "999",
        "impressions": 100,
        "clicks": 10,
        "spend_eur": 1.5,
        "orders": 1,
    })
    return client


def _make_queue(items=None) -> MagicMock:
    q = MagicMock()
    q.get_recent       = AsyncMock(return_value=items or [])
    q.set_ads_activated = AsyncMock()
    q.set_ads_paused    = AsyncMock()
    return q


def _make_policy(ads_enabled: bool = True, daily_budget: float = 1.50) -> MagicMock:
    p = MagicMock()
    p.ads_enabled     = AsyncMock(return_value=ads_enabled)
    p.ads_daily_budget = AsyncMock(return_value=daily_budget)
    return p


def _make_item(
    *,
    listing_id: str | None = "12345",
    ads_activated: bool = False,
    published_at: float | None = None,
    item_id: int = 1,
    title: str = "Test Listing",
) -> MagicMock:
    now = time.time()
    item = MagicMock()
    item.etsy_listing_id = listing_id
    item.ads_activated   = ads_activated
    item.published_at    = published_at if published_at is not None else now - (2 * 86400)  # 2 giorni fa
    item.id              = item_id
    item.listing_title   = title
    return item


# ===========================================================================
# activate_ad
# ===========================================================================


async def test_activate_ad_no_client_returns_false():
    mgr = _make_manager()
    result = await asyncio.wait_for(mgr.activate_ad("123", 1.50), timeout=5)
    assert result is False


async def test_activate_ad_success_returns_true():
    client = _make_etsy_client()
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.activate_ad("123", 1.50), timeout=5)
    assert result is True
    client.create_ad_campaign.assert_awaited_once_with(listing_id="123", daily_budget_eur=1.50)


async def test_activate_ad_mock_mode_returns_true():
    client = _make_etsy_client()
    mgr = _make_manager(etsy_client=client, mock_mode=True)
    result = await asyncio.wait_for(mgr.activate_ad(42, 2.00), timeout=5)
    assert result is True


async def test_activate_ad_api_raises_returns_false():
    client = _make_etsy_client()
    client.create_ad_campaign = AsyncMock(side_effect=RuntimeError("API down"))
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.activate_ad("999", 1.00), timeout=5)
    assert result is False


# ===========================================================================
# pause_ad
# ===========================================================================


async def test_pause_ad_no_client_returns_false():
    mgr = _make_manager()
    result = await asyncio.wait_for(mgr.pause_ad("123"), timeout=5)
    assert result is False


async def test_pause_ad_success_no_item_id():
    client = _make_etsy_client()
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.pause_ad("123"), timeout=5)
    assert result is True
    client.pause_ad_campaign.assert_awaited_once_with(listing_id="123")


async def test_pause_ad_success_with_item_id_updates_db():
    client = _make_etsy_client()
    queue = _make_queue()
    mgr = _make_manager(etsy_client=client, production_queue=queue)
    result = await asyncio.wait_for(mgr.pause_ad("123", item_id=7), timeout=5)
    assert result is True
    queue.set_ads_paused.assert_awaited_once_with(7)


async def test_pause_ad_item_id_no_queue_still_true():
    client = _make_etsy_client()
    mgr = _make_manager(etsy_client=client, production_queue=None)
    result = await asyncio.wait_for(mgr.pause_ad("123", item_id=7), timeout=5)
    assert result is True  # no crash even without queue


async def test_pause_ad_db_update_raises_still_returns_true():
    client = _make_etsy_client()
    queue = _make_queue()
    queue.set_ads_paused = AsyncMock(side_effect=Exception("DB error"))
    mgr = _make_manager(etsy_client=client, production_queue=queue)
    result = await asyncio.wait_for(mgr.pause_ad("123", item_id=3), timeout=5)
    assert result is True  # DB error non propaga


async def test_pause_ad_api_raises_returns_false():
    client = _make_etsy_client()
    client.pause_ad_campaign = AsyncMock(side_effect=RuntimeError("timeout"))
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.pause_ad("123"), timeout=5)
    assert result is False


async def test_pause_ad_mock_mode():
    client = _make_etsy_client()
    mgr = _make_manager(etsy_client=client, mock_mode=True)
    result = await asyncio.wait_for(mgr.pause_ad("999"), timeout=5)
    assert result is True


# ===========================================================================
# get_ad_stats
# ===========================================================================


async def test_get_ad_stats_no_client_returns_zeros():
    mgr = _make_manager()
    result = await asyncio.wait_for(mgr.get_ad_stats("123"), timeout=5)
    assert result["impressions"] == 0
    assert result["clicks"] == 0
    assert result["spend_eur"] == 0.0
    assert result["orders"] == 0
    assert result["listing_id"] == "123"


async def test_get_ad_stats_success_returns_api_dict():
    client = _make_etsy_client()
    expected = {"listing_id": "123", "impressions": 500, "clicks": 25, "spend_eur": 3.0, "orders": 2}
    client.get_listing_ad_stats = AsyncMock(return_value=expected)
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.get_ad_stats("123"), timeout=5)
    assert result == expected


async def test_get_ad_stats_api_raises_returns_zeros():
    client = _make_etsy_client()
    client.get_listing_ad_stats = AsyncMock(side_effect=Exception("network error"))
    mgr = _make_manager(etsy_client=client)
    result = await asyncio.wait_for(mgr.get_ad_stats("123"), timeout=5)
    assert result["impressions"] == 0
    assert result["listing_id"] == "123"


# ===========================================================================
# auto_manage_ads — scaffold / early exits
# ===========================================================================


async def test_auto_manage_ads_no_queue_returns_zeros():
    mgr = _make_manager()
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result == {"activated": 0, "paused": 0, "errors": 0, "mock": False}


async def test_auto_manage_ads_get_recent_raises_returns_error():
    queue = _make_queue()
    queue.get_recent = AsyncMock(side_effect=Exception("DB gone"))
    mgr = _make_manager(production_queue=queue)
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["errors"] == 1
    assert result["activated"] == 0


async def test_auto_manage_ads_mock_flag_in_result():
    queue = _make_queue()
    mgr = _make_manager(production_queue=queue, mock_mode=True)
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["mock"] is True


async def test_auto_manage_ads_no_listing_id_skips():
    item = _make_item(listing_id=None)
    queue = _make_queue(items=[item])
    mgr = _make_manager(production_queue=queue)
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["activated"] == 0
    assert result["errors"] == 0


async def test_auto_manage_ads_no_policy_ads_disabled():
    """Senza publication_policy, ads_enabled resta False → nessuna attivazione."""
    item = _make_item(ads_activated=False)  # listing nuovo (2gg fa)
    queue = _make_queue(items=[item])
    mgr = _make_manager(production_queue=queue, publication_policy=None)
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["activated"] == 0


async def test_auto_manage_ads_policy_raises_uses_defaults():
    """Se policy.ads_enabled() solleva, ads_enabled resta False."""
    item = _make_item(ads_activated=False)
    queue = _make_queue(items=[item])
    policy = _make_policy()
    policy.ads_enabled = AsyncMock(side_effect=Exception("policy broken"))
    mgr = _make_manager(production_queue=queue, publication_policy=policy)
    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["activated"] == 0  # default ads_enabled=False


# ===========================================================================
# auto_manage_ads — attivazione automatica
# ===========================================================================


async def test_auto_manage_ads_activates_new_listing():
    client = _make_etsy_client()
    item   = _make_item(ads_activated=False)  # published 2gg fa → < 14gg
    queue  = _make_queue(items=[item])
    policy = _make_policy(ads_enabled=True, daily_budget=1.50)
    mgr    = _make_manager(etsy_client=client, production_queue=queue, publication_policy=policy)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)

    assert result["activated"] == 1
    assert result["errors"] == 0
    queue.set_ads_activated.assert_awaited_once_with(item.id)


async def test_auto_manage_ads_activate_fails_increments_errors():
    client = _make_etsy_client()
    client.create_ad_campaign = AsyncMock(side_effect=Exception("API fail"))
    item   = _make_item(ads_activated=False)
    queue  = _make_queue(items=[item])
    policy = _make_policy(ads_enabled=True)
    mgr    = _make_manager(etsy_client=client, production_queue=queue, publication_policy=policy)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)

    assert result["activated"] == 0
    assert result["errors"] == 1


async def test_auto_manage_ads_old_listing_no_activate():
    """Listing più vecchio di 14gg non deve essere attivato."""
    item = _make_item(
        ads_activated=False,
        published_at=time.time() - (15 * 86400),  # 15 giorni fa
    )
    queue  = _make_queue(items=[item])
    policy = _make_policy(ads_enabled=True)
    mgr    = _make_manager(production_queue=queue, publication_policy=policy)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["activated"] == 0


async def test_auto_manage_ads_set_ads_activated_raises_no_crash():
    client = _make_etsy_client()
    item   = _make_item(ads_activated=False)
    queue  = _make_queue(items=[item])
    queue.set_ads_activated = AsyncMock(side_effect=Exception("DB locked"))
    policy = _make_policy(ads_enabled=True)
    mgr    = _make_manager(etsy_client=client, production_queue=queue, publication_policy=policy)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["activated"] == 1  # attivazione conta comunque


# ===========================================================================
# auto_manage_ads — pausa automatica CTR
# ===========================================================================


async def test_auto_manage_ads_pauses_low_ctr():
    client = _make_etsy_client()
    client.get_listing_ad_stats = AsyncMock(return_value={
        "listing_id": "12345", "impressions": 100, "clicks": 1, "spend_eur": 1.0, "orders": 0,
    })  # CTR = 1% < 1.5%

    item = _make_item(
        ads_activated=True,
        published_at=time.time() - (8 * 86400),  # 8gg fa → ≥ 7gg
    )
    queue = _make_queue(items=[item])
    mgr   = _make_manager(etsy_client=client, production_queue=queue)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)

    assert result["paused"] == 1
    assert result["errors"] == 0
    client.pause_ad_campaign.assert_awaited()


async def test_auto_manage_ads_no_pause_high_ctr():
    client = _make_etsy_client()
    client.get_listing_ad_stats = AsyncMock(return_value={
        "listing_id": "12345", "impressions": 100, "clicks": 5, "spend_eur": 1.0, "orders": 0,
    })  # CTR = 5% > 1.5%

    item = _make_item(
        ads_activated=True,
        published_at=time.time() - (8 * 86400),
    )
    queue = _make_queue(items=[item])
    mgr   = _make_manager(etsy_client=client, production_queue=queue)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)

    assert result["paused"] == 0
    assert result["errors"] == 0


async def test_auto_manage_ads_too_few_impressions_skip():
    client = _make_etsy_client()
    client.get_listing_ad_stats = AsyncMock(return_value={
        "listing_id": "12345", "impressions": 5, "clicks": 0, "spend_eur": 0.0, "orders": 0,
    })  # < 10 impressions

    item = _make_item(
        ads_activated=True,
        published_at=time.time() - (8 * 86400),
    )
    queue = _make_queue(items=[item])
    mgr   = _make_manager(etsy_client=client, production_queue=queue)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["paused"] == 0
    assert result["errors"] == 0


async def test_auto_manage_ads_pause_fails_increments_errors():
    client = _make_etsy_client()
    client.get_listing_ad_stats = AsyncMock(return_value={
        "listing_id": "12345", "impressions": 100, "clicks": 0, "spend_eur": 0.0, "orders": 0,
    })
    client.pause_ad_campaign = AsyncMock(side_effect=Exception("pause fail"))

    item  = _make_item(ads_activated=True, published_at=time.time() - (8 * 86400))
    queue = _make_queue(items=[item])
    mgr   = _make_manager(etsy_client=client, production_queue=queue)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["paused"] == 0
    assert result["errors"] == 1


async def test_auto_manage_ads_ctr_eval_raises_increments_errors():
    """Se get_ad_stats solleva un'eccezione non gestita internamente, errors++."""
    client = _make_etsy_client()
    # Facciamo sollevare da get_listing_ad_stats dopo il retry interno
    # (get_ad_stats cattura, ma il blocco try nel loop cattura di nuovo)
    item = _make_item(ads_activated=True, published_at=time.time() - (8 * 86400))
    queue = _make_queue(items=[item])

    mgr = _make_manager(etsy_client=client, production_queue=queue)
    # Iniettiamo un'eccezione direttamente in get_ad_stats sovrascrivendolo
    async def _bad_get_ad_stats(_self, lid):
        raise RuntimeError("unexpected")

    import types
    mgr.get_ad_stats = types.MethodType(lambda self, lid: (_ for _ in ()).throw(RuntimeError("unexpected")), mgr)  # noqa

    # Usiamo patch diretta via AsyncMock
    from unittest.mock import patch
    with patch.object(mgr, "get_ad_stats", new=AsyncMock(side_effect=RuntimeError("unexpected"))):
        result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)

    assert result["errors"] == 1


async def test_auto_manage_ads_ads_activated_too_recent_no_eval():
    """Listing con ads attive ma pubblicato da < 7gg — non valutare CTR."""
    client = _make_etsy_client()
    item = _make_item(
        ads_activated=True,
        published_at=time.time() - (3 * 86400),  # 3gg fa < 7gg
    )
    queue = _make_queue(items=[item])
    mgr   = _make_manager(etsy_client=client, production_queue=queue)

    result = await asyncio.wait_for(mgr.auto_manage_ads(), timeout=5)
    assert result["paused"] == 0
    client.get_listing_ad_stats.assert_not_called()


# ===========================================================================
# _send_summary
# ===========================================================================


async def test_send_summary_no_actions_no_telegram():
    broadcaster = AsyncMock()
    mgr = _make_manager(telegram_broadcaster=broadcaster)
    await asyncio.wait_for(mgr._send_summary(0, 0, [], []), timeout=5)
    broadcaster.assert_not_awaited()


async def test_send_summary_with_activated_calls_telegram():
    broadcaster = AsyncMock()
    mgr = _make_manager(telegram_broadcaster=broadcaster)
    await asyncio.wait_for(mgr._send_summary(1, 0, ["Listing A"], []), timeout=5)
    broadcaster.assert_awaited_once()
    msg = broadcaster.call_args[0][0]
    assert "Attivate" in msg
    assert "Listing A" in msg


async def test_send_summary_with_paused_calls_telegram():
    broadcaster = AsyncMock()
    mgr = _make_manager(telegram_broadcaster=broadcaster)
    await asyncio.wait_for(mgr._send_summary(0, 1, [], ["Listing B (CTR ads 0.5%)"]), timeout=5)
    broadcaster.assert_awaited_once()
    msg = broadcaster.call_args[0][0]
    assert "pausa" in msg
    assert "Listing B" in msg


async def test_send_summary_mock_mode_badge():
    broadcaster = AsyncMock()
    mgr = _make_manager(telegram_broadcaster=broadcaster, mock_mode=True)
    await asyncio.wait_for(mgr._send_summary(1, 0, ["X"], []), timeout=5)
    msg = broadcaster.call_args[0][0]
    assert "mock" in msg


async def test_send_summary_no_broadcaster_no_crash():
    mgr = _make_manager(telegram_broadcaster=None)
    # Non deve sollevare
    await asyncio.wait_for(mgr._send_summary(1, 1, ["A"], ["B"]), timeout=5)


async def test_send_summary_telegram_raises_no_crash():
    broadcaster = AsyncMock(side_effect=Exception("telegram down"))
    mgr = _make_manager(telegram_broadcaster=broadcaster)
    await asyncio.wait_for(mgr._send_summary(1, 0, ["A"], []), timeout=5)  # non deve sollevare


async def test_send_summary_many_activated_titles_truncated():
    broadcaster = AsyncMock()
    mgr    = _make_manager(telegram_broadcaster=broadcaster)
    titles = [f"Listing {i}" for i in range(8)]  # 8 > 5
    await asyncio.wait_for(mgr._send_summary(8, 0, titles, []), timeout=5)
    msg = broadcaster.call_args[0][0]
    assert "e altri 3" in msg


async def test_send_summary_many_paused_titles_truncated():
    broadcaster = AsyncMock()
    mgr    = _make_manager(telegram_broadcaster=broadcaster)
    titles = [f"Listing {i} (CTR 0.5%)" for i in range(7)]  # 7 > 5
    await asyncio.wait_for(mgr._send_summary(0, 7, [], titles), timeout=5)
    msg = broadcaster.call_args[0][0]
    assert "e altri 2" in msg


async def test_send_summary_both_activated_and_paused():
    broadcaster = AsyncMock()
    mgr = _make_manager(telegram_broadcaster=broadcaster)
    await asyncio.wait_for(
        mgr._send_summary(2, 3, ["A", "B"], ["C (CTR 0.1%)", "D (CTR 0.2%)", "E (CTR 0.3%)"]),
        timeout=5,
    )
    broadcaster.assert_awaited_once()
    msg = broadcaster.call_args[0][0]
    assert "Attivate" in msg
    assert "pausa" in msg
