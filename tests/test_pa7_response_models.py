"""PA-7: test TDD per NicheItemResponse e ProductionQueueItemResponse.

Questi test devono FALLIRE prima dell'implementazione (ImportError o AssertionError).
"""
from __future__ import annotations

import pytest

# ── import che DEVONO fallire prima che esistano i model ──────────────────────
from apps.backend.api.routers.etsy import NicheItemResponse, NichesResponse
from apps.backend.api.routers.system import (
    ProductionQueueItemResponse,
    ProductionQueueResponse,
)


# ── NicheItemResponse ──────────────────────────────────────────────────────────


class TestNicheItemResponseFields:
    REQUIRED = {
        "niche", "product_type", "performance_score", "confidence_level",
        "avg_ctr", "total_orders", "total_listings", "total_revenue_eur",
        "last_updated_at", "entry_score", "tier", "avg_price_eur",
        "google_trend_score",
    }
    OPTIONAL_NEW = {"audience_target", "expansion_potential", "section_name"}

    def test_required_fields_present(self):
        fields = set(NicheItemResponse.model_fields.keys())
        missing = self.REQUIRED - fields
        assert not missing, f"Campi mancanti in NicheItemResponse: {missing}"

    def test_optional_new_fields_present(self):
        fields = set(NicheItemResponse.model_fields.keys())
        missing = self.OPTIONAL_NEW - fields
        assert not missing, f"Nuovi campi opzionali mancanti: {missing}"

    def test_optional_new_fields_default_none(self):
        """I nuovi campi hanno default None — non richiedono il dato dalla query."""
        row = dict(
            niche="wall art",
            product_type="print",
            performance_score=0.8,
            confidence_level="high",
            avg_ctr=None,
            total_orders=10,
            total_listings=5,
            total_revenue_eur=100.0,
            last_updated_at=None,
            entry_score=None,
            tier=None,
            avg_price_eur=None,
            google_trend_score=None,
        )
        item = NicheItemResponse(**row)
        assert item.audience_target is None
        assert item.expansion_potential is None
        assert item.section_name is None

    def test_instantiation_with_all_fields(self):
        item = NicheItemResponse(
            niche="wall art",
            product_type="print",
            performance_score=0.8,
            confidence_level="high",
            avg_ctr=0.05,
            total_orders=42,
            total_listings=3,
            total_revenue_eur=210.0,
            last_updated_at=1_700_000_000.0,
            entry_score=0.72,
            tier=1,
            avg_price_eur=9.99,
            google_trend_score=65.0,
            audience_target="home decor lovers",
            expansion_potential=3,
            section_name="Wall Art",
        )
        assert item.niche == "wall art"
        assert item.audience_target == "home decor lovers"
        assert item.section_name == "Wall Art"


class TestNichesResponseWrapper:
    def test_niches_field_is_list(self):
        r = NichesResponse(niches=[])
        assert isinstance(r.niches, list)

    def test_niches_wraps_items(self):
        item = NicheItemResponse(
            niche="n",
            product_type=None,
            performance_score=0.1,
            confidence_level="low",
            avg_ctr=None,
            total_orders=0,
            total_listings=0,
            total_revenue_eur=0.0,
            last_updated_at=None,
            entry_score=None,
            tier=None,
            avg_price_eur=None,
            google_trend_score=None,
        )
        r = NichesResponse(niches=[item])
        assert len(r.niches) == 1


# ── ProductionQueueItemResponse ───────────────────────────────────────────────


class TestProductionQueueItemResponseFields:
    REQUIRED = {
        "id", "task_id", "niche", "product_type", "brief", "status",
        "entry_score", "listing_price", "listing_title", "file_paths",
        "etsy_listing_id", "ads_activated", "created_at", "updated_at",
    }

    def test_required_fields_present(self):
        fields = set(ProductionQueueItemResponse.model_fields.keys())
        missing = self.REQUIRED - fields
        assert not missing, f"Campi mancanti in ProductionQueueItemResponse: {missing}"

    def test_instantiation(self):
        item = ProductionQueueItemResponse(
            id=1,
            task_id="abc-123",
            niche="wall art",
            product_type="print",
            brief=None,
            status="pending_design",
            entry_score=None,
            listing_price=None,
            listing_title=None,
            file_paths=None,
            etsy_listing_id=None,
            ads_activated=None,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert item.id == 1
        assert item.status == "pending_design"


class TestProductionQueueResponseWrapper:
    def test_items_field_is_list(self):
        r = ProductionQueueResponse(items=[])
        assert isinstance(r.items, list)
