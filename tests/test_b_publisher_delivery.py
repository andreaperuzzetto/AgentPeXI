"""B-02: verifica il dispatch layer PUBLISHER_DELIVERY_METHOD."""
from __future__ import annotations

import csv
import datetime
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_publisher():
    """Stub publisher con attributi minimi per _PublishMixin."""
    pub = MagicMock()
    pub.etsy_api = MagicMock()
    pub._call_tool = AsyncMock()
    pub.memory = MagicMock()
    pub.memory.get_db = AsyncMock(return_value=MagicMock())
    return pub


def _listing_kwargs():
    return dict(
        title="Birthday Party Printable",
        description="A lovely party printable",
        price=3.99,
        tags=["party", "birthday"],
        taxonomy_id=2078,
        state="draft",
        type="download",
        who_made="i_did",
        when_made="made_to_order",
        is_digital=True,
        quantity=999,
    )


# ---------------------------------------------------------------------------
# _publish_via_csv_export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_csv_export_creates_csv_file(tmp_path, monkeypatch):
    """csv_export crea il file CSV nella directory csv_drafts."""
    monkeypatch.setenv("PUBLISHER_DELIVERY_METHOD", "csv_export")

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()

    with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_settings:
        mock_settings.STORAGE_PATH = str(tmp_path)
        listing_id = await _PublishMixin._publish_via_csv_export(
            publisher,
            _listing_kwargs(),
            "/tmp/party.pdf",
            "party_printable",
            "printable_pdf",
        )

    date_str = datetime.date.today().isoformat()
    csv_path = tmp_path / "csv_drafts" / f"{date_str}.csv"
    assert csv_path.exists(), f"CSV non creato: {csv_path}"
    assert listing_id.startswith("csv_"), f"listing_id deve iniziare con 'csv_', ottenuto: {listing_id!r}"


