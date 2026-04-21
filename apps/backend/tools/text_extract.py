"""TextExtractor — estrazione testo da URL, file e allegati Telegram.

Supporta: URL (trafilatura), PDF (PyMuPDF), TXT, MD.
Fail-safe: ogni metodo ritorna (None, motivo) su errore — mai eccezioni non gestite.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("agentpexi.text_extract")

# Stringhe che indicano estrazione fallita (pagine JS-heavy, paywall, errori)
_FAIL_INDICATORS = (
    "enable javascript",
    "javascript is required",
    "access denied",
    "403 forbidden",
    "404 not found",
    "cookie",
    "please verify",
    "captcha",
    "subscribe to read",
    "sign in to read",
)

SUPPORTED_MIME_TYPES = {
    "application/pdf":  "pdf",
    "text/plain":       "txt",
    "text/markdown":    "md",
    "text/x-markdown":  "md",
}
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}

# Directory consentite per from_file (temp di sistema + storage progetto).
# STORAGE_PATH viene da settings/.env — non hardcoded.
def _build_allowed_roots() -> tuple[Path, ...]:
    from apps.backend.core.config import settings
    roots = [Path(tempfile.gettempdir()).resolve()]
    try:
        storage = Path(settings.STORAGE_PATH).resolve()
        roots.append(storage)
    except Exception:
        pass  # settings non disponibili al momento dell'import — verrà risolto al primo uso
    return tuple(roots)

_ALLOWED_ROOTS: tuple[Path, ...] = _build_allowed_roots()

# Reti private/riservate bloccate per SSRF
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_url(url: str) -> bool:
    """Rifiuta URL con schema non-http/https o hostname che risolve a IP privati."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    if not hostname:
        return False
    # Blocca pattern ovvi senza DNS
    if hostname in ("localhost", "0.0.0.0", "[::]", "[::]1"):
        return False
    try:
        resolved_ip = socket.gethostbyname(hostname)
        addr = ipaddress.ip_address(resolved_ip)
        return not any(addr in net for net in _BLOCKED_NETWORKS)
    except (socket.gaierror, ValueError):
        return False


def _is_safe_path(file_path: str) -> bool:
    """Rifiuta percorsi che escono dalle directory consentite."""
    try:
        resolved = Path(file_path).resolve()
    except Exception:
        return False
    return any(resolved.is_relative_to(root) for root in _ALLOWED_ROOTS)


