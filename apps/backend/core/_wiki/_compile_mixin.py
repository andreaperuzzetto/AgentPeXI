from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.backend.core._wiki._helpers import (
    _parse_frontmatter,
    _slugify,
    _COMPILE_NICHE_SYSTEM,
    _COMPILE_WIKI_FILE_SYSTEM,
    NICHE_HARD_LIMIT,
    PERSONAL_HARD_LIMIT,
    DEFAULT_HARD_LIMIT,
)

logger = logging.getLogger("agentpexi.wiki")


class _CompileMixin:
    """Compilation: niche, wiki-file, index, orphan cleanup."""

    base_path: Path
    wiki_path: Path
    raw_path: Path
    _manifest_lock: asyncio.Lock

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
        async with self._manifest_lock:
            (domain_path / "_index.md").write_text(index_str, encoding="utf-8")
        logger.info("update_index: %s (%d entries)", domain, len(entries))

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

    async def cleanup_orphan_raw(self, domain: str, llm) -> dict:
        """Cleanup settimanale raw orfani: compiled_at=null da più di 30 giorni.

        Per ogni orfano nel dominio:
        1. Tenta compile_niche forzata (solo se domain='etsy' e il raw ha campo 'niche')
        2. Se la compilazione riesce → manifest aggiornato da compile_niche stesso
        3. Se fallisce o non compilabile → elimina file raw + rimuove da manifest + log

        File mancanti nel filesystem ma ancora in manifest → rimossi da manifest direttamente.

        Returns:
            {
                "compiled": int,    # orfani compilati forzatamente
                "deleted":  int,    # orfani eliminati
                "skipped":  int,    # orfani < 30 giorni (non ancora orfani)
                "errors":   list[str],
            }
        """
        manifest  = self._read_manifest()
        now       = datetime.now(timezone.utc)
        threshold = timedelta(days=30)

        stats: dict = {"compiled": 0, "deleted": 0, "skipped": 0, "errors": []}
        to_remove_from_manifest: list[str] = []

        for raw_rel, entry in list(manifest.items()):
            # Considera solo raw non compilati del dominio richiesto
            if entry.get("compiled_at") is not None:
                continue
            if domain not in raw_rel:
                continue

            raw_path = self.base_path / raw_rel

            # File mancante — pulisci solo manifest (file già sparito, entry stale)
            if not raw_path.exists():
                to_remove_from_manifest.append(raw_rel)
                logger.info(
                    "cleanup_orphan_raw [%s]: file mancante, rimosso da manifest: %s",
                    domain, raw_rel,
                )
                stats["deleted"] += 1
                continue

            # Determina età dal nome file (formato: YYYYMMDDTHHMMSS_slug.json)
            try:
                ts_str  = raw_path.name.split("_")[0]      # "20240101T120000"
                file_dt = datetime.strptime(ts_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                # Fallback su mtime se il nome non segue il formato atteso
                file_dt = datetime.fromtimestamp(raw_path.stat().st_mtime, tz=timezone.utc)

            age_days = (now - file_dt).days
            if age_days < 30:
                stats["skipped"] += 1
                continue

            # Leggi payload del raw
            try:
                data = json.loads(raw_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("cleanup_orphan_raw [%s]: errore lettura %s: %s", domain, raw_rel, exc)
                stats["errors"].append(f"read:{raw_rel}:{exc}")
                continue

            # Determina agente dal path (raw/etsy/research/…  →  agent="research")
            parts = Path(raw_rel).parts          # ("raw", "etsy", "research", "file.json")
            agent = parts[2] if len(parts) > 2 else "unknown"
            niche = data.get("niche", "")

            compiled = False

            # Tentativo di compilazione forzata (solo etsy + niche presente)
            if domain == "etsy" and niche:
                try:
                    await self.compile_niche(niche, agent, data, llm)
                    stats["compiled"] += 1
                    compiled = True
                    logger.info(
                        "cleanup_orphan_raw [%s]: compilato forzatamente '%s' "
                        "(agent=%s, age=%dd, raw=%s)",
                        domain, niche, agent, age_days, raw_rel,
                    )
                except Exception as exc:
                    logger.error(
                        "cleanup_orphan_raw [%s]: compile_niche fallito per '%s': %s",
                        domain, niche, exc,
                    )
                    stats["errors"].append(f"compile:{raw_rel}:{exc}")

            if not compiled:
                # Nessuna compilazione possibile → elimina file e pulisci manifest
                try:
                    raw_path.unlink(missing_ok=True)
                    to_remove_from_manifest.append(raw_rel)
                    stats["deleted"] += 1
                    logger.warning(
                        "cleanup_orphan_raw [%s]: eliminato orfano (niche=%r, agent=%s, age=%dd): %s",
                        domain, niche or "n/a", agent, age_days, raw_rel,
                    )
                except Exception as exc:
                    logger.error(
                        "cleanup_orphan_raw [%s]: errore eliminazione %s: %s",
                        domain, raw_rel, exc,
                    )
                    stats["errors"].append(f"delete:{raw_rel}:{exc}")

        # Rimuovi dal manifest le entry per file eliminati o mancanti
        if to_remove_from_manifest:
            async with self._manifest_lock:
                manifest = self._read_manifest()
                for key in to_remove_from_manifest:
                    manifest.pop(key, None)
                self._write_manifest(manifest)

        return stats
