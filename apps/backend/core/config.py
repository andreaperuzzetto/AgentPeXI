"""Configurazione centralizzata AgentPeXI — legge .env via Pydantic BaseSettings."""

import logging
import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("agentpexi.config")

# Costanti modelli Anthropic
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"


class Settings(BaseSettings):
    # LLM — Anthropic
    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""

    # LLM — Ollama (Personal domain)
    OLLAMA_MODEL: str = "qwen3:8b"           # upgraded da 4b per migliore accuracy classificazione
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_KEEP_ALIVE: str = "-1"

    # Personal domain — filesystem
    PERSONAL_ALLOWED_DIRS: str = ""  # CSV di path, es: /Users/andrea/Documents,/Users/andrea/Desktop

    # Personal domain — API keys esterne
    NOTION_API_TOKEN: str = ""
    NOTION_REMINDERS_DB_ID: str = ""         # se vuoto: cerca/crea DB automaticamente
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""
    PERSONAL_API_KEY: str = ""  # chiave locale per /api/personal/* e /api/screen/*

    # Screen Watcher (Blocco 2)
    SCREEN_RETENTION_DAYS: int = 30          # giorni di retention ChromaDB screen_memory
    SCREEN_BLOCKLIST: str = ""               # app aggiuntive da bloccare (CSV bundle id o nome)

    # Personal Agents — Urgency system
    URGENCY_OLLAMA_TIMEOUT: int = 8          # secondi timeout classificatore Ollama
    URGENCY_MEDIUM_DIGEST_HOUR: int = 18     # ora (0-23) invio digest MEDIUM giornaliero

    # Personal Agents — Remind
    REMIND_CHECKER_INTERVAL: int = 2         # minuti tra check reminder scaduti
    REMIND_UNACK_PING_HOURS: int = 1         # ore tra re-ping reminder non confermati

    # Personal Agents — Summarize
    SUMMARIZE_MAX_CHARS: int = 20_000        # soglia per passare a map-reduce
    SUMMARIZE_MAX_CHUNKS: int = 5            # massimo chunk da processare (≈15.000 chars)
    SUMMARIZE_CHUNK_THRESHOLD: int = 3_000   # dimensione singolo chunk in caratteri

    # Personal Agents — Research Personal
    DDGS_MAX_RESULTS: int = 8                # risultati DuckDuckGo per quick mode
    DDGS_RETRY_WAIT_SECS: int = 3            # attesa tra retry DuckDuckGo (rate limit)

    # Personal Agents — Learning loop
    LEARNING_DECAY_DAYS: int = 7             # applicazione decay ogni N giorni
    LEARNING_DECAY_FACTOR: float = 0.98      # fattore moltiplicativo decay peso
    LEARNING_ACCEPTANCE_THRESHOLD: float = 0.02  # delta minimo per accettare un pattern
    LEARNING_EVAL_WINDOW: int = 10           # ultimi N task per calcolare baseline

    # Etsy
    ETSY_API_KEY: str = ""
    ETSY_API_SECRET: str = ""
    ETSY_SHOP_ID: str = ""
    ETSY_ENV: str = "sandbox"

    # Tools
    TAVILY_API_KEY: str = ""
    REPLICATE_API_KEY: str = ""
    FAL_KEY: str = ""  # fal.ai — Nano Banana Pro (primario). Se vuoto, fallback su Replicate.

    # Voice
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""
    ELEVENLABS_MAX_CHARS: int = 5000  # limite caratteri per singola richiesta TTS
    WHISPER_MODEL: str = "base"       # tiny | base | small | medium | large-v3
    WHISPER_DEVICE: str = "cpu"       # cpu | cuda
    WHISPER_LANGUAGE: str = "it"      # codice lingua ISO 639-1

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Wiki knowledge base (Blocco 5.2)
    WIKI_BASE_PATH: str = "knowledge_base"  # relativo alla root di AgentPeXI
    # Opzionale: path assoluto verso vault Obsidian esistente:
    # WIKI_BASE_PATH: str = "/Users/andrea/Library/Mobile Documents/iCloud~md~obsidian/Documents/AgentPeXI"

    # Storage & Security
    STORAGE_PATH: str = "~/.agentpexi-storage"  # sovrascrivibile via STORAGE_PATH nel .env
    SECRET_KEY: str = ""

    # CORS — origini consentite (CSV). Default: solo localhost dev (Vite 5173 + produzione 8000)
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:8000"

    # System
    MAX_PARALLEL_TASKS: int = 5
    COST_ALERT_THRESHOLD_EUR: float = 70.0
    USD_EUR_RATE: float = 0.92         # tasso di cambio USD→EUR, sovrascrivibile via .env

    # Prezzi LLM Anthropic (USD per milione di token) — aggiornare quando Anthropic cambia listino
    LLM_SONNET_INPUT_PRICE: float = 3.0     # $3/M input token
    LLM_SONNET_OUTPUT_PRICE: float = 15.0   # $15/M output token
    LLM_SONNET_CACHE_READ_PRICE: float = 0.3    # $0.30/M cache read
    LLM_SONNET_CACHE_WRITE_PRICE: float = 3.75  # $3.75/M cache write
    LLM_HAIKU_INPUT_PRICE: float = 0.80    # $0.80/M input token
    LLM_HAIKU_OUTPUT_PRICE: float = 4.0    # $4/M output token
    LLM_HAIKU_CACHE_READ_PRICE: float = 0.08    # $0.08/M cache read
    LLM_HAIKU_CACHE_WRITE_PRICE: float = 1.0    # $1/M cache write

    @field_validator("STORAGE_PATH")
    @classmethod
    def expand_storage_path(cls, v: str) -> str:
        return os.path.expanduser(v)

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_set(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "SECRET_KEY non configurata — impostare SECRET_KEY nel .env "
                "con una chiave Fernet valida (es: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`)"
            )
        return v

    @model_validator(mode="after")
    def warn_missing_api_keys(self) -> "Settings":
        """Logga warning per API key critiche non configurate."""
        _CRITICAL: list[tuple[str, str]] = [
            ("ANTHROPIC_API_KEY", "LLM Anthropic (Etsy domain)"),
            ("TAVILY_API_KEY", "ricerca web Tavily"),
            ("TELEGRAM_BOT_TOKEN", "bot Telegram"),
            ("TELEGRAM_CHAT_ID", "notifiche Telegram"),
            ("ETSY_API_KEY", "API Etsy"),
        ]
        for attr, label in _CRITICAL:
            if not getattr(self, attr, ""):
                logger.warning(
                    "[config] %s non configurata (%s) — aggiungere al .env",
                    attr,
                    label,
                )
        return self
    PORT: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
