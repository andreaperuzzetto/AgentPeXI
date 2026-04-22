#!/usr/bin/env python3
"""
reset_etsy_db.py — Pulisce tutti i dati Etsy-domain prima di un test.

Cosa viene eliminato:
  SQLite  → etsy_listings, listing_analyses, production_queue
            agent_logs, agent_steps, llm_calls, tool_calls,
            pending_actions, error_log
  ChromaDB → intera collection pepe_memory (etsy knowledge)
  Wiki     → raw/etsy/, wiki/etsy/, .manifest.json (solo entry etsy)
  Storage  → pending/ (PDF/PNG/SVG generati)

Cosa viene PRESERVATO (personal domain):
  SQLite  → conversations, oauth_tokens, reminders, personal_learning,
             learning_evaluations, scheduled_tasks
  Wiki    → raw/personal/, wiki/personal/
  ChromaDB → screen_memory collection

Uso:
    python scripts/reset_etsy_db.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

# Legge STORAGE_PATH da .env se presente, altrimenti usa default
def _get_storage_path() -> Path:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("STORAGE_PATH="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                return Path(raw).expanduser()
    return Path("~/.agentpexi-storage").expanduser()


STORAGE_PATH = _get_storage_path()

DB_PATH       = STORAGE_PATH / "agentpexi.db"
CHROMADB_PATH = STORAGE_PATH / "chromadb"
WIKI_BASE     = STORAGE_PATH / "knowledge_base"   # default; può essere overridato in .env
PENDING_PATH  = STORAGE_PATH / "pending"

# Controlla se WIKI_BASE_PATH è overridata nel .env
def _get_wiki_path() -> Path:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("WIKI_BASE_PATH="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                return Path(raw).expanduser()
    return WIKI_BASE

WIKI_PATH = _get_wiki_path()

# ── Tabelle SQLite da svuotare (dominio Etsy + operazionali) ──────────────────

TABLES_TO_CLEAR = [
    # Dati Etsy
    "etsy_listings",
    "listing_analyses",
    "production_queue",
    # Log operazionali (contengono dati mock mescolati)
    "agent_logs",
    "agent_steps",
    "llm_calls",
    "tool_calls",
    "error_log",
    # Pending actions Etsy (budget_alert, production_queue_proposal, ecc.)
    # NB: pending_actions ha UNIQUE su action_type — svuota tutto
    "pending_actions",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def _ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def _skip(msg: str) -> None:
    print(f"  ⏭   {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠️   {msg}")


# ── Step 1 — SQLite ───────────────────────────────────────────────────────────

def reset_sqlite(dry_run: bool) -> None:
    _section("SQLite")

    if not DB_PATH.exists():
        _skip(f"DB non trovato: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()

        # Controlla quali tabelle esistono effettivamente
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing = {row[0] for row in cur.fetchall()}

        for table in TABLES_TO_CLEAR:
            if table not in existing:
                _skip(f"{table} — tabella non presente")
                continue

            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]

            if dry_run:
                _skip(f"[dry-run] {table} — {count} righe da eliminare")
            else:
                cur.execute(f"DELETE FROM {table}")
                _ok(f"{table} — {count} righe eliminate")

        if not dry_run:
            conn.commit()
            # Recupera spazio disco
            cur.execute("VACUUM")
            _ok("VACUUM eseguito")

    finally:
        conn.close()


# ── Step 2 — ChromaDB ─────────────────────────────────────────────────────────

def reset_chromadb(dry_run: bool) -> None:
    _section("ChromaDB — collection pepe_memory")

    if not CHROMADB_PATH.exists():
        _skip(f"ChromaDB path non trovato: {CHROMADB_PATH}")
        return

    if dry_run:
        _skip(f"[dry-run] ChromaDB path trovato: {CHROMADB_PATH}")
        return

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMADB_PATH))

        collections = [c.name for c in client.list_collections()]

        if "pepe_memory" in collections:
            col = client.get_collection("pepe_memory")
            count = col.count()
            client.delete_collection("pepe_memory")
            _ok(f"pepe_memory eliminata ({count} documenti)")
        else:
            _skip("pepe_memory non trovata — già pulita")

        if "screen_memory" in collections:
            _skip("screen_memory preservata (personal domain)")
        else:
            _skip("screen_memory non presente")

    except ImportError:
        _warn("chromadb non installato — skip")
    except Exception as exc:
        _warn(f"Errore ChromaDB: {exc}")


# ── Step 3 — Wiki ─────────────────────────────────────────────────────────────

def reset_wiki(dry_run: bool) -> None:
    _section("Wiki knowledge base")

    if not WIKI_PATH.exists():
        _skip(f"Wiki path non trovato: {WIKI_PATH}")
        return

    # raw/etsy/
    raw_etsy = WIKI_PATH / "raw" / "etsy"
    if raw_etsy.exists():
        files = list(raw_etsy.rglob("*"))
        file_count = sum(1 for f in files if f.is_file())
        if dry_run:
            _skip(f"[dry-run] raw/etsy/ — {file_count} file da eliminare")
        else:
            shutil.rmtree(raw_etsy)
            raw_etsy.mkdir(parents=True, exist_ok=True)
            _ok(f"raw/etsy/ svuotata ({file_count} file)")
    else:
        _skip("raw/etsy/ non trovata")

    # wiki/etsy/
    wiki_etsy = WIKI_PATH / "wiki" / "etsy"
    if wiki_etsy.exists():
        files = list(wiki_etsy.rglob("*.md"))
        if dry_run:
            _skip(f"[dry-run] wiki/etsy/ — {len(files)} file .md da eliminare")
        else:
            shutil.rmtree(wiki_etsy)
            wiki_etsy.mkdir(parents=True, exist_ok=True)
            _ok(f"wiki/etsy/ svuotata ({len(files)} file .md)")
    else:
        _skip("wiki/etsy/ non trovata")

    # .manifest.json — rimuove solo le entry etsy
    manifest_path = WIKI_PATH / ".manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            etsy_keys = [k for k in manifest if "etsy" in k]
            personal_keys = [k for k in manifest if "etsy" not in k]

            if dry_run:
                _skip(
                    f"[dry-run] .manifest.json — {len(etsy_keys)} entry etsy da rimuovere, "
                    f"{len(personal_keys)} entry personal preservate"
                )
            else:
                cleaned = {k: v for k, v in manifest.items() if "etsy" not in k}
                manifest_path.write_text(
                    json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _ok(
                    f".manifest.json — {len(etsy_keys)} entry etsy rimosse, "
                    f"{len(personal_keys)} entry personal preservate"
                )
        except Exception as exc:
            _warn(f"Errore manifest: {exc}")
    else:
        _skip(".manifest.json non trovato")

    _skip("raw/personal/ e wiki/personal/ preservate")


# ── Step 4 — Storage pending (PDF/PNG/SVG generati) ──────────────────────────

def reset_pending(dry_run: bool) -> None:
    _section("Storage pending (output Design agent)")

    if not PENDING_PATH.exists():
        _skip(f"pending/ non trovata: {PENDING_PATH}")
        return

    # Conta solo le task dir (UUID), non file sciolti
    task_dirs = [d for d in PENDING_PATH.iterdir() if d.is_dir()]
    total_files = sum(len(list(d.rglob("*"))) for d in task_dirs)

    if dry_run:
        _skip(
            f"[dry-run] pending/ — {len(task_dirs)} task dir, "
            f"{total_files} file totali da eliminare"
        )
        return

    for d in task_dirs:
        shutil.rmtree(d)
    _ok(f"pending/ svuotata ({len(task_dirs)} task dir, {total_files} file)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Etsy domain DB per test pulito")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra cosa verrebbe eliminato senza farlo davvero",
    )
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "RESET REALE"
    print(f"\n{'═' * 50}")
    print(f"  AgentPeXI — Etsy DB Reset [{mode}]")
    print(f"  STORAGE_PATH: {STORAGE_PATH}")
    print(f"  WIKI_PATH:    {WIKI_PATH}")
    print(f"{'═' * 50}")

    if not args.dry_run:
        confirm = input(
            "\n  ⚠️  Stai per eliminare TUTTI i dati Etsy-domain.\n"
            "  I dati personal (reminders, oauth, learning) vengono preservati.\n"
            "  Continuare? [s/N] "
        ).strip().lower()
        if confirm != "s":
            print("  Annullato.")
            sys.exit(0)

    reset_sqlite(args.dry_run)
    reset_chromadb(args.dry_run)
    reset_wiki(args.dry_run)
    reset_pending(args.dry_run)

    print(f"\n{'═' * 50}")
    if args.dry_run:
        print("  DRY-RUN completato — nessuna modifica effettuata.")
        print("  Riesegui senza --dry-run per applicare il reset.")
    else:
        print("  ✅ Reset completato. DB pronto per il test.")
    print(f"{'═' * 50}\n")


if __name__ == "__main__":
    main()
