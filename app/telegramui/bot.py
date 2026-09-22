"""Telegram TEST interface: polling owner of the registered course test bot.

UX: /start /help /status + media intake. Statuses are sent sparingly
(получен -> готово), never per-stage spam. The bot token arrives via the
broker-scoped environment and never appears in logs or storage.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.events import EventBus, MeetingPipelineError
from ..meetings.models import MeetingState
from ..meetings.pipeline import MeetingPipeline
from .chunking import split_message, telegram_safe

HELP_TEXT = (
    "🤖 AI Meeting Intelligence & Audit Assistant\n\n"
    "Пришли аудио или видео файл встречи (MP3/WAV/M4A/MP4, до ~45 МБ) — я:\n"
    "1) извлеку аудио и затранскрибирую (AssemblyAI);\n"
    "2) разделю участников (Speaker A/B);\n"
    "3) проведу аудит встречи (DeepSeek);\n"
    "4) сохраню отчёт в PostgreSQL и верну резюме.\n\n"
    "Команды: /start, /help, /status\n"
    "Если файл слишком большой — пришли MP3 или сжатое аудио."
)
START_TEXT = (
    "Привет! Пришли аудио/видео встречи — получишь структурированный отчёт: "
    "решения, обязательства, действия, риски и аудит разговора. /help — подробности."
)


def _result_text(result: dict[str, Any]) -> str:
    analysis = result.get("analysis")
    transcript = result.get("transcript")
    if not analysis:
        return "✅ Транскрипция готова, аудит временно недоступен — отчёт будет дополнен позже."
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(analysis.overall_status, "🟡")
    lines = [f"✅ Встреча обработана {icon}", "", f"📝 {analysis.meeting_summary[:400]}", ""]
    if analysis.actions:
        lines.append("🔶 Действия:")
        lines.extend(f"• {a.text} — {a.owner}" + (f" (до {a.deadline})" if a.deadline != "NOT_SPECIFIED" else "")
                     for a in analysis.actions[:7])
        lines.append("")
    if analysis.risks:
        lines.append("⚠️ Риски:")
        lines.extend(f"• {r.severity}: {r.description[:120]}" for r in analysis.risks[:5])
        lines.append("")
    if analysis.open_questions:
        lines.append("❓ Открытые вопросы:")
        lines.extend(f"• {q.question[:120]}" for q in analysis.open_questions[:4])
    speakers = getattr(transcript, "speaker_count", None)
    if speakers:
        lines.append("")
        lines.append(f"🎙 Спикеров: {speakers}; реплик: {len(getattr(transcript, 'utterances', []) or [])}")
    return "\n".join(lines)


class MeetingBot:
    def __init__(self, pipeline: MeetingPipeline, bus: EventBus, *, api_call, allowed_chat_ids: tuple[str, ...],
                 download_media, poll_timeout: int = 25) -> None:
        self.pipeline, self.bus, self.api_call = pipeline, bus, api_call
        self.allowed_chat_ids, self.download_media = allowed_chat_ids, download_media
        self.poll_timeout = poll_timeout
        self.offset = 0
        self.started_at = time.time()

    # ------------------------------------------------------------- sending

    def send(self, chat_id: str, text: str) -> None:
        for chunk in split_message(telegram_safe(text)):
            self.api_call(chat_id, chunk)

    # ------------------------------------------------------------- updates

    def poll_once(self) -> int:
        updates = self._get_updates()
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            self._handle(update)
        return len(updates)

    def _get_updates(self) -> list[dict[str, Any]]:
        raise NotImplementedError  # provided by transports below

    def _handle(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id") or "")
        if not chat_id or chat_id not in self.allowed_chat_ids:
            return  # TEST allowlist only
        text = str(message.get("text") or "").strip()
        if text.startswith("/start"):
            self.send(chat_id, START_TEXT)
            return
        if text.startswith("/help"):
            self.send(chat_id, HELP_TEXT)
            return
        if text.startswith("/status"):
            self.send(chat_id, self._status_text())
            return
        document = message.get("document") or (message.get("audio") or message.get("video") or None)
        if document:
            self._handle_media(chat_id, document)
        elif text:
            self.send(chat_id, "Пришли файл аудио/видео встречи — или /help для инструкции.")

    def _handle_media(self, chat_id: str, document: dict[str, Any]) -> None:
        file_id = str(document.get("file_id") or "")
        file_name = str(document.get("file_name") or document.get("file_unique_id") or "meeting_media")
        file_size = int(document.get("file_size") or 0)
        if not file_id:
            return
        if file_size and file_size > self.pipeline.settings.max_media_bytes:
            self.send(chat_id, "⚠️ Файл слишком большой. Пришли MP3/сжатое аудио до 45 МБ или более короткую запись.")
            return
        try:
            self.send(chat_id, "📥 Файл получен. Готовлю аудио, транскрибирую и анализирую — сообщу, когда будет готово.")
            with tempfile.TemporaryDirectory(prefix="meeting-intake-") as tmp:
                raw_path = Path(tmp) / file_name
                self.download_media(file_id, raw_path)
                intake = self.pipeline.accept_media(raw_path, original_name=file_name,
                                                    source="telegram_test", file_unique_id=str(document.get("file_unique_id") or ""))
            if intake.get("reprocess"):
                result = self.pipeline.process(intake["meeting_id"])
                self.send(chat_id, _result_text(result))
                self.bus.emit("telegram.sent", intake["meeting_id"], kind="result_retry")
                return
            if intake.get("duplicate"):
                self.send(chat_id, f"♻️ Этот файл уже обрабатывался (встреча {intake['meeting_id']}). Повторная обработка не требуется.")
                return
            result = self.pipeline.process(intake["meeting_id"])
            self.send(chat_id, _result_text(result))
            self.bus.emit("telegram.sent", intake["meeting_id"], kind="result")
        except MeetingPipelineError as exc:
            hint = {
                "MEDIA_TOO_LARGE": "Файл слишком большой — пришли MP3 до 45 МБ.",
                "UNSUPPORTED_MEDIA": "Формат не поддерживается — пришли MP3/WAV/M4A/MP4.",
                "INVALID_MEDIA": "Не удалось декодировать файл — проверь, что запись воспроизводится.",
                "CREDENTIAL_UNAVAILABLE": "Сервис анализа временно недоступен (учётные данные не доставлены). Транскрипция может быть готова — попробуй позже.",
            }.get(exc.code, "Произошла ошибка обработки. Попробуй ещё раз позже.")
            self.send(chat_id, f"❌ {hint}")
            self.bus.emit("meeting.failed", "telegram", error_code=exc.code)

    def _status_text(self) -> str:
        uptime = int(time.time() - self.started_at)
        rows = self.pipeline.store.recent_meetings(limit=5)
        lines = [f"🟢 Assistant активен (uptime {uptime // 60} мин)", "", "Последние встречи:"]
        if not rows:
            lines.append("— пока пусто")
        for row in rows:
            icon = {"COMPLETED": "✅", "FAILED": "❌", "RETRY_PENDING": "🔁"}.get(row["state"], "⏳")
            lines.append(f"{icon} {row['filename'][:40]} · {row['state']}")
        return "\n".join(lines)

    def run_forever(self) -> None:
        self.bus.emit("bot.started", "telegram-runtime", mode="long_poll")
        while True:
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 — polling must survive provider hiccups
                self.bus.emit("bot.poll_error", "telegram-runtime", error=type(exc).__name__)
                time.sleep(3)
