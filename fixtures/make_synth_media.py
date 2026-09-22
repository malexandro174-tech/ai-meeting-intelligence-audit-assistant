"""Synthetic demo media generator (safe for the public repository).

Creates: a two-tone MP3 "dialogue" (220 Hz then 330 Hz), a WAV, and a small
MP4 with an audio track (for the video -> audio extraction path). No real
voices, no personal data.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CONCAT = "[0:a][1:a]concat=n=2:v=0:a=1[a]"


def make(directory: Path, ffmpeg: str = "ffmpeg") -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    inputs = ["-f", "lavfi", "-i", "sine=frequency=220:duration=4",
              "-f", "lavfi", "-i", "sine=frequency=330:duration=4"]
    concat = ["-filter_complex", CONCAT, "-map", "[a]"]
    outputs: dict[str, Path] = {}
    jobs = [
        ("synthetic_dialogue.mp3", [*inputs, *concat, "-b:a", "64k"]),
        ("synthetic_dialogue.wav", [*inputs, *concat]),
        ("synthetic_clip.mp4", [*inputs, "-f", "lavfi", "-i", "color=c=0x1d2436:s=320x240:d=8",
                                *concat, "-map", "2:v", "-shortest", "-c:v", "libx264",
                                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-b:a", "64k"]),
    ]
    for name, args in jobs:
        target = directory / name
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, str(target)],
                       check=True, capture_output=True, timeout=120)
        outputs[name] = target
    return outputs


if __name__ == "__main__":
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    for name, path in make(target_dir).items():
        print(f"{name}: {path.stat().st_size} bytes")
