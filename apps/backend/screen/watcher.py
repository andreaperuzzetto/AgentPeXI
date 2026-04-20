"""ScreenWatcher — cattura passiva dello schermo per il dominio Personal.

Processo persistente avviato nel lifespan FastAPI.
- Rileva cambio app attiva (NSWorkspace) + polling fallback ogni 30s
- Diff pixel per catture intermedie su cambiamenti significativi (>15%)
- OCR via macOS Vision (pyobjc)
- Redaction pattern sensibili
- Chunking + store in ChromaDB screen_memory

Nessun dato esce dal Mac: tutto elaborato localmente con Ollama.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Coroutine

from apps.backend.core.config import settings

if TYPE_CHECKING:
    from apps.backend.core.memory import MemoryManager

logger = logging.getLogger("agentpexi.screen")

# ---------------------------------------------------------------------------
# Blocklist app predefinite
# ---------------------------------------------------------------------------

_DEFAULT_BLOCKLIST: set[str] = {
    # Password manager
    "com.agilebits.onepassword7",
    "com.agilebits.onepassword-osx-helper",
    "com.apple.keychainaccess",
    # Mail
    "com.apple.mail",
    "com.readdle.smartemail",         # Spark
    "it.bloop.airmail2",             # Airmail
    # Messaggistica
    "org.whispersystems.signal-desktop",
    "org.whatsapp.WhatsApp",
    "org.telegram.desktop",
    "ru.keepcoder.Telegram",
    # Banche (pattern — gestito nella logica di check)
    # i bundle id esatti variano, usiamo anche il nome app
}

_DEFAULT_BLOCKLIST_NAMES: set[str] = {
    "1Password", "Keychain Access", "Mail", "Spark", "Airmail",
    "Signal", "WhatsApp", "Telegram", "Messages",
}

# Pattern per redaction di dati sensibili
REDACTION_PATTERNS: list[re.Pattern] = [
    re.compile(r'(?i)(password|passwd|pwd)\s*[:=]\s*\S+'),
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'(?<![/\w])[A-Za-z0-9]{40,}(?![/\w])'),  # token/chiavi molto lunghe (non URL né path)
    re.compile(r'\b[A-Z0-9]{20,}\b'),           # API keys uppercase
    re.compile(r'sk-[A-Za-z0-9]{20,}'),         # OpenAI-style keys
    re.compile(r'ghp_[A-Za-z0-9]{36}'),         # GitHub PAT
    re.compile(r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b'),  # carte di credito
    re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b'),  # IBAN
]

# Dimensione massima chunk in caratteri (≈ 500 token @ 4 char/token)
_CHUNK_SIZE = 2000
_CHUNK_OVERLAP = 200

# Soglia diff pixel per cattura intermedia
_PIXEL_DIFF_THRESHOLD = 0.15


class ScreenWatcher:
    """Monitora lo schermo passivamente e indicizza il testo in ChromaDB screen_memory."""

    def __init__(
        self,
        memory: "MemoryManager",
        ws_broadcaster: Callable[[dict], Coroutine] | None = None,
    ) -> None:
        self.memory = memory
        self._ws_broadcast = ws_broadcaster
        self._paused: bool = False
        self._running: bool = False
        self._task: asyncio.Task | None = None

        # Statistiche sessione
        self._captures_today: int = 0
        self._last_capture_time: str = ""
        self._last_capture_app: str = ""

        # Error tracking — notifica Telegram dopo N errori consecutivi
        self._consecutive_errors: int = 0
        self._error_notify_threshold: int = 5  # notifica dopo 5 errori di fila
        self._error_notifier: Callable[[str], None] | None = None  # impostato da main

        # Stato precedente per rilevare cambiamenti
        self._last_app_bundle: str = ""
        self._last_frame_hash: str = ""

        # Blocklist: default + custom da .env
        self._blocklist_bundles: set[str] = set(_DEFAULT_BLOCKLIST)
        self._blocklist_names: set[str] = set(_DEFAULT_BLOCKLIST_NAMES)
        if settings.SCREEN_BLOCKLIST:
            for entry in settings.SCREEN_BLOCKLIST.split(","):
                e = entry.strip()
                if e:
                    self._blocklist_bundles.add(e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Avvia il watcher nel loop asyncio corrente."""
        self._running = True
        self._task = asyncio.create_task(self._watch_loop(), name="screen-watcher")
        logger.info("ScreenWatcher avviato")
        await self._emit_status("active")

    async def stop(self) -> None:
        """Ferma il watcher gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ScreenWatcher fermato")

    def pause(self) -> None:
        self._paused = True
        logger.info("ScreenWatcher in pausa")
        asyncio.create_task(self._emit_status("paused"))

    def resume(self) -> None:
        self._paused = False
        logger.info("ScreenWatcher ripreso")
        asyncio.create_task(self._emit_status("active"))

    def get_status(self) -> dict:
        return {
            "active": self._running and not self._paused,
            "paused": self._paused,
            "captures_today": self._captures_today,
            "last_capture_time": self._last_capture_time,
            "last_capture_app": self._last_capture_app,
        }

    # ------------------------------------------------------------------
    # Loop principale
    # ------------------------------------------------------------------

    def set_error_notifier(self, notifier: Callable) -> None:
        """Registra callback per notifiche Telegram su errori ripetuti."""
        self._error_notifier = notifier

    async def _watch_loop(self) -> None:
        """Loop principale: polling ogni 30s + rilevamento cambio app."""
        while self._running:
            try:
                if not self._paused:
                    await self._check_and_capture()
                self._consecutive_errors = 0  # reset su successo
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._consecutive_errors += 1
                logger.error("ScreenWatcher errore nel loop (%d): %s", self._consecutive_errors, exc)
                # Notifica Telegram + stato error dopo N errori consecutivi
                if self._consecutive_errors == self._error_notify_threshold:
                    await self._emit_status("error", str(exc))
                    if self._error_notifier:
                        try:
                            await self._error_notifier(
                                f"⚠️ ScreenWatcher: {self._consecutive_errors} errori consecutivi.\n"
                                f"Ultimo: {exc}\n\nIl watcher continua a girare."
                            )
                        except Exception:
                            pass
            await asyncio.sleep(30)

    async def _check_and_capture(self) -> None:
        """Controlla se catturare: cambio app, diff pixel significativo."""
        # Recupera app attiva (thread sincrono → run_in_executor)
        app_info = await asyncio.get_event_loop().run_in_executor(
            None, self._get_active_app
        )
        if app_info is None:
            return

        app_name = app_info.get("name", "")
        bundle_id = app_info.get("bundle_id", "")

        # Blocklist check
        if self._is_blocked(app_name, bundle_id):
            return

        # Screenshot (in executor per non bloccare asyncio)
        screenshot_data = await asyncio.get_event_loop().run_in_executor(
            None, self._take_screenshot
        )
        if screenshot_data is None:
            return

        # Calcola hash frame per pixel diff
        frame_hash = hashlib.md5(screenshot_data).hexdigest()
        app_changed = bundle_id != self._last_app_bundle
        frame_changed = frame_hash != self._last_frame_hash

        if not app_changed and not frame_changed:
            return  # Nulla di nuovo

        # Pixel diff check se stessa app
        if not app_changed and frame_changed:
            diff = await asyncio.get_event_loop().run_in_executor(
                None, self._pixel_diff, screenshot_data
            )
            if diff < _PIXEL_DIFF_THRESHOLD:
                return  # Cambio troppo piccolo

        # Aggiorna stato
        self._last_app_bundle = bundle_id
        self._last_frame_hash = frame_hash

        # OCR
        text = await asyncio.get_event_loop().run_in_executor(
            None, self._ocr, screenshot_data
        )
        if not text or len(text.strip()) < 50:
            return  # Troppo poco testo, skip

        # Redaction + chunking + store
        clean_text = self._redact(text)
        await self._store_chunks(clean_text, app_name, bundle_id)

    # ------------------------------------------------------------------
    # macOS helpers (sincroni — eseguiti in executor)
    # ------------------------------------------------------------------

    def _get_active_app(self) -> dict | None:
        """Restituisce {name, bundle_id} dell'app attiva via NSWorkspace."""
        try:
            from AppKit import NSWorkspace  # type: ignore[import]
            ws = NSWorkspace.sharedWorkspace()
            app = ws.frontmostApplication()
            if app is None:
                return None
            return {
                "name": app.localizedName() or "",
                "bundle_id": app.bundleIdentifier() or "",
            }
        except Exception as exc:
            logger.debug("_get_active_app fallito: %s", exc)
            return None

    def _take_screenshot(self) -> bytes | None:
        """Screenshot dello schermo intero con mss."""
        try:
            import mss  # type: ignore[import]
            import mss.tools
            with mss.mss() as sct:
                monitor = sct.monitors[0]  # monitor 0 = tutti i monitor combinati
                img = sct.grab(monitor)
                return mss.tools.to_png(img.rgb, img.size)
        except Exception as exc:
            logger.debug("Screenshot fallito: %s", exc)
            return None

    def _pixel_diff(self, current_png: bytes) -> float:
        """Calcola la percentuale di pixel diversi rispetto al frame precedente."""
        try:
            import numpy as np  # type: ignore[import]
            from PIL import Image  # type: ignore[import]
            import io
            current = np.array(Image.open(io.BytesIO(current_png)).convert("RGB"))
            if not hasattr(self, "_prev_frame") or self._prev_frame is None:
                self._prev_frame = current
                return 1.0  # Prima cattura: forza capture
            diff = np.abs(current.astype(int) - self._prev_frame.astype(int))
            changed_pixels = np.sum(diff.mean(axis=2) > 10)
            total_pixels = current.shape[0] * current.shape[1]
            self._prev_frame = current
            return changed_pixels / total_pixels
        except Exception:
            return 1.0  # In caso di errore, forza capture

    def _ocr(self, png_bytes: bytes) -> str:
        """OCR via macOS Vision framework (pyobjc). Privacy: elaborazione locale."""
        try:
            import Vision  # type: ignore[import]
            import Quartz  # type: ignore[import]
            import objc  # type: ignore[import]

            # Converti PNG bytes in CGImage
            data = objc.lookUpClass("NSData").dataWithBytes_length_(png_bytes, len(png_bytes))
            provider = Quartz.CGDataProviderCreateWithCFData(data)
            cg_image = Quartz.CGImageCreateWithPNGDataProvider(provider, None, True, Quartz.kCGRenderingIntentDefault)
            if cg_image is None:
                return ""

            results: list[str] = []
            semaphore = __import__("threading").Event()

            request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(
                lambda req, err: results.extend(
                    obs.topCandidates_(1)[0].string()
                    for obs in (req.results() or [])
                    if obs.topCandidates_(1)
                ) or semaphore.set()
            )
            request.setRecognitionLanguages_(["it-IT", "en-US"])
            request.setUsesLanguageCorrection_(True)

            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
            handler.performRequests_error_([request], None)
            semaphore.wait(timeout=10)

            return "\n".join(results)
        except Exception as exc:
            logger.debug("OCR Vision fallito: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Redaction + chunking
    # ------------------------------------------------------------------

    @staticmethod
    def _redact(text: str) -> str:
        """Applica regex di redaction sui pattern sensibili."""
        for pattern in REDACTION_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
        """Divide il testo in chunk con overlap."""
        if len(text) <= chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            # Cerca il punto di taglio più naturale (newline o spazio)
            if end < len(text):
                nl = chunk.rfind("\n")
                sp = chunk.rfind(" ")
                cut = nl if nl > chunk_size * 0.7 else sp
                if cut > 0:
                    chunk = chunk[:cut]
            chunks.append(chunk.strip())
            start += len(chunk) - overlap
        return [c for c in chunks if c]

    # ------------------------------------------------------------------
    # Store in ChromaDB
    # ------------------------------------------------------------------

    async def _store_chunks(self, text: str, app_name: str, bundle_id: str) -> None:
        """Chunking + store in screen_memory ChromaDB."""
        chunks = self._chunk_text(text)
        ts = datetime.utcnow().isoformat()
        ts_safe = ts.replace(":", "-").replace(".", "-")
        bundle_safe = (bundle_id or "unknown").replace(".", "-")

        ids = [f"{ts_safe}_{bundle_safe}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "timestamp": ts,
                "app_name": app_name,
                "bundle_id": bundle_id,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]

        ok = await self.memory.add_screen_memory(chunks, metadatas, ids)
        if ok:
            self._captures_today += 1
            self._last_capture_time = ts
            self._last_capture_app = app_name
            logger.debug("ScreenWatcher: %d chunk salvati (%s)", len(chunks), app_name)

            if self._ws_broadcast:
                try:
                    step_desc = f"{app_name} — {len(chunks)} chunk"
                    # screen_watcher_capture: interpretato dal frontend come agent_step
                    await self._ws_broadcast({
                        "type": "watcher_capture",
                        "agent": "watcher",
                        "task_id": "watcher",
                        "step_id": ids[0],
                        "step_number": self._captures_today,
                        "step_type": "capture",
                        "description": step_desc,
                        "duration_ms": 0,
                        "timestamp": ts,
                        "app_name": app_name,
                        "chunks": len(chunks),
                    })
                    # Aggiorna anche lo status con lastTask
                    await self._ws_broadcast({
                        "type": "watcher_status",
                        "status": "active",
                        "last_task": step_desc,
                        "captures_today": self._captures_today,
                        "last_capture_time": ts,
                        "last_capture_app": app_name,
                    })
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Blocklist check
    # ------------------------------------------------------------------

    async def _emit_status(self, status: str, message: str = "") -> None:
        """Emette evento screen_watcher_status via WebSocket."""
        if self._ws_broadcast:
            try:
                await self._ws_broadcast({
                    "type": "watcher_status",
                    "status": status,          # 'active' | 'paused' | 'error'
                    "message": message,
                    "captures_today": self._captures_today,
                    "last_capture_time": self._last_capture_time,
                    "last_capture_app": self._last_capture_app,
                })
            except Exception:
                pass

    def _is_blocked(self, app_name: str, bundle_id: str) -> bool:
        """Controlla se l'app è nella blocklist."""
        if bundle_id in self._blocklist_bundles:
            return True
        if app_name in self._blocklist_names:
            return True
        # Pattern banche: bundle id che contiene 'bank' o 'banking'
        if bundle_id and ("bank" in bundle_id.lower() or "banking" in bundle_id.lower()):
            return True
        return False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_memories(self) -> int:
        """Elimina chunk più vecchi di SCREEN_RETENTION_DAYS. Chiamato dallo scheduler."""
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=settings.SCREEN_RETENTION_DAYS)).isoformat()
        deleted = await self.memory.delete_old_screen_memory(older_than_iso=cutoff)
        if deleted:
            logger.info("screen_cleanup: eliminati %d chunk (retention %dd)", deleted, settings.SCREEN_RETENTION_DAYS)
        return deleted
