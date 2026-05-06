import json
import logging
import time as _time
from typing import Annotated, Literal

import apps.backend.api.state as state
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator


class NicheItemResponse(BaseModel):
    niche: str
    product_type: str | None
    performance_score: float
    confidence_level: str
    avg_ctr: float | None
    total_orders: int | None
    total_listings: int | None
    total_revenue_eur: float | None
    last_updated_at: float | None
    entry_score: float | None
    tier: int | None
    avg_price_eur: float | None
    google_trend_score: float | None
    # nuovi campi PA-7 (opzionali — la query non li popola ancora)
    audience_target: str | None = None
    expansion_potential: int | None = None

    @field_validator("expansion_potential", mode="before")
    @classmethod
    def _coerce_expansion_potential(cls, v: object) -> int | None:
        """Coerce DB TEXT→int; graceful fallback for legacy 'high'/'medium'/'low'."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (ValueError, TypeError):
            return None  # 'high'/'medium'/'low' → None during transition
    section_name: str | None = None


class NichesResponse(BaseModel):
    niches: list[NicheItemResponse]


class SectionItemResponse(BaseModel):
    section_id: str
    section_name: str
    listing_count: int
    last_listing_at: str | None
    pending_uncategorized: int


class SectionsResponse(BaseModel):
    sections: list[SectionItemResponse]


class StyleGuideOptionResponse(BaseModel):
    id: int
    aesthetic_name: str
    palette_primary: str
    palette_secondary: str
    palette_accent: str
    mockup_style: str
    tone: str
    logo_path: str | None
    banner_path: str | None
    is_active: bool


class StyleGuideOptionsResponse(BaseModel):
    options: list[StyleGuideOptionResponse]


class ShopIdentityResponse(BaseModel):
    identity: StyleGuideOptionResponse | None


logger = logging.getLogger("agentpexi.api")
router = APIRouter(dependencies=[Depends(state.verify_personal_key)])

_bundles_cache: dict = {"data": None, "cached_at": 0.0}
_BUNDLES_CACHE_TTL = 600


@router.post("/api/etsy/auth/status")
async def etsy_auth_status() -> dict:
    """Verifica se i token Etsy sono validi."""
    if not state.etsy_api:
        return JSONResponse(status_code=503, content={"error": "EtsyAPI non inizializzato"})
    return await state.etsy_api.check_auth_status()


@router.get("/api/etsy/shop")
async def etsy_shop_info() -> dict:
    """Info shop Etsy (test connessione)."""
    if not state.etsy_api:
        return JSONResponse(status_code=503, content={"error": "EtsyAPI non inizializzato"})
    try:
        shop = await state.etsy_api.get_shop()
        return {"shop": shop}
    except RuntimeError as exc:
        logger.warning("etsy shop auth error: %s", exc)
        return JSONResponse(status_code=401, content={"error": "Token Etsy non valido o scaduto"})
    except Exception as exc:
        logger.exception("etsy shop error")
        return JSONResponse(status_code=502, content={"error": "Errore comunicazione Etsy"})


@router.get("/api/etsy/listings")
async def get_etsy_listings(
    status: Annotated[Literal["all", "draft", "active", "inactive", "sold_out", "expired"], Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    """Lista listing Etsy con filtro status (draft|active|all)."""
    if not state.memory:
        return {"listings": []}
    filter_status = None if status == "all" else status
    listings = await state.memory.get_etsy_listings(status=filter_status, limit=limit)
    return {"listings": listings}


@router.get("/api/etsy/niches")
async def get_etsy_niches(
    min_score: float | None = None,
    confidence: str | None = None,
) -> NichesResponse:
    """
    Legge niche_intelligence JOIN market_signals (più recente per niche).

    Query params opzionali:
      min_score   float — filtra performance_score >= min_score
      confidence  str   — filtra confidence_level (low|medium|high)

    Risposta per niche:
      niche, product_type,
      performance_score, confidence_level,
      avg_ctr, total_orders, total_listings, total_revenue_eur,
      last_updated_at,
      entry_score, tier, avg_price_eur, google_trend_score  ← da market_signals
    """
    if not state.memory:
        return NichesResponse(niches=[])
    if confidence is not None and confidence not in {"low", "medium", "high"}:
        return JSONResponse(status_code=422, content={"error": "confidence must be low|medium|high"})
    try:
        db = await state.memory.get_db()
        conditions: list[str] = []
        params: list = []

        if min_score is not None:
            conditions.append("ni.performance_score >= ?")
            params.append(min_score)
        if confidence:
            conditions.append("ni.confidence_level = ?")
            params.append(confidence)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cursor = await db.execute(
            f"""
            SELECT
                ni.niche,
                ni.product_type,
                ni.performance_score,
                ni.confidence_level,
                ni.avg_ctr,
                ni.total_orders,
                ni.total_listings,
                ni.total_revenue_eur,
                ni.last_updated_at,
                ni.audience_target,
                ni.expansion_potential,
                ms.entry_score,
                ms.tier,
                ms.avg_price_eur,
                ms.google_trend_score,
                es.section_name
            FROM niche_intelligence ni
            LEFT JOIN (
                SELECT ms1.niche,
                       ms1.entry_score,
                       ms1.tier,
                       ms1.avg_price_eur,
                       ms1.google_trend_score
                FROM market_signals ms1
                INNER JOIN (
                    SELECT niche, MAX(collected_at) AS max_at
                    FROM market_signals
                    GROUP BY niche
                ) latest ON ms1.niche = latest.niche
                         AND ms1.collected_at = latest.max_at
            ) ms ON ms.niche = ni.niche
            LEFT JOIN niche_section_map nsm ON nsm.niche_key = ni.niche
            LEFT JOIN etsy_sections es ON es.section_id = nsm.section_id
            {where}
            ORDER BY COALESCE(ms.entry_score, ni.performance_score) DESC
            """,
            params,
        )
        rows = await cursor.fetchall()
        niches = [NicheItemResponse(**dict(r)) for r in rows]
        return NichesResponse(niches=niches)
    except Exception:
        logger.exception("get_etsy_niches error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/etsy/sections")
async def get_etsy_sections() -> SectionsResponse:
    """
    Ritorna tutte le sezioni Etsy attive con conteggio listing e pending uncategorized.

    pending_uncategorized: numero globale di niche non ancora mappate a una sezione.
    last_listing_at: timestamp ISO dell'ultimo listing pubblicato in questa sezione.
    """
    if not state.memory:
        return JSONResponse(status_code=503, content={"error": "MemoryManager non inizializzato"})
    try:
        from apps.backend.core.etsy_sections_service import EtsySectionsService
        db = await state.memory.get_db()
        ess = EtsySectionsService(db)
        raw = await ess.get_sections_with_uncategorized_counts()
        sections = [SectionItemResponse(**s) for s in raw]
        return SectionsResponse(sections=sections)
    except Exception:
        logger.exception("get_etsy_sections error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/etsy/bundles")
async def get_etsy_bundles() -> dict:
    """
    Ritorna niches bundle-ready via BundleStrategy.check_all_niches().
    Cache 10 minuti — non riesegue la scan ad ogni request.
    """
    now = _time.time()
    if _bundles_cache["data"] is not None and (now - _bundles_cache["cached_at"]) < _BUNDLES_CACHE_TTL:
        return {"bundles": _bundles_cache["data"], "cached_at": _bundles_cache["cached_at"]}

    if not state.bundle_strategy:
        return {"bundles": [], "cached_at": None}
    try:
        results = await state.bundle_strategy.check_all_niches()
        _bundles_cache["data"]      = results
        _bundles_cache["cached_at"] = now
        return {"bundles": results, "cached_at": now}
    except Exception:
        logger.exception("get_etsy_bundles error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/etsy/ads-status")
async def get_etsy_ads_status() -> dict:
    """
    Riassunto stato Etsy Ads.

    - activated_count: listing con ads_activated=1 in production_queue
    - paused_count:    listing con ladder_level='ctr_low' (proxy per ads in pausa)
    - avg_ctr:         CTR medio su listing con ads attive
    - last_auto_manage_at: ultimo run auto_manage (da config, se presente)
    """
    if not state.memory:
        return {"activated_count": 0, "paused_count": 0, "avg_ctr": None, "last_auto_manage_at": None}
    try:
        db = await state.memory.get_db()

        # Listing con ads attive
        cur = await db.execute(
            "SELECT COUNT(*) AS cnt FROM production_queue WHERE ads_activated = 1"
        )
        row = await cur.fetchone()
        activated_count = row["cnt"] if row else 0

        # Listing con ads esplicitamente messe in pausa da EtsyAdsManager
        cur = await db.execute(
            "SELECT COUNT(*) AS cnt FROM production_queue WHERE ads_paused = 1"
        )
        row = await cur.fetchone()
        paused_count = row["cnt"] if row else 0

        # CTR medio sulle listing ads attive
        cur = await db.execute(
            """
            SELECT AVG(lp.ctr) AS avg_ctr
            FROM listing_performance lp
            JOIN production_queue pq ON lp.production_queue_id = pq.id
            WHERE pq.ads_activated = 1 AND lp.ctr > 0
            """
        )
        row = await cur.fetchone()
        avg_ctr = round(float(row["avg_ctr"]), 4) if row and row["avg_ctr"] else None

        # Ultimo run auto_manage_ads — da config se tracciato
        cur = await db.execute(
            "SELECT value FROM config WHERE key = 'etsy_ads.last_auto_manage_at'"
        )
        row = await cur.fetchone()
        last_auto_manage_at = float(row["value"]) if row and row["value"] else None

        return {
            "activated_count":    activated_count,
            "paused_count":       paused_count,
            "avg_ctr":            avg_ctr,
            "last_auto_manage_at": last_auto_manage_at,
        }
    except Exception:
        logger.exception("get_etsy_ads_status error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/etsy/shop-optimizer")
async def get_etsy_shop_optimizer() -> dict:
    """
    Stato corrente ShopProfileOptimizer — ultimo titolo e niches applicati.
    Legge dalla tabella config (non chiama LLM né Etsy API).
    """
    if not state.memory:
        return {"last_title": None, "last_niches": [], "last_applied_at": None, "status": "unavailable"}
    try:
        db = await state.memory.get_db()

        cur = await db.execute(
            "SELECT key, value FROM config WHERE key IN (?, ?, ?)",
            (
                "shop_optimizer.last_applied_title",
                "shop_optimizer.last_applied_niches",
                "shop_optimizer.last_applied_at",
            ),
        )
        rows = await cur.fetchall()
        cfg = {r["key"]: r["value"] for r in rows}

        last_title = cfg.get("shop_optimizer.last_applied_title")
        last_niches_raw = cfg.get("shop_optimizer.last_applied_niches")
        last_applied_at = cfg.get("shop_optimizer.last_applied_at")

        try:
            last_niches = json.loads(last_niches_raw) if last_niches_raw else []
        except (json.JSONDecodeError, TypeError):
            last_niches = []

        status = "applied" if last_title else "never_applied"

        return {
            "last_title":      last_title,
            "last_niches":     last_niches,
            "last_applied_at": float(last_applied_at) if last_applied_at else None,
            "status":          status,
        }
    except Exception:
        logger.exception("get_etsy_shop_optimizer error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.post("/api/etsy/shop-optimizer/preview")
async def etsy_shop_optimizer_preview(body: dict | None = None) -> dict:
    """
    Genera anteprima titolo + about senza applicare su Etsy.
    Chiama ShopProfileOptimizer.preview() — usa LLM ma non Etsy API.

    Body opzionale: { "focus_niche": "wedding planner" }
    """
    if not state.shop_optimizer:
        return JSONResponse(status_code=503, content={"error": "ShopProfileOptimizer non inizializzato"})
    try:
        focus_niche = (body or {}).get("focus_niche")
        result = await state.shop_optimizer.preview(focus_niche=focus_niche)
        return {
            "title":   result.get("title"),
            "about":   result.get("about"),
            "niches":  result.get("niches", []),
            "changed": result.get("changed", False),
            "status":  "ok",
        }
    except Exception:
        logger.exception("etsy_shop_optimizer_preview error")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/api/etsy/style-guide-options")
async def get_style_guide_options() -> StyleGuideOptionsResponse:
    """Lista tutte le opzioni style guide salvate (attive e non)."""
    if not state.memory:
        return StyleGuideOptionsResponse(options=[])
    try:
        db = await state.memory.get_db()
        from apps.backend.core.shop_identity_service import ShopIdentityService
        svc = ShopIdentityService(db)
        records = await svc.list_options()
        options = [
            StyleGuideOptionResponse(
                id=r.id,
                aesthetic_name=r.aesthetic_name,
                palette_primary=r.palette_primary,
                palette_secondary=r.palette_secondary,
                palette_accent=r.palette_accent,
                mockup_style=r.mockup_style,
                tone=r.tone,
                logo_path=r.logo_path,
                banner_path=r.banner_path,
                is_active=r.is_active,
            )
            for r in records
        ]
        return StyleGuideOptionsResponse(options=options)
    except Exception:
        logger.exception("get_style_guide_options error")
        return StyleGuideOptionsResponse(options=[])


@router.get("/api/etsy/shop-identity")
async def get_shop_identity() -> ShopIdentityResponse:
    """Ritorna l'identity attiva, o null se nessuna è attiva."""
    if not state.memory:
        return ShopIdentityResponse(identity=None)
    try:
        db = await state.memory.get_db()
        from apps.backend.core.shop_identity_service import ShopIdentityService
        svc = ShopIdentityService(db)
        record = await svc.get_active()
        if record is None:
            return ShopIdentityResponse(identity=None)
        return ShopIdentityResponse(
            identity=StyleGuideOptionResponse(
                id=record.id,
                aesthetic_name=record.aesthetic_name,
                palette_primary=record.palette_primary,
                palette_secondary=record.palette_secondary,
                palette_accent=record.palette_accent,
                mockup_style=record.mockup_style,
                tone=record.tone,
                logo_path=record.logo_path,
                banner_path=record.banner_path,
                is_active=record.is_active,
            )
        )
    except Exception:
        logger.exception("get_shop_identity error")
        return ShopIdentityResponse(identity=None)

