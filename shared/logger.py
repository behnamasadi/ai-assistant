"""Centralized structured JSON logger that writes to stdout."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": record.name,
            "msg": record.getMessage(),
        }
        extras = getattr(record, "extras", None)
        if extras:
            payload.update(extras)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(service: str) -> logging.Logger:
    logger = logging.getLogger(service)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger


def log(logger: logging.Logger, level: str, msg: str, **fields: Any) -> None:
    """Emit a log record with extra structured fields."""
    logger.log(
        getattr(logging, level.upper()),
        msg,
        extra={"extras": fields},
    )
