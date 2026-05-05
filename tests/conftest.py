"""conftest.py — pre-imports real modules before test_block1_integration.py
stubs them via sys.modules.setdefault().

test_block1_integration.py uses sys.modules.setdefault() for package-level
modules (so pre-importing them here prevents stubbing) and explicitly replaces
apps.backend.core.models (handled separately in test_core_models.py via
importlib loading from disk).
"""
from __future__ import annotations

# Pre-import these so block1's setdefault() is a no-op for them:
import apps.backend.tools.file_gen  # noqa: F401 — ensures apps.backend.tools is a real package
import apps.backend.core.budget_manager  # noqa: F401 — imports real aiosqlite too
import apps.backend.agents._design.colors  # noqa: F401
import apps.backend.agents._design.utils  # noqa: F401
import apps.backend.agents._design.scoring  # noqa: F401
import apps.backend.core._pepe._confidence  # noqa: F401
import apps.backend.core._pepe._llm  # noqa: F401
import apps.backend.agents.base  # noqa: F401
import apps.backend.core._pepe._pipeline  # noqa: F401
