"""Optional non-blocking n8n completion event (metadata only, never the transcript)."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request as urlrequest


def send_completion_event(meeting_id: str, status: str, actions_count: int, risks_count: int,
                          correlation_id: str, bus: Any) -> bool:
    """POST one small JSON payload to the configured TEST webhook; failure never blocks the pipeline."""
    webhook = os.getenv("N8N_EVENT_WEBHOOK", "")
    if not webhook:
        return False
    payload = {"meeting_id": meeting_id, "status": status, "actions_count": actions_count,
               "risks_count": risks_count, "correlation_id": correlation_id}
    try:
        req = urlrequest.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
        with urlrequest.urlopen(req, timeout=10) as response:
            ok = 200 <= response.status < 300
    except (error.URLError, OSError, ValueError) as exc:
        bus.emit("n8n.event_failed", meeting_id, error=type(exc).__name__)
        return False
    bus.emit("n8n.event_sent", meeting_id, ok=ok)
    return ok
