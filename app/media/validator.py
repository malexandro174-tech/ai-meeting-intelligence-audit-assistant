"""Media intake validation: type, size, signature sniffing, duration sanity."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.security import ALLOWED_MEDIA_EXTENSIONS, content_hash, sniff_media_kind
from ..core.events import MeetingPipelineError

AUDIO_TARGET_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus"}


@dataclass(frozen=True)
class ValidatedMedia:
    path: Path
    kind: str            # audio | video
    size_bytes: int
    content_hash: str
    duration_seconds: float
    format_name: str


def _probe(ffprobe_bin: str, path: Path) -> tuple[float, str]:
    import json
    completed = subprocess.run(
        [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0:
        raise MeetingPipelineError("INVALID_MEDIA", "ffprobe failed to decode the file")
    try:
        info = json.loads(completed.stdout or "{}").get("format") or {}
        return float(info["duration"]), str(info.get("format_name") or "unknown")
    except (KeyError, TypeError, ValueError) as exc:
        raise MeetingPipelineError("INVALID_MEDIA", f"unreadable probe output ({type(exc).__name__})") from None


def validate_media(path: Path, *, max_bytes: int, max_seconds: int, ffprobe_bin: str = "ffprobe") -> ValidatedMedia:
    """Full intake gate: existence, size, extension, magic bytes, decodability, duration."""
    if not path.is_file() or path.stat().st_size == 0:
        raise MeetingPipelineError("INVALID_MEDIA", "file missing or empty")
    size = path.stat().st_size
    if size > max_bytes:
        raise MeetingPipelineError("MEDIA_TOO_LARGE", f"{size} bytes exceeds limit {max_bytes}")
    if path.suffix.casefold() not in {ext.casefold() for ext in ALLOWED_MEDIA_EXTENSIONS}:
        raise MeetingPipelineError("UNSUPPORTED_MEDIA", f"extension {path.suffix} not allowed")
    header = path.open("rb").read(16)
    kind = sniff_media_kind(header)
    if kind is None:
        raise MeetingPipelineError("INVALID_MEDIA", "file signature does not match any supported media container")
    duration, format_name = _probe(ffprobe_bin, path)
    if duration <= 1 or duration > max_seconds:
        raise MeetingPipelineError("INVALID_MEDIA", f"duration {duration:.0f}s outside sane bounds")
    return ValidatedMedia(path=path, kind=kind, size_bytes=size, content_hash=content_hash(path.read_bytes()),
                          duration_seconds=duration, format_name=format_name)
