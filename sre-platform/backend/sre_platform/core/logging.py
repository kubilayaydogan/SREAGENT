"""Yapılandırılmış (JSON satır) loglama + correlation ID. Mesajlar ve alanlar redakte edilir."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from airlab_agent.redact import redact, redact_text

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "cid": correlation_id.get(),
               "msg": redact_text(record.getMessage(), "log")}
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extra:
            out.update(redact(extra, "log"))
        if record.exc_info:
            out["exc"] = redact_text(self.formatException(record.exc_info), "log")[-4000:]
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.handlers[:] = [h]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "mcp", "uvicorn.access"):
        logging.getLogger(noisy).setLevel("WARNING")
