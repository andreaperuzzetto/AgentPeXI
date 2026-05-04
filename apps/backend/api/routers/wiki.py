import logging
import re

import apps.backend.api.state as state
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

logger = logging.getLogger("agentpexi.api")
router = APIRouter()

_NICHE_SAFE_RE = re.compile(r'^[A-Za-z0-9 _\-]{1,80}$')


def _get_wiki_llms():
    """Ritorna (llm_etsy, llm_personal) da pepe, oppure (None, None) se non disponibile."""
    if not state.pepe:
        return None, None
    return getattr(state.pepe, "client", None), getattr(state.pepe, "_local_client", None)


@router.get("/api/wiki/stats")
async def get_wiki_stats() -> dict:
    """Statistiche wiki: file per dominio, raw pending, nicchie Etsy."""
    if not state.pepe or not getattr(state.pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    try:
        stats = await state.pepe.wiki.get_stats()
        return stats
    except Exception as exc:
        logger.exception("wiki stats error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@router.get("/api/wiki/query")
async def wiki_query(domain: str = "etsy", q: str = "") -> dict:
    """Query tiered sulla wiki (Pass 1 frontmatter, Pass 2 body se necessario).

    Params: domain=etsy|personal, q=testo della query.
    """
    if not state.pepe or not getattr(state.pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    if not q:
        return JSONResponse(status_code=400, content={"error": "Parametro 'q' obbligatorio"})
    llm_etsy, llm_personal = _get_wiki_llms()
    llm = llm_personal if domain == "personal" else llm_etsy
    if not llm:
        return JSONResponse(status_code=503, content={"error": "LLM non disponibile"})
    try:
        result = await state.pepe.wiki.query(domain, q, llm)
        return {"domain": domain, "query": q, "result": result}
    except Exception as exc:
        logger.exception("wiki query error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@router.get("/api/wiki/niche/{niche}")
async def get_wiki_niche(niche: str) -> dict:
    """Contesto wiki per una nicchia Etsy specifica (lettura diretta, no LLM)."""
    if not _NICHE_SAFE_RE.match(niche):
        return JSONResponse(status_code=400, content={"error": "Parametro 'niche' non valido"})
    if not state.pepe or not getattr(state.pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    try:
        content = await state.pepe.wiki.get_niche_context(niche)
        if content is None:
            return JSONResponse(status_code=404, content={"error": "Niche non trovata"})
        return {"niche": niche, "content": content}
    except Exception as exc:
        logger.exception("wiki niche error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@router.post("/api/wiki/lint", dependencies=[Depends(state.verify_personal_key)])
async def wiki_lint(body: dict | None = None) -> dict:
    """Lint wiki: wikilinks rotti + raw pending + suggerimenti.

    Body: {domain: 'etsy'|'personal'} (default: etsy).
    """
    if not state.pepe or not getattr(state.pepe, "wiki", None):
        return JSONResponse(status_code=503, content={"error": "WikiManager non inizializzato"})
    domain = (body or {}).get("domain", "etsy")
    llm_etsy, llm_personal = _get_wiki_llms()
    llm = llm_personal if domain == "personal" else llm_etsy
    if not llm:
        return JSONResponse(status_code=503, content={"error": "LLM non disponibile"})
    try:
        report = await state.pepe.wiki.lint(domain, llm)
        return {"domain": domain, "report": report}
    except Exception as exc:
        logger.exception("wiki lint error")
        return JSONResponse(status_code=500, content={"error": "Errore interno"})


@router.post("/api/domain", dependencies=[Depends(state.verify_personal_key)])
async def switch_domain(body: dict) -> dict:
    """Cambia dominio attivo. Body: {domain: 'etsy'|'personal'}."""
    if not state.pepe:
        return JSONResponse(status_code=503, content={"error": "Pepe non inizializzato"})
    from apps.backend.core.domains import DOMAIN_ETSY
    domain_name = (body or {}).get("domain", "")
    if domain_name == "personal":
        state.pepe.set_active_domain(None)
    elif domain_name == "etsy":
        state.pepe.set_active_domain(DOMAIN_ETSY)
    else:
        return JSONResponse(status_code=400, content={"error": f"Dominio sconosciuto: {domain_name}"})
    await state.ws_manager.broadcast({"type": "domain_switched", "domain": domain_name})
    return {"domain": domain_name}
