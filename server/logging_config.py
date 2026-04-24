from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from typing import Any


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
USER_IDENTITY: ContextVar[str] = ContextVar("user_identity", default="-")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": REQUEST_ID.get("-"),
            "user_identity": USER_IDENTITY.get("-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    use_json = os.getenv("CODEWEAVE_JSON_LOGS", "1").strip().lower() in {"1", "true", "yes", "on"}

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler()
    if use_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s user=%(user_identity)s] %(message)s"
            )
        )
    root.addHandler(handler)
