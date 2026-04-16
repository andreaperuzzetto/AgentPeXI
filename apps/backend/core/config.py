"""Configurazione centralizzata AgentPeXI — legge .env via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings

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

    # Personal Agents — Remind
    REMIND_CHECKER_INTERVAL: int = 5         # minuti tra check reminder scaduti
    REMIND_UNACK_PING_HOURS: int = 1         # ore tra re-ping reminder non confermati

    # Personal Agents — Summarize
    SUMMARIZE_MAX_CHARS: int = 20_000        # soglia per passare a map-reduce

    # Personal Agents — Research Personal
    DDGS_MAX_RESULTS: int = 8                # risultati DuckDuckGo per quick mode

    # Personal Agents — Learning loop
    LEARNING_DECAY_DAYS: int = 7             # applicazione decay ogni N giorni
    LEARNING_DECAY_FACTOR: float = 0.98      # fattore moltiplicativo decay peso

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

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Storage & Security
    STORAGE_PATH: str = "/Volumes/Progetti/agentpexi-storage"
    SECRET_KEY: str = ""

    # System
    MAX_PARALLEL_TASKS: int = 5
    COST_ALERT_THRESHOLD_EUR: float = 70.0
    PORT: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
