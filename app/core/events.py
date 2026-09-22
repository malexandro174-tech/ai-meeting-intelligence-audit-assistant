"""Structured observability: one correlation id per meeting, JSON events, cost counters, log sanitization."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from typing import Any

SENSITIVE_MARKERS = ("token", "secret", "password", "authorization", "api_key", "credential")


def sanitize(value: Any) -> Any:
    """Drop or mask anything that looks like credential material before it reaches a log."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if any(m in str(k).lower() for m in SENSITIVE_MARKERS) else sanitize(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 64 and any(m in value.lower()[:24] for m in SENSITIVE_MARKERS):
        return "<redacted>"
    return value


class EventBus:
    """Emits one structured JSON line per pipeline event; counters track cost observability."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdout
        self.costs: Counter[str] = Counter()

    def emit(self, event: str, correlation_id: str, **metadata: Any) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event,
                  "correlation_id": correlation_id, **sanitize(metadata)}
        self.stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stream.flush()

    def count(self, metric: str, amount: int = 1) -> None:
        self.costs[metric] += amount


TYPED_FAILURES = {
    "AUTH_FAILED", "UPLOAD_FAILED", "TRANSCRIPTION_FAILED", "TIMEOUT", "RATE_LIMITED",
    "INVALID_MEDIA", "PROVIDER_UNAVAILABLE", "CREDENTIAL_UNAVAILABLE", "ANALYSIS_FAILED",
    "STORAGE_FAILED", "TELEGRAM_FAILED", "UNSUPPORTED_MEDIA", "MEDIA_TOO_LARGE",
}


class MeetingPipelineError(RuntimeError):
    """Typed pipeline failure; never carries secret material."""

    def __init__(self, code: str, detail: str = "", retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}"[:300])
        self.code, self.detail, self.retryable = code, detail, retryable
