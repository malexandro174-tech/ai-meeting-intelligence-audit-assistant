"""Transcript normalization: provider payload -> domain Transcript (adapter isolation)."""
from __future__ import annotations

from typing import Any

from .models import Transcript, Utterance


def normalize_transcript(provider_payload: dict[str, Any], *, meeting_id: str, source: str,
                         filename: str, duration_seconds: float) -> Transcript:
    """Map AssemblyAI-shaped payload into the domain model; unknown fields are dropped here."""
    utterances = [
        Utterance(
            speaker=str(u.get("speaker") or "?"),
            start_ms=int(u.get("start") or 0),
            end_ms=int(u.get("end") or 0),
            text=str(u.get("text") or ""),
        )
        for u in (provider_payload.get("utterances") or []) if isinstance(u, dict)
    ]
    # Fallback: no diarization -> single speaker block from plain text.
    if not utterances:
        text = str(provider_payload.get("text") or "").strip()
        if text:
            total_ms = int(duration_seconds * 1000)
            utterances = [Utterance(speaker="A", start_ms=0, end_ms=total_ms, text=text)]
    speaker_count = len({u.speaker for u in utterances})
    return Transcript(
        meeting_id=meeting_id, source=source, filename=filename,
        duration_seconds=float(provider_payload.get("audio_duration") or duration_seconds),
        language=(provider_payload.get("language_code") or None),
        speaker_count=speaker_count, utterances=utterances,
    )
