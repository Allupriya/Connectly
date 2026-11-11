"""Central logging setup for the chat backend.

Every module gets its logger via :func:`get_logger`, so all log lines share a
single, clearly formatted style. The format includes the timestamp, level,
logger name (which process/module emitted it) and the message, e.g.::

    2026-07-28 22:50:01 | ERROR | backend.routers.chats | create_dm failed ...

This makes it easy, when inspecting the server response/terminal, to see
exactly which process raised an error and why.
"""

import logging
import sys

from .settings import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int | str | None = None) -> None:
    """Configure the root logger once (safe to call multiple times)."""
    global _configured
    if _configured:
        return

    level = level or settings.LOG_LEVEL

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if uvicorn/others already added some.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging has been configured."""
    setup_logging()
    return logging.getLogger(name)