class TextExtractor:
    """Estrazione testo da URL, file e allegati Telegram."""

    def __init__(self, max_chars: int = 50_000) -> None:
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    async def from_url(self, url: str) -> str | None:
        """Estrae testo da una URL via trafilatura.

        Restituisce None se:
        - URL con schema non-http/https o hostname privato/loopback (SSRF)
        - pagina inaccessibile / timeout
        - contenuto vuoto o < 100 caratteri dopo estrazione
        - quality check fallisce (JS-heavy, paywall, errore HTTP)
        """
        if not _is_safe_url(url):
            logger.warning("TextExtract URL '%s' rifiutata: SSRF o schema non consentito", url[:80])
            return None
        try:
            text = await asyncio.get_event_loop().run_in_executor(
                None,
                self._trafilatura_sync,
                url,
            )
        except Exception as exc:
            logger.warning("TextExtract URL '%s' fallito: %s", url[:80], exc)
            return None

        if not text:
            return None

        if len(text) < 100:
            logger.warning("TextExtract URL '%s': testo troppo corto (%d chars)", url[:80], len(text))
            return None

        # Source quality check sulle prime 300 chars
        preview = text[:300].lower()
        for indicator in _FAIL_INDICATORS:
            if indicator in preview:
                logger.warning(
                    "TextExtract URL '%s': quality check fallito ('%s')", url[:80], indicator
                )
                return None

        return self._truncate(text)

    @staticmethod
    def _trafilatura_sync(url: str) -> str | None:
        """Esecuzione sincrona di trafilatura (da run_in_executor)."""
        import trafilatura
        downloaded = trafilatura.fetch_url(url, config=trafilatura.settings.use_config())
        if not downloaded:
            return None
        return trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )

    # ------------------------------------------------------------------
    # File locale
    # ------------------------------------------------------------------

    async def from_file(self, file_path: str, mime_type: str | None = None) -> str | None:
        """Estrae testo da un file locale.

        Restituisce None se formato non supportato, PDF criptato, percorso
        esterno alle directory consentite, o errore di lettura.
        """
        if not _is_safe_path(file_path):
            logger.warning(
                "TextExtract: percorso '%s' rifiutato (fuori dalle directory consentite)",
                file_path,
            )
            return None

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            logger.warning(
                "TextExtract: estensione '%s' non supportata. Accettati: %s",
                ext,
                ", ".join(sorted(SUPPORTED_EXTENSIONS)),
            )
            return None

        try:
            if ext == ".pdf":
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._extract_pdf, file_path
                )
            else:
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._read_text_file, file_path
                )
        except Exception as exc:
            logger.warning("TextExtract file '%s' fallito: %s", file_path, exc)
            return None

    def _extract_pdf(self, file_path: str) -> str | None:
        """Estrae testo da PDF con PyMuPDF (fitz)."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("TextExtract: PyMuPDF non installato (pip install PyMuPDF)")
            return None

        try:
            doc = fitz.open(file_path)
        except Exception as exc:
            logger.warning("TextExtract PDF: impossibile aprire — %s", exc)
            return None

        if doc.needs_pass:
            logger.warning("TextExtract PDF: protetto da password")
            doc.close()
            return None  # segnale speciale gestito da from_file

        parts = []
        for page in doc:
            parts.append(page.get_text())
        doc.close()

        text = "\n".join(parts).strip()
        return self._truncate(text) if text else None

    @staticmethod
    def _read_text_file(file_path: str) -> str | None:
        """Legge file TXT/MD con fallback encoding."""
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(file_path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        return None

    # ------------------------------------------------------------------
    # Allegato Telegram
    # ------------------------------------------------------------------

    async def from_telegram_file(
        self,
        bot_token: str,
        file_id: str,
    ) -> tuple[str | None, str]:
        """Scarica un allegato Telegram e ne estrae il testo.

        Restituisce (testo | None, mime_type_rilevato).
        Timeout totale: 15 secondi. Cleanup file tmp automatico.
        """
        import aiohttp

        tmp_path: str | None = None
        try:
            # 1. Recupera il file_path da Telegram
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(
                    f"https://api.telegram.org/bot{bot_token}/getFile",
                    params={"file_id": file_id},
                ) as resp:
                    data = await resp.json()

            if not data.get("ok"):
                logger.warning("Telegram getFile fallito: %s", data)
                return None, ""

            tg_path: str = data["result"]["file_path"]
            ext = os.path.splitext(tg_path)[1].lower() or ".bin"

            # 2. Download
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(
                    f"https://api.telegram.org/file/bot{bot_token}/{tg_path}"
                ) as resp:
                    content = await resp.read()

            # 3. Salva in tmp
            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="agentpexi_")
            with os.fdopen(fd, "wb") as f:
                f.write(content)

            # 4. Estrai testo
            text = await self.from_file(tmp_path)
            return text, ext

        except Exception as exc:
            logger.warning("from_telegram_file fallito: %s", exc)
            return None, ""
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def chunk_text(
        self,
        text: str,
        max_chars: int = 3_000,
        overlap: int = 200,
    ) -> list[str]:
        """Divide il testo in chunk con overlap, senza spezzare le parole."""
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end >= len(text):
                chunks.append(text[start:])
                break
            # Cerca il primo spazio prima di end (non spezza parole)
            cut = text.rfind(" ", start, end)
            if cut == -1 or cut <= start:
                cut = end   # fallback: taglia esatto
            chunks.append(text[start:cut])
            start = max(start + 1, cut - overlap)

        return chunks

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_chars:
            return text
        logger.info("TextExtract: testo troncato a %d chars", self.max_chars)
        return text[: self.max_chars]
