from __future__ import annotations

import logging

from services.tools_logger import ToolsLogger


def test_tools_logger_writes_to_runtime_log_without_duplicate_handlers(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / ".runtime" / "tools.log"
    monkeypatch.setattr(ToolsLogger, "_LOG_PATH", log_path)

    logger = logging.getLogger(ToolsLogger._LOGGER_NAME)
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    try:
        ToolsLogger().info("tool completed id=%s", "tool-1")
        ToolsLogger().info("tool completed id=%s", "tool-2")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)

    assert log_path.read_text(encoding="utf-8").count("tool completed") == 2
