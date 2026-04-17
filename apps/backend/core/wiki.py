"""WikiManager — knowledge base strutturata per AgentPeXI.

Architettura:
    knowledge_base/
    ├── .manifest.json              ← delta tracker: {raw_path: {compiled_at, wiki_files_updated}}
    ├── raw/{domain}/{agent}/       ← output grezzo agenti (immutabile)
    └── wiki/{domain}/              ← conoscenza compilata (markdown + frontmatter YAML)

LLM routing (determinato dal tipo di client passato):
    anthropic.AsyncAnthropic  → Sonnet  (Etsy wiki)
    openai.AsyncOpenAI        → Ollama  (Personal wiki — privacy totale)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import openai

from apps.backend.core.config import MODEL_SONNET, settings

logger = logging.getLogger("agentpexi.wiki")

# ── Limiti token per tipo file ────────────────────────────────────────────────

COMPACTION_LIMITS: dict[str, int] = {
    "seasonal":  3000,
    "pricing":   3000,
    "learnings": 1500,
}
NICHE_HARD_LIMIT    = 2000
PERSONAL_HARD_LIMIT = 1800
DEFAULT_HARD_LIMIT  = 2000

# ── Helpers ───────────────────────────────────────────────────────────────────

# Frontmatter YAML minimale — nessun PyYAML (non nei requirements).
# Supporta solo scalari e liste inline — sufficiente per i nostri campi.
_FM_RE       = re.compile(r"^---\n(.*?)\n---\s*\n?", re.DOTALL)
_FM_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Estrae campi scalari dal frontmatter YAML senza PyYAML."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    fields: dict[str, Any] = {}
    for key, val in _FM_FIELD_RE.findall(m.group(1)):
        fields[key] = val.strip().strip('"').strip("'")
    return fields


def _estimate_tokens(text: str) -> int:
    """Stima token senza tiktoken: len(parole) * 1.33."""
    return int(len(text.split()) * 1.33)


# ── System prompts ────────────────────────────────────────────────────────────

_COMPILE_NICHE_SYSTEM = """\
Sei WikiManager di AgentPeXI. Aggiorna il file wiki di una nicchia Etsy.
Restituisci SOLO il file markdown completo con frontmatter YAML, nessun commento, \
nessun wrapper ```markdown.

STRUTTURA OBBLIGATORIA:
---
summary: "<1-2 frasi, max 30 parole, descrivi nicchia e stato attuale>"
last_updated: "<ISO8601>"
confidence: <float 0-1>
agents: [<lista agenti che hanno contribuito, es: research, analytics>]
---

# <Nome Nicchia>

## Domanda e Competizione
## Pricing Osservato
## Tag Etsy Validati
## Performance Storica
| Data | Views | Vendite | Revenue | Note |
|---|---|---|---|---|
## Learnings
## Connessioni

REGOLE (non negoziabili):
- Aggiorna sezioni esistenti — non duplicare informazioni già presenti
- Performance: max 12 righe. Oltre: consolida le più vecchie in "[YYYY-MM — YYYY-MM] media views X, vendite Y"
- Learnings: max 7 bullet. Sostituisci i meno distinti, non aggiungerne di nuovi
- Connessioni: usa [[slug-nicchia]] syntax, max 5 link
- Tag Validati: max 13 tag
- File sotto {token_limit} token — compatta se necessario
- summary: aggiornalo sempre per riflettere lo stato corrente
"""

_COMPILE_WIKI_FILE_SYSTEM = """\
Sei WikiManager di AgentPeXI. Aggiorna un file wiki markdown con nuove informazioni.
Restituisci SOLO il file markdown completo, nessun commento.

REGOLE:
- Aggiorna sezioni esistenti — non aggiungere se il contenuto è già rappresentato
- Se il file ha frontmatter YAML, aggiorna last_updated e summary
- File sotto {token_limit} token — compatta se necessario
"""

_QUERY_PASS1_SYSTEM = """\
Sei WikiManager di AgentPeXI. Hai un indice wiki con i summary di ogni file.
Identifica quali file sono rilevanti per la query.

Rispondi con JSON puro (nessun wrapper), questo schema esatto:
{
  "relevant_files": ["dominio/sottocartella/file.md"],
  "sufficient_from_summaries": true,
  "quick_answer": "risposta breve se sufficient=true, altrimenti null"
}
"""

_QUERY_PASS2_SYSTEM = """\
Sei WikiManager di AgentPeXI. Sintetizza le informazioni dai file wiki rilevanti.
Produci testo strutturato da iniettare nel system prompt dell'orchestratore.
Max 600 token. Evidenzia dati pratici: prezzi, tag, performance, learnings.
NON inventare dati non presenti nei file.
"""

_LINT_SYSTEM = """\
Sei WikiManager di AgentPeXI. Analizza la wiki e identifica:
1. File con sezioni mancanti o vuote (<!-- --> o tabelle senza righe)
2. [[wikilink]] rotti (puntano a file non esistenti)
3. File non aggiornati da >30 giorni con dati potenzialmente stale
4. Raw non compilati nel manifest (backlog)
5. Suggerimenti per nuovi articoli basati su pattern nei dati esistenti

Rispondi in testo strutturato conciso — priorità ai problemi bloccanti.
"""

_DISTILL_SYSTEM = """\
Sei WikiManager di AgentPeXI. Il file wiki allegato ha superato il limite dimensione.
Distilla il contenuto mantenendo solo le informazioni più rilevanti e actionable.
Restituisci SOLO il file markdown distillato con frontmatter aggiornato, nessun commento.

REGOLE:
- Mantieni tutto il frontmatter, aggiorna last_updated
- Performance: consolida righe >12 mesi fa in riga summary
- Learnings: tieni i 5-7 più actionable, elimina i ridondanti
- Non perdere prezzi reali o tag validati da dati Analytics
- Target: circa {target_tokens} token
"""


# ── WikiManager ───────────────────────────────────────────────────────────────

class WikiManager:
    """Gestisce lettura/scrittura della knowledge base strutturata di AgentPeXI."""

    def __init__(self, base_path: Path) -> None:
        self.base_path      = base_path
        self.wiki_path      = base_path / "wiki"
        self.raw_path       = base_path / "raw"
        self._manifest_lock = asyncio.Lock()  # serializza R/W su .manifest.json — obbligatorio
                                              # se due agent completano in parallelo (es.
                                              # research + analytics dallo stesso /pipeline)

    # ── Init ──────────────────────────────────────────────────────────────────

    async def init(self) -> None:
        """Crea la struttura di directory e .manifest.json vuoto se non esistono."""
        dirs = [
            self.wiki_path / "etsy" / "niches",
            self.wiki_path / "etsy" / "patterns",
            self.wiki_path / "etsy" / "meta",
            self.wiki_path / "personal",
            self.raw_path / "etsy" / "research",
            self.raw_path / "etsy" / "analytics",
            self.raw_path / "etsy" / "publisher",
            self.raw_path / "personal" / "research",
            self.raw_path / "personal" / "summarize",
            self.raw_path / "personal" / "screen",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        manifest_path = self.base_path / ".manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text("{}", encoding="utf-8")

        logger.info("WikiManager inizializzato su %s", self.base_path)

    # ── Scrittura ─────────────────────────────────────────────────────────────

    async def store_raw(self, domain: str, agent: str, data: dict) -> Path:
        """Salva output grezzo in raw/{domain}/{agent}/{timestamp}.json.

        Aggiorna .manifest.json: {raw_path: {compiled_at: null, wiki_files_updated: []}}.
        compiled_at rimane null finché compile_niche/compile_wiki_file non processa il file.
        Tutte le scritture su .manifest.json passano per self._manifest_lock.
        """
        ts        = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        agent_dir = self.raw_path / domain / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        hint      = data.get("niche") or data.get("query") or ""
        slug_part = _slugify(str(hint))[:30] if hint else "raw"
        file_path = agent_dir / f"{ts}_{slug_part}.json"
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        async with self._manifest_lock:
            manifest = self._read_manifest()
            rel      = str(file_path.relative_to(self.base_path))
            manifest[rel] = {"compiled_at": None, "wiki_files_updated": []}
            self._write_manifest(manifest)

        logger.debug("store_raw: %s", file_path.name)
        return file_path

    async def compile_niche(
        self, niche: str, agent: str, output: dict, llm
    ) -> None:
        """Aggiorna wiki/etsy/niches/{slug}.md dopo Research, Analytics o Publisher.

        - Controlla .manifest.json: se raw_path già compilato con compiled_at recente → skip (no delta)
        - Se il file esiste: merge intelligente (non sovrascrive, aggiorna sezioni)
        - Se non esiste: crea da template con frontmatter + tutte le sezioni
        - Aggiorna sempre: last_updated nel frontmatter, confidence, performance data
        - Al termine: aggiorna .manifest.json con compiled_at=now e wiki_files_updated=[path]
          (dentro self._manifest_lock)
        """
        wiki_file = self.wiki_path / "etsy" / "niches" / f"{_slugify(niche)}.md"
        existing  = wiki_file.read_text(encoding="utf-8") if wiki_file.exists() else ""

        system = _COMPILE_NICHE_SYSTEM.format(token_limit=NICHE_HARD_LIMIT)
        user   = self._build_compile_niche_user(niche, agent, output, existing)

        try:
            updated = await self._llm_call(llm, system, user, max_tokens=3000)
        except Exception as exc:
            logger.error("compile_niche LLM error (%s/%s): %s", niche, agent, exc)
            return

        async with self._manifest_lock:
            wiki_file.write_text(updated, encoding="utf-8")
            manifest  = self._read_manifest()
            rel_wiki  = str(wiki_file.relative_to(self.base_path))
            niche_slug = _slugify(niche)
            now_iso   = datetime.now(timezone.utc).isoformat()
            for raw_rel, entry in manifest.items():
                if (
                    f"raw/etsy/{agent}" in raw_rel
                    and niche_slug in raw_rel
                    and entry["compiled_at"] is None
                ):
                    entry["compiled_at"]       = now_iso
                    entry["wiki_files_updated"] = [rel_wiki]
            self._write_manifest(manifest)

        logger.info("compile_niche: %s (%s) → %s", niche, agent, wiki_file.name)

    async def compile_wiki_file(
        self, domain: str, rel_path: str, content: str, llm
    ) -> None:
        """Aggiorna un file wiki arbitrario: wiki/{domain}/{rel_path}.md

        Metodo generico per file non-niche (patterns, meta, personal).
        Stessa logica merge di compile_niche: aggiorna sezioni esistenti,
        non sovrascrive, rispetta i limiti dimensione di Step 5.2.1b.
        Aggiorna .manifest.json al termine.
        """
        wiki_file = self.wiki_path / domain / f"{rel_path}.md"
        wiki_file.parent.mkdir(parents=True, exist_ok=True)
        existing  = wiki_file.read_text(encoding="utf-8") if wiki_file.exists() else ""

        limit  = PERSONAL_HARD_LIMIT if domain == "personal" else DEFAULT_HARD_LIMIT
        system = _COMPILE_WIKI_FILE_SYSTEM.format(token_limit=limit)
        user   = (
            f"FILE ESISTENTE:\n{existing}\n\n---\nNUOVE INFORMAZIONI ({domain}/{rel_path}):\n{content}"
            if existing
            else f"Crea un nuovo file wiki per {domain}/{rel_path}.\n\nINFORMAZIONI:\n{content}"
        )

        try:
            updated = await self._llm_call(llm, system, user, max_tokens=2500)
        except Exception as exc:
            logger.error("compile_wiki_file LLM error (%s/%s): %s", domain, rel_path, exc)
            return

        async with self._manifest_lock:
            wiki_file.write_text(updated, encoding="utf-8")
            manifest = self._read_manifest()
            rel_wiki = str(wiki_file.relative_to(self.base_path))
            now_iso  = datetime.now(timezone.utc).isoformat()
            for raw_rel, entry in manifest.items():
                if domain in raw_rel and entry["compiled_at"] is None:
                    entry["compiled_at"] = now_iso
                    if rel_wiki not in entry["wiki_files_updated"]:
                        entry["wiki_files_updated"].append(rel_wiki)
            self._write_manifest(manifest)

        logger.info("compile_wiki_file: %s/%s.md", domain, rel_path)

    async def update_index(self, domain: str, llm) -> None:  # noqa: ARG002
        """Rigenera wiki/{domain}/_index.md leggendo solo il frontmatter (campo summary:).

        Non apre i body completi — costo proporzionale al numero di file, non alla loro dimensione.
        `llm` non è usato qui (lettura pura), ma è accettato per coerenza di firma.
        """
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return

        entries: list[str] = []
        for md_file in sorted(domain_path.rglob("*.md")):
            if md_file.name.startswith("_"):
                continue
            try:
                fm      = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                summary = fm.get("summary", "").strip()
                rel     = md_file.relative_to(domain_path)
                line    = f"- [[{rel.stem}]]"
                if summary:
                    line += f" — {summary}"
                entries.append(line)
            except Exception:
                continue

        now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        index_str = (
            f"# Wiki Index — {domain}\n"
            f"> Aggiornato: {now} | {len(entries)} articoli\n\n"
            + "\n".join(entries)
        )
        (domain_path / "_index.md").write_text(index_str, encoding="utf-8")
        logger.info("update_index: %s (%d entries)", domain, len(entries))

    # ── Lettura ───────────────────────────────────────────────────────────────

    async def query(self, domain: str, query_text: str, llm) -> str:
        """Tiered retrieval in due pass.

        Pass 1 (cheap) — legge solo il frontmatter YAML di ogni file (campo summary:).
          LLM identifica i file rilevanti. Se la risposta è sufficiente dai summary → stop.
        Pass 2 (costoso, solo se necessario) — apre i body completi dei file rilevanti.
          LLM produce sintesi da iniettare nel system prompt di Pepe.
        """
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return ""

        # Pass 1 — solo frontmatter
        summaries: dict[str, str] = {}
        for md_file in sorted(domain_path.rglob("*.md")):
            if md_file.name.startswith("_"):
                continue
            try:
                fm  = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                rel = str(md_file.relative_to(self.wiki_path))
                summaries[rel] = fm.get("summary", "")
            except Exception:
                continue

        if not summaries:
            return ""

        index_snapshot = "\n".join(
            f"{path}: {summary}" for path, summary in summaries.items()
        )
        pass1_user = f"QUERY: {query_text}\n\nINDICE WIKI:\n{index_snapshot}"

        try:
            raw      = await self._llm_call(llm, _QUERY_PASS1_SYSTEM, pass1_user, max_tokens=500)
            # Estrai JSON anche se l'LLM wrappa in ```json
            json_str = re.search(r"\{.*\}", raw, re.DOTALL)
            pass1    = json.loads(json_str.group() if json_str else raw)
        except Exception as exc:
            logger.warning("query Pass 1 failed: %s", exc)
            return ""

        if pass1.get("sufficient_from_summaries") and pass1.get("quick_answer"):
            return str(pass1["quick_answer"])

        # Pass 2 — body completi dei file rilevanti (max 5)
        relevant = pass1.get("relevant_files", [])[:5]
        if not relevant:
            return ""

        bodies: list[str] = []
        for rel_path in relevant:
            full = self.wiki_path / rel_path
            if full.exists():
                bodies.append(f"### {rel_path}\n{full.read_text(encoding='utf-8')}")

        if not bodies:
            return ""

        pass2_user = f"QUERY: {query_text}\n\n{'---'.join(bodies)}"
        try:
            return await self._llm_call(llm, _QUERY_PASS2_SYSTEM, pass2_user, max_tokens=700)
        except Exception as exc:
            logger.warning("query Pass 2 failed: %s", exc)
            return ""

    async def get_niche_context(self, niche: str) -> str | None:
        """Ritorna contenuto wiki/etsy/niches/{slug}.md se esiste, None altrimenti."""
        p = self.wiki_path / "etsy" / "niches" / f"{_slugify(niche)}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None

    # ── Manutenzione ──────────────────────────────────────────────────────────

    async def lint(self, domain: str, llm) -> str:
        """Health check sulla wiki: sezioni vuote, wikilink rotti, raw pending, suggerimenti."""
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return f"Wiki {domain}: directory non trovata."

        all_slugs: set[str]     = set()
        file_summaries: list[str] = []

        for md_file in sorted(domain_path.rglob("*.md")):
            if md_file.name.startswith("_"):
                continue
            all_slugs.add(md_file.stem)
            try:
                fm = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
                file_summaries.append(
                    f"{md_file.relative_to(domain_path)}: "
                    f"updated={fm.get('last_updated', '?')}, "
                    f"confidence={fm.get('confidence', '?')}, "
                    f"summary={fm.get('summary', '(no summary)')[:60]}"
                )
            except Exception:
                file_summaries.append(f"{md_file.name}: (errore lettura)")

        # Wikilink rotti
        broken: list[str] = []
        for md_file in domain_path.rglob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            for link in _WIKILINK_RE.findall(text):
                slug = _slugify(link.split("|")[0])
                if slug not in all_slugs:
                    broken.append(f"  {md_file.name} → [[{link}]]")

        # Manifest — raw non compilati
        manifest = self._read_manifest()
        pending  = [k for k, v in manifest.items() if domain in k and v["compiled_at"] is None]

        snapshot = "\n".join(file_summaries)
        extra: list[str] = []
        if broken:
            extra.append("WIKILINK ROTTI:\n" + "\n".join(broken[:20]))
        if pending:
            extra.append(
                f"RAW NON COMPILATI ({len(pending)}):\n"
                + "\n".join(f"  {p}" for p in pending[:10])
            )

        user = f"WIKI SNAPSHOT ({domain}):\n{snapshot}"
        if extra:
            user += "\n\n" + "\n\n".join(extra)

        try:
            return await self._llm_call(llm, _LINT_SYSTEM, user, max_tokens=800)
        except Exception as exc:
            return f"Lint fallito: {exc}"

    async def get_stats(self) -> dict:
        """Statistiche rapide per il report Telegram del health check."""
        manifest     = self._read_manifest()
        pending      = sum(1 for v in manifest.values() if v["compiled_at"] is None)
        niches_dir   = self.wiki_path / "etsy" / "niches"
        patterns_dir = self.wiki_path / "etsy" / "patterns"
        return {
            "etsy_niches":   len(list(niches_dir.glob("*.md")))   if niches_dir.exists()   else 0,
            "etsy_patterns": len(list(patterns_dir.glob("*.md"))) if patterns_dir.exists() else 0,
            "pending_raw":   pending,
            "total_raw":     len(manifest),
        }

    async def compact_wiki(self, domain: str, llm) -> dict[str, list]:
        """Per ogni file wiki che supera il limite hard, chiama LLM per distillare.

        Chiamata pubblica — invocata da pepe.run_wiki_health_check (Step 5.2.4)
        prima del lint, in modo che il report lavori già sui file compattati.
        """
        stats: dict[str, list] = {"compacted": [], "skipped": []}
        for wiki_file in self._iter_wiki_files(domain):
            try:
                text        = wiki_file.read_text(encoding="utf-8")
                token_count = _estimate_tokens(text)
                threshold   = COMPACTION_LIMITS.get(wiki_file.stem, DEFAULT_HARD_LIMIT)
                if domain == "personal":
                    threshold = PERSONAL_HARD_LIMIT

                if token_count > threshold:
                    target = int(threshold * 0.70)
                    await self._distill_file(wiki_file, llm, target=target)
                    stats["compacted"].append(wiki_file.name)
                    logger.info(
                        "_compact_wiki: %s (%d tok → target %d)", wiki_file.name, token_count, target
                    )
                else:
                    stats["skipped"].append(wiki_file.name)
            except Exception as exc:
                logger.error("_compact_wiki: errore su %s: %s", wiki_file.name, exc)
        return stats

    async def _distill_file(self, wiki_file: Path, llm, target: int = 1200) -> None:
        """Distilla un file wiki verboso. Scrittura atomica con .bak per safety.

        Pattern: copia .bak → scrivi nuovo → unlink .bak solo a successo → ripristino su eccezione.
        """
        original = wiki_file.read_text(encoding="utf-8")
        bak      = wiki_file.with_suffix(wiki_file.suffix + ".bak")
        bak.write_text(original, encoding="utf-8")  # backup prima di qualsiasi modifica
        try:
            system    = _DISTILL_SYSTEM.format(target_tokens=target)
            distilled = await self._llm_call(llm, system, original, max_tokens=target + 300)
            wiki_file.write_text(distilled, encoding="utf-8")
            bak.unlink(missing_ok=True)             # pulizia .bak solo a successo
        except Exception:
            if bak.exists():
                wiki_file.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
                bak.unlink(missing_ok=True)
            raise

    # ── Helpers interni ───────────────────────────────────────────────────────

    async def _llm_call(
        self, llm, system: str, user: str, max_tokens: int = 2000
    ) -> str:
        """Chiamata LLM con routing automatico: Anthropic (Sonnet) o OpenAI-compat (Ollama)."""
        if isinstance(llm, anthropic.AsyncAnthropic):
            msg = await llm.messages.create(
                model=MODEL_SONNET,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text
        else:  # openai.AsyncOpenAI — Ollama
            resp = await llm.chat.completions.create(
                model=settings.OLLAMA_MODEL,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            return resp.choices[0].message.content or ""

    def _build_compile_niche_user(
        self, niche: str, agent: str, output: dict, existing: str
    ) -> str:
        """Costruisce il prompt user per compile_niche in base all'agente."""
        section_hints = {
            "research":  "Domanda e Competizione, Pricing Osservato, Tag Etsy Validati",
            "analytics": "Performance Storica (aggiungi riga), Pricing (aggiorna se cambiato)",
            "publisher": "Performance Storica (aggiungi riga con data e revenue pubblicazione)",
        }
        hint = section_hints.get(agent, "sezioni rilevanti in base ai dati")

        parts = [
            f"NICCHIA: {niche}",
            f"AGENTE: {agent} — aggiorna principalmente: {hint}",
            f"\nNUOVI DATI:\n{json.dumps(output, ensure_ascii=False, indent=2)}",
        ]
        if existing:
            parts.append(f"\nFILE ESISTENTE:\n{existing}")
        else:
            parts.append("\nIL FILE NON ESISTE ANCORA — crealo da zero seguendo il template.")
        return "\n".join(parts)

    def _iter_wiki_files(self, domain: str):
        """Itera i file .md della wiki escludendo _index.md e file con prefisso _."""
        domain_path = self.wiki_path / domain
        if not domain_path.exists():
            return
        yield from (f for f in sorted(domain_path.rglob("*.md")) if not f.name.startswith("_"))

    def _read_manifest(self) -> dict:
        p = self.base_path / ".manifest.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_manifest(self, data: dict) -> None:
        (self.base_path / ".manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
