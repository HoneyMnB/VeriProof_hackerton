"""Tool execution logger.
docker exec cs-api tail -f /app/.runtime/tools.log 명령어로 로그 확인 가능
``ToolsLogger`` writes tool-related application logs to
``veriproof/.runtime/tools.log``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock


class ToolsLogger:
    """Write tool logs to ``veriproof/.runtime/tools.log``.

    Usage::

        from services.tools_logger import ToolsLogger

        tool_log = ToolsLogger()
        tool_log.info("image analysis started asset_id=%s", asset_id)
        try:
            run_tool()
        except Exception:
            tool_log.exception("image analysis failed asset_id=%s", asset_id)
            raise

    The underlying :class:`logging.Logger` is available through ``logger`` for
    standard logging methods not exposed directly by this class.
    """

    _LOGGER_NAME = "veriproof.tools"
    _LOG_PATH = Path(__file__).resolve().parents[1] / ".runtime" / "tools.log"
    _FORMATTER = logging.Formatter(
        "{levelname} {asctime} {name} {message}", style="{"
    )
    _handler_lock = Lock()

    def __init__(self) -> None:
        self.logger = logging.getLogger(self._LOGGER_NAME)
        self._ensure_file_handler()

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        """Record a debug message."""
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        """Record an informational message."""
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        """Record a warning message."""
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        """Record an error message."""
        self.logger.error(message, *args, **kwargs)

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        """Record an error message with the active exception traceback."""
        self.logger.exception(message, *args, **kwargs)

    def _ensure_file_handler(self) -> None:
        with self._handler_lock:
            self._LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_path = self._LOG_PATH.resolve()
            if any(
                isinstance(handler, logging.FileHandler)
                and Path(handler.baseFilename).resolve() == log_path
                for handler in self.logger.handlers
            ):
                return

            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(self._FORMATTER)
            self.logger.addHandler(handler)
