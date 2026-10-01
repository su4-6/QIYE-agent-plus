"""Request correlation without storing credentials or user text in logs."""
import contextvars
import json
import logging
from datetime import datetime, timezone

request_id = contextvars.ContextVar("request_id", default="")


def configure_logging():
    logger = logging.getLogger("atlas.events")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def event(name: str, **fields) -> None:
    logging.getLogger("atlas.events").info(json.dumps(
        {"event": name, "request_id": request_id.get(), "timestamp": datetime.now(timezone.utc).isoformat(), **fields}, ensure_ascii=False))
