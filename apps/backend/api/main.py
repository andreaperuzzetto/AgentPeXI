"""FastAPI + WebSocket — API principale AgentPeXI."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import apps.backend.api.state as state
from apps.backend.api.middleware import RequestIDFilter, RequestIDMiddleware
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from apps.backend.core.config import settings
from apps.backend.core.memory import MemoryManager  # noqa: F401 — used by routes via state
from apps.backend.core.startup import (
    init_memory,
    init_tools,
    init_storage,
    init_pepe,
    init_wiki,
    init_etsy,
    init_autonomy_services,
    init_all_agents,
    init_screen_watcher,
    build_autopilot_callables,
    init_autopilot_loop,
    init_scheduler,
    init_telegram_bot,
)
from apps.backend.api.routers import (
    autopilot,
    etsy,
    finance,
    memory_routes,
    personal,
    screen,
    system,
    wiki,
)

# ------------------------------------------------------------------
# Logging — console + file rotante in logs/
# ------------------------------------------------------------------

_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] [%(request_id)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "agentpexi.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)

# Inject request_id into every log record via ContextVar
_request_id_filter = RequestIDFilter()
for _h in logging.root.handlers:
    _h.addFilter(_request_id_filter)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

logger = logging.getLogger("agentpexi.api")


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: MemoryManager, Pepe, workers, Telegram bot. Shutdown: graceful stop."""

    # ── Phase 1: Memory + tools + storage ──────────────────────────────────
    state.memory = await init_memory(settings, state.ws_manager.broadcast)
    notion_calendar, web_search, text_extractor = await init_tools(settings)
    state.storage = await init_storage()

    # ── Telegram broadcast helpers (lazy closures — read state at call time) ─
    async def telegram_broadcast(msg: str) -> None:
        if state.pepe and hasattr(state.pepe, "notify_telegram"):
            await state.pepe.notify_telegram(msg, priority=True)

    async def telegram_broadcast_markup(msg: str, reply_markup) -> None:
        """Invia messaggio con InlineKeyboardMarkup — usata da AutopilotLoop per approve/skip."""
        if not state.telegram_bot or not settings.TELEGRAM_CHAT_ID:
            return
        try:
            await state.telegram_bot._app.bot.send_message(
                chat_id=int(settings.TELEGRAM_CHAT_ID),
                text=msg,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("telegram_broadcast_markup fallito: %s", exc)

    # ── Phase 2: Pepe orchestrator + core agents ────────────────────────────
    _pepe = await init_pepe(state.memory, state.storage, state.ws_manager.broadcast, telegram_broadcast)
    state.pepe = _pepe.pepe

    await init_wiki(state.pepe, settings)

    # ── Phase 3: Etsy + Autonomy Layer ─────────────────────────────────────
    state.etsy_api = await init_etsy(state.memory, state.pepe)

    _autonomy = await init_autonomy_services(state.memory)
    state.production_queue = _autonomy.production_queue
    state.budget_manager = _autonomy.budget_manager
    state.publication_policy = _autonomy.publication_policy

    # ── Phase 4: All agents + intelligence/growth singletons ────────────────
    _agents = await init_all_agents(
        pepe=state.pepe,
        memory=state.memory,
        storage=state.storage,
        etsy_api=state.etsy_api,
        ws_broadcast=state.ws_manager.broadcast,
        telegram_broadcast=telegram_broadcast,
        notion_calendar=notion_calendar,
        web_search=web_search,
        text_extractor=text_extractor,
        production_queue=state.production_queue,
        publication_policy=state.publication_policy,
    )
    state.bundle_strategy = _agents.bundle_strategy
    state.shop_optimizer = _agents.shop_optimizer
    state.etsy_ads_manager = _agents.etsy_ads_manager
    state.finance_tracker = _agents.finance_tracker

    # ── Phase 5: Screen watcher ─────────────────────────────────────────────
    state.screen_watcher, _screen_watcher_error = await init_screen_watcher(
        state.memory, state.ws_manager.broadcast
    )

    # ── Phase 6: AutopilotLoop ──────────────────────────────────────────────
    _design_pipeline, _niche_picker, _bundle_checker = build_autopilot_callables(
        memory=state.memory,
        pepe=state.pepe,
        production_queue=state.production_queue,
        bundle_strategy=state.bundle_strategy,
        learning_loop=_agents.learning_loop,
    )
    state.autopilot_loop = await init_autopilot_loop(
        db=_autonomy.db,
        production_queue=state.production_queue,
        budget_manager=state.budget_manager,
        publication_policy=state.publication_policy,
        bot_send=telegram_broadcast,
        bot_send_markup=telegram_broadcast_markup,
        design_pipeline=_design_pipeline,
        niche_picker=_niche_picker,
        bundle_checker=_bundle_checker,
    )

    # ── Phase 7: Scheduler ──────────────────────────────────────────────────
    state.scheduler = await init_scheduler(
        memory=state.memory,
        ws_broadcast=state.ws_manager.broadcast,
        pepe=state.pepe,
        storage=state.storage,
        research_agent=_pepe.research_agent,
        design_agent=_pepe.design_agent,
        publisher_agent=_agents.publisher_agent,
        analytics_agent=_agents.analytics_agent,
        finance_agent=_agents.finance_agent,
        telegram_broadcast=telegram_broadcast,
        screen_watcher=state.screen_watcher,
        production_queue=state.production_queue,
        budget_manager=state.budget_manager,
        publication_policy=state.publication_policy,
        autopilot_loop=state.autopilot_loop,
        etsy_api=state.etsy_api,
        shop_optimizer=state.shop_optimizer,
        etsy_ads_manager=state.etsy_ads_manager,
        learning_loop=_agents.learning_loop,
    )

    # ── Phase 8: Telegram bot ───────────────────────────────────────────────
    state.telegram_bot = await init_telegram_bot(
        pepe=state.pepe,
        scheduler=state.scheduler,
        screen_watcher=state.screen_watcher,
        autopilot_loop=state.autopilot_loop,
        production_queue=state.production_queue,
        budget_manager=state.budget_manager,
        publication_policy=state.publication_policy,
        etsy_api=state.etsy_api,
        analytics_agent=_agents.analytics_agent,
        learning_loop=_agents.learning_loop,
        bundle_strategy=state.bundle_strategy,
        shop_optimizer=state.shop_optimizer,
        etsy_ads_manager=state.etsy_ads_manager,
        finance_tracker=state.finance_tracker,
    )

    # ── AutopilotLoop: restore previous run state ───────────────────────────
    _ap_prev_status = await state.autopilot_loop._get_status()
    if _ap_prev_status == "running":
        await state.autopilot_loop.start()
        logger.info("AutopilotLoop ripreso (stato precedente: running)")
    else:
        await state.autopilot_loop._set_status("paused_manual")
        logger.info("AutopilotLoop in attesa di /run (stato precedente: %s)", _ap_prev_status)

    await state.scheduler.start()
    logger.info("Scheduler avviato")

    if state.screen_watcher is not None:
        state.screen_watcher.set_error_notifier(telegram_broadcast)

    if _screen_watcher_error:
        await telegram_broadcast(
            f"⚠️ ScreenWatcher non avviato all'avvio del server.\n"
            f"Errore: {_screen_watcher_error}\n\n"
            "Controlla che mss, pyobjc e Vision siano installati. "
            "Il resto del sistema funziona normalmente."
        )

    yield

    # ── Shutdown (reverse order) ─────────────────────────────────────────────
    await state.telegram_bot.stop()
    if state.autopilot_loop is not None:
        await state.autopilot_loop.stop()
        logger.info("AutopilotLoop fermato")
    await state.scheduler.stop()
    if state.screen_watcher is not None:
        await state.screen_watcher.stop()
        logger.info("ScreenWatcher fermato")
    if state.etsy_api is not None:
        await state.etsy_api.close()
        logger.info("EtsyAPI chiuso")
    if state.pepe is not None:
        await state.pepe.stop()
        logger.info("Pepe fermato")
    if state.memory is not None:
        await state.memory.close()
        logger.info("MemoryManager chiuso")


# ------------------------------------------------------------------
# App FastAPI
# ------------------------------------------------------------------

app = FastAPI(title="AgentPeXI", version="0.1.0", lifespan=lifespan)
app.state.limiter = state.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Personal-Key", "Content-Type", "X-Request-ID"],
)
app.add_middleware(RequestIDMiddleware)

