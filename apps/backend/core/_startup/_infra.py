"""Infrastructure init functions: memory, tools, storage, screen watcher."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("agentpexi.startup")


async def init_memory(settings, ws_broadcast: Callable) -> Any:
    """Init MemoryManager, wire KnowledgeBridge and WS broadcaster."""
    from apps.backend.core.memory import MemoryManager
    from apps.backend.core.knowledge_bridge import KnowledgeBridge

    memory = MemoryManager()
    await memory.init()
    memory.set_ws_broadcaster(ws_broadcast)

    bridge = KnowledgeBridge(memory=memory)
    memory.set_bridge_callback(bridge.on_new_insight)
    bridge.set_ws_broadcaster(ws_broadcast)

    logger.info("MemoryManager inizializzato + KnowledgeBridge registrato")
    return memory


async def init_tools(settings) -> tuple[Any, Any, Any]:
    """Init shared tools: NotionCalendar, WebSearchTool, TextExtractor."""
    from apps.backend.tools.notion_calendar import NotionCalendar
    from apps.backend.tools.web_search import WebSearchTool
    from apps.backend.tools.text_extract import TextExtractor

    notion_calendar = NotionCalendar(token=getattr(settings, "NOTION_API_TOKEN", ""))
    try:
        await notion_calendar.ensure_database()
        logger.info("Notion Calendar database pronto")
    except Exception as exc:
        logger.warning("notion_calendar.ensure_database fallito (fail-safe): %s", exc)

    web_search = WebSearchTool()
    text_extractor = TextExtractor(max_chars=settings.SUMMARIZE_MAX_CHARS)
    return notion_calendar, web_search, text_extractor


async def init_storage() -> Any:
    """Init StorageManager."""
    from apps.backend.core.storage import StorageManager

    storage = StorageManager()
    storage.ensure_dirs()
    logger.info("StorageManager inizializzato")
    return storage


async def init_screen_watcher(memory, ws_broadcast: Callable) -> tuple[Any, str | None]:
    """Start ScreenWatcher (fail-safe). Returns (watcher_or_None, error_str_or_None)."""
    from apps.backend.screen.watcher import ScreenWatcher

    watcher = ScreenWatcher(memory=memory, ws_broadcaster=ws_broadcast)
    try:
        await watcher.start()
        logger.info("ScreenWatcher avviato")
        return watcher, None
    except Exception as exc:
        logger.warning("ScreenWatcher non avviato: %s", exc)
        return None, str(exc)
