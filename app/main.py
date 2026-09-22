"""Application entry: wiring, resume-after-restart, TEST-bot polling loop.

Deployment shape: one container runs bot + pipeline (justified: single-tenant
v1, the polling bot is the only trigger, and all durable state lives in
PostgreSQL, so a combined process restarts safely and resumes pending work).
"""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

from .broker.gateway import build_gateway
from .config import Settings
from .core.events import EventBus
from .meetings.pipeline import MeetingPipeline
from .meetings.storage import MeetingStore
from .telegramui.bot import MeetingBot
from .telegramui.transport import TelegramApi


def _load_root_env() -> None:
    """magos_local transport: canonical root .env supplies scoped TEST-bot token (LOCAL_ENV delivery)."""
    if os.getenv("BROKER_TRANSPORT", "magos_local") != "magos_local":
        return
    root = Path(__file__).resolve().parents[3]
    env_file = root / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def build_app() -> tuple[MeetingPipeline, MeetingStore, EventBus, Settings]:
    settings = Settings()
    _load_root_env()
    bus = EventBus()
    gateway = build_gateway(settings.broker_transport, settings.requester_id, settings)
    store = MeetingStore(settings.database_url)
    store.migrate()
    pipeline = MeetingPipeline(settings, gateway, store, bus)
    return pipeline, store, bus, settings


def _send_adapter(gateway, settings):
    if settings.broker_transport == "magos_local":
        def send(chat_id: str, text: str) -> None:
            gateway.telegram().send_message(chat_id, text)
        return send
    telegram_env = gateway.telegram()

    def send_env(chat_id: str, text: str) -> None:
        telegram_env.send_message(chat_id, text)
    return send_env


def main() -> int:
    pipeline, store, bus, settings = build_app()
    stopped = threading.Event()

    def _stop(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    completed = pipeline.resume_pending()
    bus.emit("runtime.resumed", "meeting-runtime", completed=len(completed))

    api = TelegramApi(settings.telegram_bot_token_env)
    send = _send_adapter(pipeline.gateway, settings)

    # A finished deferral must reach the user without a re-upload.
    for row in completed:
        if row.get("source") == "telegram_test" and settings.telegram_allowed_chat_ids:
            try:
                result = {"analysis": store.load_analysis(row["meeting_id"]),
                          "transcript": store.load_transcript(row["meeting_id"])}
                if result["analysis"]:
                    from .telegramui.bot import _result_text
                    from .telegramui.chunking import split_message, telegram_safe
                    chat_id = settings.telegram_allowed_chat_ids[0]
                    for chunk in split_message(telegram_safe(_result_text(result))):
                        send(chat_id, chunk)
                    bus.emit("telegram.sent", row["meeting_id"], kind="result_resume")
            except Exception as exc:  # noqa: BLE001 — delivery must not crash the runtime
                bus.emit("telegram.resume_delivery_failed", row["meeting_id"], error=type(exc).__name__)

    def api_call(chat_id: str, text: str) -> None:
        send(chat_id, text)

    def download(file_id: str, target: Path) -> None:
        api.download_file(file_id, target)

    bot = MeetingBot(pipeline, bus, api_call=api_call,
                     allowed_chat_ids=settings.telegram_allowed_chat_ids,
                     download_media=download, poll_timeout=settings.telegram_poll_timeout)

    # Minimal HTTP health/operator surface (optional dark view served from the same process).
    if settings.operator_view_enabled:
        from .operatorview import start_operator_view
        start_operator_view(settings, store, bus)

    bus.emit("runtime.started", "meeting-runtime", transport=settings.broker_transport)
    while not stopped.is_set():
        try:
            updates = api.get_updates(bot.offset, settings.telegram_poll_timeout)
            for update in updates:
                bot.offset = max(bot.offset, int(update.get("update_id", 0)) + 1)
                bot._handle(update)
        except Exception as exc:  # noqa: BLE001 — polling survives provider hiccups with backoff
            bus.emit("bot.poll_error", "telegram-runtime", error=type(exc).__name__)
            stopped.wait(3)
    bus.emit("runtime.stopped", "meeting-runtime")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
