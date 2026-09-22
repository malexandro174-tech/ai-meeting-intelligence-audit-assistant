"""Optional lightweight operator web view: latest meetings, statuses, actions, risks.

Dark theme (navy/graphite/burgundy). Read-only; served from the app process.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .meetings.storage import MeetingStore

STYLE = """body{background:#0d1321;color:#d8dee9;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:24px}
h1{color:#7d1f35;}table{border-collapse:collapse;width:100%;background:#141a2a;margin-top:12px}
th{background:#1d2436;color:#aeb8cc;text-align:left;padding:8px}td{padding:8px;border-bottom:1px solid #1d2436}
.ok{color:#5fbf77}.warn{color:#d9a441}.err{color:#c94f5e}.muted{color:#7a8499}"""


def _render(store: MeetingStore) -> str:
    rows = store.recent_meetings(limit=30)
    kpi = store.kpi_snapshot()
    row_html = "".join(
        f"<tr><td>{r['meeting_id']}</td><td>{r['filename']}</td>"
        f"<td class=\"{'ok' if r['state'] == 'COMPLETED' else 'err' if r['state'] == 'FAILED' else 'warn'}\">{r['state']}</td>"
        f"<td>{r['duration_seconds'] or '—'}</td><td>{r['speaker_count'] or '—'}</td>"
        f"<td>{r['actions']}</td><td>{r['risks']}</td></tr>" for r in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Meeting Intelligence</title>
<style>{STYLE}</style></head><body>
<h1>AI Meeting Intelligence — Operator View</h1>
<p class="muted">Meetings: {kpi['meetings_count']} · minutes: {kpi['total_duration_minutes']} ·
open actions: {kpi['open_actions_count']} · critical risks: {kpi['critical_risks_count']} ·
audit score (1🟢–3🔴): {kpi['avg_audit_score']}</p>
<table><tr><th>ID</th><th>File</th><th>State</th><th>Sec</th><th>Speakers</th><th>Actions</th><th>Risks</th></tr>
{row_html}</table></body></html>"""


def start_operator_view(settings: Any, store: MeetingStore, bus: Any) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            if self.path not in {"/", "/meetings"}:
                self.send_error(404)
                return
            if self.path == "/meetings":
                body = json.dumps(store.recent_meetings(30), ensure_ascii=False, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                body = _render(store).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((settings.operator_view_host, settings.operator_view_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="operator-view").start()
    bus.emit("operator_view.started", "meeting-runtime", port=settings.operator_view_port)