# ------------------------------------------------------------------
# Router includes
# ------------------------------------------------------------------

app.include_router(system.router)
app.include_router(autopilot.router)
app.include_router(screen.router)
app.include_router(personal.router)
app.include_router(wiki.router)
app.include_router(memory_routes.router)
app.include_router(etsy.router)
app.include_router(finance.router)


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------


@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket, key: str = Query("")) -> None:
    """WebSocket dedicato al canale voce Orb — wake word "Jarvis" via Whisper.

    Protocollo a due fasi:

    Fase 1 — Wake word (Whisper-based keyword spotting):
      Client → Server: binario (blob WebM 3s completo, da MediaRecorder monouso)
      Ogni blob è un WebM auto-contenuto → Whisper trascrive → cerca "jarvis".
      Se trovato:
        Server → Client: {"type": "wake"}

    Fase 2 — Utterance (STT + Pepe + TTS):
      Client → Server: binario (blob WebM completo, max 8s)
      Server → Client: {"type": "response", "text": "...", "audio_b64": "..."|null}
        audio_b64: M4A/AAC base64 (macOS say+afconvert), null se TTS non disponibile
        In assenza di audio_b64 il frontend usa il browser SpeechSynthesis come fallback.

    Dopo la risposta il ciclo riparte dalla Fase 1.
    Canale separato da /ws/chat — non interferisce con gli eventi UI.
    """
    import hmac
    api_key = settings.PERSONAL_API_KEY
    if not api_key or not hmac.compare_digest(key, api_key):
        await websocket.close(code=4003)
        return
    from apps.backend.voice.stt import transcribe
    from apps.backend.voice.tts import play_via_say
    from apps.backend.voice.wake import detect_wake_word_in_text
    from apps.backend.voice import wake_oww
    from apps.backend.voice import collector as voice_collector

    await websocket.accept()
    logger.info("WebSocket /ws/voice connesso")

    phase = "wakeword"              # "wakeword" | "utterance"
    _post_reply_timeout: float | None = None  # secondi, None = nessun timeout

    # Durata della finestra di ascolto post-risposta (Step 6)
    _POST_REPLY_S: float = 20.0

    try:
        while True:
            # ── Receive con timeout opzionale (post-reply window) ────────────
            try:
                if _post_reply_timeout is not None:
                    data = await asyncio.wait_for(
                        websocket.receive_bytes(), timeout=_post_reply_timeout
                    )
                    _post_reply_timeout = None
                else:
                    data = await websocket.receive_bytes()
            except asyncio.TimeoutError:
                # L'utente non ha parlato nella finestra post-reply → torna al wake word
                logger.info("Voice: post-reply window scaduto → ritorno in ascolto wake word")
                await websocket.send_json({"type": "done"})
                phase = "wakeword"
                _post_reply_timeout = None
                continue

            # ── Fase 1: ogni messaggio è un blob WebM completo da 3s ──────────
            if phase == "wakeword":
                try:
                    # ── Raccolta campioni (se attiva) ────────────────────────
                    if voice_collector.is_active():
                        await asyncio.get_running_loop().run_in_executor(
                            None, voice_collector.save_sample, data
                        )

                    # ── Wake word detection ──────────────────────────────────
                    wake_detected = False

                    _use_whisper = True
                    try:
                        oww_score = await wake_oww.predict(data)
                        if oww_score is not None:
                            _use_whisper = False
                            wake_detected = wake_oww.is_wake_word(oww_score)
                        else:
                            logger.warning("wake_oww: predict() → None, uso Whisper (emergenza)")
                    except Exception as oww_exc:
                        logger.warning("wake_oww eccezione (%s) — uso Whisper (emergenza)", oww_exc)

                    if _use_whisper:
                        _tmp_wake = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
                        try:
                            _tmp_wake.write(data)
                            _tmp_wake.close()
                            wake_text = await transcribe(_tmp_wake.name, language=settings.WHISPER_LANGUAGE, vad_filter=True)
                            if wake_text:
                                logger.info("Wake Whisper fallback: '%s'", wake_text[:80])
                            wake_detected = detect_wake_word_in_text(wake_text)
                        finally:
                            try:
                                os.unlink(_tmp_wake.name)
                            except OSError:
                                pass

                    if wake_detected:
                        import random
                        _WAKE_ACKS = ["Dimmi.", "Sì?", "Ti ascolto.", "Dimmi pure.", "Eccomi."]
                        await play_via_say(random.choice(_WAKE_ACKS))
                        await websocket.send_json({"type": "wake"})
                        # ── Drain handshake ─────────────────────────────────
                        drained = 0
                        while True:
                            raw = await websocket.receive()
                            if raw.get("bytes"):
                                drained += 1
                                logger.debug(
                                    "Drenato blob stale #%d (%d bytes)",
                                    drained, len(raw["bytes"]),
                                )
                            elif raw.get("text"):
                                try:
                                    ctrl = json.loads(raw["text"])
                                    if ctrl.get("type") == "utterance_ready":
                                        logger.debug(
                                            "Frontend pronto per utterance (drenati %d blob stale)",
                                            drained,
                                        )
                                        break
                                except Exception as exc:
                                    logger.debug("ws/voice: JSON parse ctrl msg fallito: %s", exc)
                        phase = "utterance"
                except Exception as exc:
                    logger.warning("Errore wake word detection: %s", exc)

            # ── Fase 2: trascrivi utterance → Pepe → TTS → risposta ──
            elif phase == "utterance":
                _tmp_utt = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
                try:
                    _tmp_utt.write(data)
                    _tmp_utt.close()
                    # Utterance: forza lingua italiana per massima accuratezza
                    text = await transcribe(_tmp_utt.name, language=settings.WHISPER_LANGUAGE, vad_filter=True)
                    logger.info("Voice utterance: '%s'", text[:120])

                    if text.strip():
                        import random
                        _THINK_ACKS = [
                            "Vediamo.",
                            "Un attimo.",
                            "Ci penso.",
                            "Dammi un secondo.",
                            "Mmh, vediamo.",
                        ]
                        _ACK_AFTER_S = 1.5   # secondi di attesa prima di suonare l'ack

                        handle_task = asyncio.create_task(
                            state.pepe.handle_user_message(
                                message=text,
                                session_id="voice_orb",
                                source="orb_voice",
                            )
                        )

                        # Aspetta _ACK_AFTER_S — se ancora in corso, suona l'ack
                        _VOICE_TIMEOUT_S = 45.0  # timeout massimo per handle_user_message
                        done, _ = await asyncio.wait({handle_task}, timeout=_ACK_AFTER_S)
                        if not done:
                            # Pepe sta ancora elaborando → ack mentre aspettiamo
                            await play_via_say(random.choice(_THINK_ACKS))

                        # Timeout globale: evita Think perenne se LLM/Ollama non risponde
                        try:
                            reply = await asyncio.wait_for(
                                asyncio.shield(handle_task), timeout=_VOICE_TIMEOUT_S
                            )
                        except asyncio.TimeoutError:
                            handle_task.cancel()
                            reply = "Ci ho messo troppo, riprova."
                            logger.warning("Voice: handle_user_message timeout (%ss)", _VOICE_TIMEOUT_S)
                        logger.info("Voice response → '%s'", reply[:120] if reply else '<VUOTO>')

                        if not reply or not reply.strip():
                            reply = "Scusa, puoi ripetere?"
                            logger.warning("Voice: Pepe ha restituito risposta vuota — fallback attivo")

                        # Controlla se Pepe ha una domanda in sospeso (clarification)
                        is_clarification = await state.pepe.has_pending_voice_clarification()

                        await websocket.send_json({"type": "speaking", "text": reply})
                        await play_via_say(reply)

                        if is_clarification:
                            # Rimane in utterance — manda "clarify" invece di "done"
                            await websocket.send_json({"type": "clarify"})
                            logger.info("Voice: Pepe in attesa di risposta, fase utterance mantenuta")
                            # phase rimane "utterance"
                        else:
                            await websocket.send_json({
                                "type": "post_reply_listen",
                                "timeout_ms": int(_POST_REPLY_S * 1000),
                            })
                            _post_reply_timeout = _POST_REPLY_S
                            logger.info("Voice: post-reply window aperto (%.0fs)", _POST_REPLY_S)
                            # phase rimane "utterance"
                    else:
                        # Nessun testo rilevato — torna in ascolto
                        await websocket.send_json({"type": "done"})
                        phase = "wakeword"

                except Exception as stt_exc:
                    logger.exception("Errore STT/Pepe in /ws/voice: %s", stt_exc)
                    await websocket.send_json({
                        "type": "error",
                        "message": "Errore elaborazione",
                        "detail": str(stt_exc),
                        "agent": "stt/pepe",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    _post_reply_timeout = None
                    phase = "wakeword"
                finally:
                    try:
                        os.unlink(_tmp_utt.name)
                    except OSError:
                        pass

    except WebSocketDisconnect:
        logger.info("WebSocket /ws/voice disconnesso")
    except Exception:
        logger.exception("Errore imprevisto in /ws/voice")


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, key: str = Query("")) -> None:
    """WebSocket unidirezionale: broadcast eventi sistema → client (dashboard).
    Il frontend non invia messaggi — usa solo Telegram per interagire con Pepe.
    """
    import hmac
    api_key = settings.PERSONAL_API_KEY
    if not api_key or not hmac.compare_digest(key, api_key):
        await ws.close(code=4003)
        return
    await state.ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        state.ws_manager.disconnect(ws)
    except Exception:
        state.ws_manager.disconnect(ws)


# ------------------------------------------------------------------
# Static files (frontend build) — montati per ultimi
# ------------------------------------------------------------------

_frontend_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"
)
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
