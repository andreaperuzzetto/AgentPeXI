"""Configurazione centralizzata AgentPeXI — legge .env via Pydantic BaseSettings."""

from pydantic_settings import BaseSettings

# Costanti modelli Anthropic
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"


class Settings(BaseSettings):
    # LLM
    ANTHROPIC_API_KEY: str = ""
    VOYAGE_API_KEY: str = ""

    # Etsy
    ETSY_API_KEY: str = ""
    ETSY_API_SECRET: str = ""
    ETSY_SHOP_ID: str = ""
    ETSY_ENV: str = "sandbox"

    # Tools
    TAVILY_API_KEY: str = ""
    REPLICATE_API_KEY: str = ""

    # Voice
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Storage & Security
    STORAGE_PATH: str = "/Volumes/NomeSSD/agentpexi-storage"
    SECRET_KEY: str = ""

    # System
    MAX_PARALLEL_TASKS: int = 5
    COST_ALERT_THRESHOLD_EUR: float = 70.0
    PORT: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