@pytest.mark.asyncio
async def test_csv_export_contains_listing_title(tmp_path, monkeypatch):
    """Il CSV contiene il titolo del listing."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    kwargs = _listing_kwargs()

    with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_settings:
        mock_settings.STORAGE_PATH = str(tmp_path)
        await _PublishMixin._publish_via_csv_export(
            publisher, kwargs, "/tmp/party.pdf", "party_printable", "printable_pdf"
        )

    date_str = datetime.date.today().isoformat()
    csv_path = tmp_path / "csv_drafts" / f"{date_str}.csv"
    content = csv_path.read_text(encoding="utf-8")
    assert kwargs["title"] in content, f"Titolo mancante nel CSV. Contenuto:\n{content}"


@pytest.mark.asyncio
async def test_csv_export_appends_on_second_call(tmp_path, monkeypatch):
    """Due chiamate csv_export producono due righe (non sovrascrive)."""
    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()

    with patch("apps.backend.agents._publisher._publish_mixin.settings") as mock_settings:
        mock_settings.STORAGE_PATH = str(tmp_path)
        await _PublishMixin._publish_via_csv_export(
            publisher, _listing_kwargs(), "/tmp/a.pdf", "niche_a", "printable_pdf"
        )
        kwargs2 = _listing_kwargs()
        kwargs2["title"] = "Second Listing Title"
        await _PublishMixin._publish_via_csv_export(
            publisher, kwargs2, "/tmp/b.pdf", "niche_b", "printable_pdf"
        )

    date_str = datetime.date.today().isoformat()
    csv_path = tmp_path / "csv_drafts" / f"{date_str}.csv"
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 2, f"Attese 2 righe nel CSV, trovate {len(rows)}"


# ---------------------------------------------------------------------------
# _publish_via_make_webhook
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_make_webhook_raises_if_url_not_set(monkeypatch):
    """make_webhook solleva RuntimeError se MAKE_WEBHOOK_URL non è configurata."""
    monkeypatch.delenv("MAKE_WEBHOOK_URL", raising=False)

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    with pytest.raises(RuntimeError, match="MAKE_WEBHOOK_URL"):
        await _PublishMixin._publish_via_make_webhook(
            publisher, _listing_kwargs(), "/tmp/party.pdf", "party_printable", "printable_pdf"
        )


@pytest.mark.asyncio
async def test_make_webhook_posts_to_url_and_returns_make_id(monkeypatch):
    """make_webhook POSTa al webhook URL e ritorna listing_id con prefisso 'make_'."""
    from apps.backend.agents._publisher import _publish_mixin as _mixin_mod

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()

    # Mock aiohttp async context manager chain
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(_mixin_mod.settings, "MAKE_WEBHOOK_URL", "http://hook.make.com/test123"), \
            patch("aiohttp.ClientSession", return_value=mock_session):
        listing_id = await _PublishMixin._publish_via_make_webhook(
            publisher, _listing_kwargs(), "/tmp/party.pdf", "party_printable", "printable_pdf"
        )

    assert listing_id.startswith("make_"), f"listing_id deve iniziare con 'make_', ottenuto: {listing_id!r}"
    mock_session.post.assert_called_once()
    called_url = mock_session.post.call_args[0][0]
    assert called_url == "http://hook.make.com/test123"


# ---------------------------------------------------------------------------
# _dispatch_publish — routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_defaults_to_csv_export(tmp_path, monkeypatch):
    """Senza env var, _dispatch_publish usa csv_export (default sicuro)."""
    monkeypatch.delenv("PUBLISHER_DELIVERY_METHOD", raising=False)

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    publisher._publish_via_csv_export = AsyncMock(return_value="csv_20240101_120000")

    listing_id, uploaded = await _PublishMixin._dispatch_publish(
        publisher, _listing_kwargs(), "/tmp/party.pdf", [], "party_printable", "printable_pdf"
    )

    assert listing_id.startswith("csv_")
    assert uploaded == 0


@pytest.mark.asyncio
async def test_dispatch_csv_export_returns_zero_uploads(tmp_path, monkeypatch):
    """csv_export restituisce images_uploaded=0 (nessun upload Etsy)."""
    monkeypatch.setenv("PUBLISHER_DELIVERY_METHOD", "csv_export")

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    publisher._publish_via_csv_export = AsyncMock(return_value="csv_20240101_120001")

    listing_id, uploaded = await _PublishMixin._dispatch_publish(
        publisher, _listing_kwargs(), "/tmp/party.pdf", [], "party_printable", "printable_pdf"
    )

    assert uploaded == 0


@pytest.mark.asyncio
async def test_dispatch_make_webhook_returns_make_id(monkeypatch):
    """make_webhook via _dispatch_publish ritorna listing_id con prefisso 'make_'."""
    monkeypatch.setenv("PUBLISHER_DELIVERY_METHOD", "make_webhook")

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    publisher._publish_via_make_webhook = AsyncMock(return_value="make_1234567890")

    listing_id, uploaded = await _PublishMixin._dispatch_publish(
        publisher, _listing_kwargs(), "/tmp/party.pdf", [], "party_printable", "printable_pdf",
        method="make_webhook",
    )

    assert listing_id.startswith("make_")
    assert uploaded == 0


@pytest.mark.asyncio
async def test_dispatch_etsy_api_calls_publish_via_etsy(monkeypatch):
    """etsy_api via _dispatch_publish chiama _publish_via_etsy_api."""
    monkeypatch.setenv("PUBLISHER_DELIVERY_METHOD", "etsy_api")

    from apps.backend.agents._publisher._publish_mixin import _PublishMixin

    publisher = _make_publisher()
    publisher._publish_via_etsy_api = AsyncMock(return_value=("etsy_9999", 2))

    listing_id, uploaded = await _PublishMixin._dispatch_publish(
        publisher, _listing_kwargs(), "/tmp/party.pdf", [], "party_printable", "printable_pdf",
        method="etsy_api",
    )

    assert listing_id == "etsy_9999"
    assert uploaded == 2
    publisher._publish_via_etsy_api.assert_called_once()
