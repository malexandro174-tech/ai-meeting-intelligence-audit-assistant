"""Audio extraction and normalization via ffmpeg (video -> audio, loudness-normalized mono MP3)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.events import MeetingPipelineError


def extract_audio(source: Path, target_dir: Path, *, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Extract/normalize the audio track; the visual stream is never uploaded."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (source.stem[:100] + "_audio.mp3")
    command = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vn",                       # drop video
        "-ac", "1", "-ar", "16000",  # mono 16 kHz speech profile
        "-b:a", "64k",
        str(target),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MeetingPipelineError("INVALID_MEDIA", f"ffmpeg unavailable: {type(exc).__name__}") from None
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise MeetingPipelineError("INVALID_MEDIA", (completed.stderr or "ffmpeg produced no audio")[:200])
    return target
