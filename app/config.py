"""Runtime configuration. Secrets arrive only via environment (canonical LOCAL_ENV delivery) and are never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Broker transport: "magos_local" (inside Mag_OS runtime) or "container_env" (Docker/VPS scoped env delivery).
    broker_transport: str = field(default_factory=lambda: os.getenv("BROKER_TRANSPORT", "magos_local"))
    requester_id: str = field(default_factory=lambda: os.getenv("MEETING_REQUESTER_ID", "ai-meeting-intelligence-assistant"))

    # Storage.
    database_url: str = field(default_factory=lambda: os.getenv(
        "MEETING_DATABASE_URL", "postgresql://meeting:meeting@127.0.0.1:5433/meeting_intelligence"))

    # Media.
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("MEETING_DATA_DIR", PROJECT_ROOT / "data")))
    ffmpeg_bin: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe_bin: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))
    max_media_bytes: int = field(default_factory=lambda: _env_int("MEETING_MAX_MEDIA_BYTES", 45 * 1024 * 1024))
    max_audio_seconds: int = field(default_factory=lambda: _env_int("MEETING_MAX_AUDIO_SECONDS", 4 * 3600))

    # Telegram (TEST profile; token arrives via env only, chat targets come from the Broker allowlist).
    telegram_bot_token_env: str = "TELEGRAM_COURSE_TEST_BOT_TOKEN"
    telegram_allowed_chat_ids: tuple[str, ...] = field(default_factory=lambda: tuple(
        item.strip() for item in os.getenv("MEETING_TELEGRAM_ALLOWED_CHAT_IDS", "-5487263463").split(",") if item.strip()))
    telegram_poll_timeout: int = field(default_factory=lambda: _env_int("TELEGRAM_POLL_TIMEOUT", 25))

    # AssemblyAI pipeline.
    transcription_timeout_seconds: int = field(default_factory=lambda: _env_int("TRANSCRIPTION_TIMEOUT_SECONDS", 900))
    transcription_poll_interval: int = field(default_factory=lambda: _env_int("TRANSCRIPTION_POLL_INTERVAL", 5))

    # Analysis.
    audit_profile: str = field(default_factory=lambda: os.getenv("MEETING_AUDIT_PROFILE", "commercial_meeting_v1"))
    profiles_dir: Path = field(default_factory=lambda: Path(os.getenv("MEETING_PROFILES_DIR", PROJECT_ROOT / "profiles")))
    analysis_max_retries: int = field(default_factory=lambda: _env_int("ANALYSIS_MAX_RETRIES", 2))

    # Retention (seconds; 0 = keep originals).
    source_retention_seconds: int = field(default_factory=lambda: _env_int("MEETING_SOURCE_RETENTION_SECONDS", 3600))

    # Optional operator web view.
    operator_view_enabled: bool = field(default_factory=lambda: os.getenv("OPERATOR_VIEW_ENABLED", "0") == "1")
    operator_view_host: str = field(default_factory=lambda: os.getenv("OPERATOR_VIEW_HOST", "0.0.0.0"))
    operator_view_port: int = field(default_factory=lambda: _env_int("OPERATOR_VIEW_PORT", 8080))

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"
