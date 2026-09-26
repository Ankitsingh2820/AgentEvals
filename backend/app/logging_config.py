"""Structured (JSON-lines) logging.

Log records carry identifiers (run_id, agent_id, request_id, trace_id) and metrics only.
Prompts, model outputs and tool payloads are stored in the trace tables, never written to
logs, so logs can be shipped to third-party sinks without leaking sensitive content. As a
second line of defence, anything shaped like a credential is redacted before output.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from opentelemetry import trace

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic keys
    re.compile(r"\bae_[A-Za-z0-9_\-]{20,}"),  # AgentEval API keys
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
]


def redact(value):
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
