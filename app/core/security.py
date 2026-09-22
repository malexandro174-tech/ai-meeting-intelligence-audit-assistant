"""Input hardening: safe filenames, path-traversal protection, content sniffing."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

ALLOWED_MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".webm"}

# Magic-byte sniffers: extension alone is not trusted.
MAGIC_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("mp3_id3", b"ID3"),
    ("mp3_sync", b"\xff\xfb"),
    ("mp3_sync2", b"\xff\xf3"),
    ("mp3_sync3", b"\xff\xe3"),
    ("wav", b"RIFF"),
    ("mp4_ftyp", b"ftyp"),
    ("ogg", b"OggS"),
)


def safe_filename(original: str) -> str:
    """Normalize a user-supplied filename into a storage-safe, flat name."""
    name = unicodedata.normalize("NFKD", Path(original.replace("\\", "/")).name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name:
        name = "meeting_media"
    return name[:120]


def storage_path(directory: Path, filename: str) -> Path:
    """Resolve a storage path and refuse anything escaping the target directory."""
    resolved = (directory / safe_filename(filename)).resolve()
    if directory.resolve() not in resolved.parents:
        raise ValueError("PATH_TRAVERSAL_REJECTED")
    return resolved


def sniff_media_kind(header: bytes) -> str | None:
    if header[:3] == b"ID3" or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xe3"}:
        return "audio"
    if header[:4] == b"RIFF":
        return "audio"
    if b"ftyp" in header[:12]:   # MP4/M4A: 'ftyp' box at offset 4
        return "video" if header[8:12] not in {b"M4A "} else "audio"
    if header[:4] == b"OggS":
        return "audio"
    return None


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
