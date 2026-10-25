from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import LOGS_DIR

LOG_PATH = LOGS_DIR / "installer.log"
_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger("modinstaller")
    if _configured:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%d.%m.%Y %H:%M:%S")
        )
        logger.addHandler(handler)
    except OSError:
        pass
    if sys.stderr is not None and getattr(sys, "frozen", False) is False:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(stream)
    _configured = True
    return logger


def get_logger(name: str = "app") -> logging.Logger:
    return logging.getLogger(f"modinstaller.{name}")


def log_file_path() -> Path:
    return LOG_PATH
