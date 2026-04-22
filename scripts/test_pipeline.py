#!/usr/bin/env python3
"""
test_pipeline.py — Test end-to-end Research → Design per tutti i product type.

Bypassa pepe.py: istanzia ResearchAgent e DesignAgent direttamente,
esegue Research poi passa l'output a Design, esattamente come fa la pipeline reale.

Uso:
    # Da root del progetto AgentPeXI:
    python scripts/test_pipeline.py
    python scripts/test_pipeline.py --type printable_pdf
    python scripts/test_pipeline.py --type digital_art_png --niche "botanical art prints"
    python scripts/test_pipeline.py --dry-run   # solo Research, salta Design

Prerequisiti:
    - .env configurato (ANTHROPIC_API_KEY, SECRET_KEY, STORAGE_PATH)
    - DB pulito (python scripts/reset_etsy_db.py)
    - Dipendenze installate (pip install -r requirements.txt)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
# Aggiunge la root del progetto al sys.path per permettere import assoluti
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Import progetto ───────────────────────────────────────────────────────────
try:
    import anthropic
    from apps.backend.core.config import settings
    from apps.backend.core.memory import MemoryManager
    from apps.backend.core.models import AgentTask, TaskStatus
    from apps.backend.core.storage import StorageManager
    from apps.backend.agents.research import ResearchAgent
    from apps.backend.agents.design import DesignAgent
except ImportError as e:
    print(f"\n❌ Errore import: {e}")
    print("   Assicurati di eseguire lo script dalla root del progetto AgentPeXI")
    print("   e che le dipendenze siano installate.\n")
    sys.exit(1)

# ── Configurazione test ───────────────────────────────────────────────────────

# Niche predefinita per ogni product type — scelte per massimizzare
# la probabilità che Research le valuti viable con dati reali
DEFAULT_NICHES: dict[str, str] = {
    "printable_pdf": "productivity weekly planner",
    "digital_art_png": "botanical art prints",
    "svg_bundle": "wedding monogram svg",
}

VALID_PRODUCT_TYPES = ("printable_pdf", "digital_art_png", "svg_bundle")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'═' * 55}")
    print(f"  {title}")
    print(f"{'═' * 55}")


def _ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️   {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠️   {msg}")


def _err(msg: str) -> None:
    print(f"  ❌  {msg}")


def _fmt_cost(usd: float) -> str:
    return f"${usd:.4f} ({usd * settings.USD_EUR_RATE:.4f}€)"


# ── Core ──────────────────────────────────────────────────────────────────────

async def run_research(
    agent: ResearchAgent,
    niche: str,
    product_type: str,
) -> tuple[dict | None, float]:
    """
    Esegue ResearchAgent per una singola nicchia.
    Ritorna (output_data, cost_usd) oppure (None, 0.0) in caso di fallimento.
    """
    task = AgentTask(
        agent_name="research",
        input_data={
            "niches": [niche],
            "product_type": product_type,
        },
        source="test_runner",
    )

    _info(f"Research avviato — niche='{niche}' product_type='{product_type}'")
    t0 = time.monotonic()

    try:
        result = await agent.execute(task)
    except Exception as exc:
        _err(f"Research exception: {exc}")
        return None, 0.0

    elapsed = time.monotonic() - t0

    if result.status == TaskStatus.COMPLETED:
        _ok(f"Research completato in {elapsed:.1f}s — costo: {_fmt_cost(result.cost_usd)}")
        return result.output_data, result.cost_usd
    else:
        _warn(f"Research status: {result.status.value} — {result.output_data.get('error', 'nessun dettaglio')}")
        # Ritorna comunque i dati parziali se presenti
        return result.output_data if result.output_data else None, result.cost_usd


async def run_design(
    agent: DesignAgent,
    niche: str,
    product_type: str,
    research_output: dict,
    upstream_cost: float,
) -> tuple[dict | None, float]:
    """
    Esegue DesignAgent con l'output di Research come contesto.
    Ritorna (output_data, cost_usd) oppure (None, 0.0) in caso di fallimento.
    """
    # Estrai la prima niche viable dal risultato Research
    niches = research_output.get("niches", [])
    first_viable = next((n for n in niches if n.get("viable")), niches[0] if niches else {})

    design_input = {
        "niche": first_viable.get("name") or niche,
        "product_type": first_viable.get("recommended_product_type") or product_type,
        "research_context": research_output,
        "keywords": first_viable.get("keywords", []),
        "color_schemes": [],
        "_run_cost_usd": upstream_cost,
    }

    task = AgentTask(
        agent_name="design",
        input_data=design_input,
        source="test_runner",
    )

    actual_type = design_input["product_type"]
    _info(f"Design avviato — niche='{design_input['niche']}' product_type='{actual_type}'")
    t0 = time.monotonic()

    try:
        result = await agent.execute(task)
    except Exception as exc:
        _err(f"Design exception: {exc}")
        return None, 0.0

    elapsed = time.monotonic() - t0

    if result.status == TaskStatus.COMPLETED:
        variants = result.output_data.get("variants", [])
        n_variants = len(variants)
        _ok(f"Design completato in {elapsed:.1f}s — {n_variants} varianti — costo: {_fmt_cost(result.cost_usd)}")

        # Mostra path dei file generati
        for v in variants[:5]:  # max 5 in output
            path = v.get("file_path") or v.get("path", "")
            template = v.get("template", "?")
            color = v.get("color_scheme", "?")
            print(f"       📄  {template} [{color}]: {path}")
        if n_variants > 5:
            print(f"       ... e altri {n_variants - 5} file")

        return result.output_data, result.cost_usd
    else:
        _warn(f"Design status: {result.status.value} — {result.output_data.get('error', 'nessun dettaglio')}")
        return result.output_data if result.output_data else None, result.cost_usd


async def run_test(
    product_types: list[str],
    niche_override: str | None,
    dry_run: bool,
    save_json: bool,
) -> None:
    """Esegue il test per tutti i product type richiesti."""

    _section("AgentPeXI — Pipeline Test Runner")
    _info(f"Product types da testare: {', '.join(product_types)}")
    _info(f"Modalità: {'DRY-RUN (solo Research)' if dry_run else 'COMPLETA (Research + Design)'}")
    _info(f"Storage path: {settings.STORAGE_PATH}")

    if not settings.ANTHROPIC_API_KEY:
        _err("ANTHROPIC_API_KEY non configurata nel .env — impossibile procedere.")
        sys.exit(1)

    # ── Init infrastruttura ───────────────────────────────────────────────────
    _section("Inizializzazione")

    memory = MemoryManager()
    await memory.init()
    _ok("MemoryManager inizializzato")

    storage = StorageManager()
    storage.ensure_dirs()
    _ok("StorageManager inizializzato")

    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    _ok("Anthropic client pronto")

    research_agent = ResearchAgent(
        anthropic_client=anthropic_client,
        memory=memory,
        ws_broadcaster=None,
        telegram_broadcaster=None,
    )
    _ok("ResearchAgent istanziato")

    design_agent = DesignAgent(
        anthropic_client=anthropic_client,
        memory=memory,
        storage=storage,
        ws_broadcaster=None,
        get_mock_mode=lambda: False,  # Design non ha mock mode; genera file reali
    )
    _ok("DesignAgent istanziato")

    # ── Esecuzione per ogni product type ─────────────────────────────────────
    results: list[dict] = []
    total_cost = 0.0

    for pt in product_types:
        niche = niche_override or DEFAULT_NICHES[pt]

        _section(f"TEST: {pt.upper()}  —  niche: '{niche}'")

        # Research
        research_data, research_cost = await run_research(research_agent, niche, pt)
        total_cost += research_cost

        if not research_data:
            _err("Research fallito — salto Design per questo product type")
            results.append({"product_type": pt, "niche": niche, "status": "research_failed"})
            continue

        # Verifica viable
        niches_data = research_data.get("niches", [])
        viable_niches = [n for n in niches_data if n.get("viable")]
        if not viable_niches:
            _warn("Nessuna nicchia viable trovata da Research — Design non avviato")
            _info(f"  Summary Research: {research_data.get('summary', 'n/d')}")
            results.append({
                "product_type": pt,
                "niche": niche,
                "status": "not_viable",
                "research_summary": research_data.get("summary"),
            })
        else:
            first = viable_niches[0]
            _info(f"Nicchia viable: '{first.get('name', niche)}'")
            _info(f"  Demand: {first.get('demand', {}).get('level', 'n/d')} | Competition: {first.get('competition', {}).get('level', 'n/d')}")
            _info(f"  Pricing sweet spot: ${first.get('pricing', {}).get('conversion_sweet_spot_usd', 'n/d')}")
            _info(f"  Tags: {', '.join(first.get('etsy_tags_13', [])[:5])}...")

        if dry_run:
            results.append({
                "product_type": pt,
                "niche": niche,
                "status": "dry_run_ok",
                "viable": bool(viable_niches),
                "research_cost_usd": research_cost,
            })
            continue

        if not viable_niches:
            continue

        # Design
        design_data, design_cost = await run_design(
            design_agent, niche, pt, research_data, research_cost
        )
        total_cost += design_cost

        run_result = {
            "product_type": pt,
            "niche": niche,
            "status": "completed" if design_data else "design_failed",
            "research_cost_usd": research_cost,
            "design_cost_usd": design_cost,
            "total_cost_usd": research_cost + design_cost,
        }

        if design_data:
            run_result["variants_generated"] = len(design_data.get("variants", []))
            run_result["pending_task_id"] = design_data.get("pending_task_id", "")

        results.append(run_result)

    # ── Riepilogo ─────────────────────────────────────────────────────────────
    _section("RIEPILOGO")

    for r in results:
        pt = r["product_type"]
        status = r["status"]
        cost = r.get("total_cost_usd") or r.get("research_cost_usd", 0.0)
        variants = r.get("variants_generated", "-")

        if status == "completed":
            print(f"  ✅  {pt:<20} → {variants} varianti  —  costo: {_fmt_cost(cost)}")
        elif status == "dry_run_ok":
            viable = "viable" if r.get("viable") else "NOT viable"
            print(f"  🔍  {pt:<20} → Research OK ({viable})  —  {_fmt_cost(cost)}")
        elif status == "not_viable":
            print(f"  ⚠️   {pt:<20} → Niche non viable")
        else:
            print(f"  ❌  {pt:<20} → {status}")

    print(f"\n  💰  Costo totale run: {_fmt_cost(total_cost)}")

    if save_json:
        out_path = PROJECT_ROOT / "scripts" / "last_test_results.json"
        out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        _info(f"Risultati salvati in: {out_path}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test end-to-end Research → Design per AgentPeXI"
    )
    parser.add_argument(
        "--type",
        choices=list(VALID_PRODUCT_TYPES) + ["all"],
        default="all",
        help="Product type da testare (default: all)",
    )
    parser.add_argument(
        "--niche",
        default=None,
        help="Override niche (usata per tutti i product type selezionati)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Esegue solo Research, salta Design (verifica viability e costi)",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Salva risultati in scripts/last_test_results.json",
    )
    args = parser.parse_args()

    if args.type == "all":
        types_to_test = list(VALID_PRODUCT_TYPES)
    else:
        types_to_test = [args.type]

    asyncio.run(
        run_test(
            product_types=types_to_test,
            niche_override=args.niche,
            dry_run=args.dry_run,
            save_json=args.save_json,
        )
    )


if __name__ == "__main__":
    main()
