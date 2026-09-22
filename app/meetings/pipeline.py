"""Core pipeline: media intake -> validation -> audio -> transcription -> analysis -> reports.

States, idempotency, resume-after-restart, bounded retries and typed failures.
One correlation id (= meeting id) threads Telegram, media, AssemblyAI, DeepSeek,
PostgreSQL and every structured event.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from ..broker.gateway import BrokerGateway, BrokerGatewayError
from ..config import Settings
from ..core.events import EventBus, MeetingPipelineError, sanitize
from ..core.security import safe_filename, storage_path
from ..media.extractor import extract_audio
from ..media.validator import validate_media
from .analyzer import run_analysis
from .models import MeetingState
from .normalizer import normalize_transcript
from .profiles import AuditProfile
from .reports import write_artifacts
from .storage import MeetingStore


class MeetingPipeline:
    def __init__(self, settings: Settings, gateway: BrokerGateway, store: MeetingStore, bus: EventBus) -> None:
        self.settings, self.gateway, self.store, self.bus = settings, gateway, store, bus
        self.profile = AuditProfile.load(settings.profiles_dir, settings.audit_profile)

    # ------------------------------------------------------------- helpers

    def _event(self, name: str, meeting_id: str, **metadata: Any) -> None:
        self.bus.emit(name, meeting_id, **sanitize(metadata))
        try:
            self.store.record_event(meeting_id, name, metadata)
        except Exception:  # observability must never break the pipeline
            pass

    # -------------------------------------------------------------- intake

    def accept_media(self, source_path: Path, *, original_name: str, source: str,
                     file_unique_id: str | None) -> dict[str, Any]:
        """Idempotent intake; returns existing meeting for already-processed content."""
        raw = source_path.read_bytes()
        from ..core.security import content_hash
        digest = content_hash(raw)
        stored = storage_path(self.settings.media_dir, original_name)
        self.settings.media_dir.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(raw)
        meeting_id = f"mtg_{uuid.uuid4().hex[:12]}"
        row = self.store.create_meeting(meeting_id=meeting_id, correlation_id=meeting_id, source=source,
                                        filename=safe_filename(original_name), content_hash=digest,
                                        file_unique_id=file_unique_id)
        if row["duplicate"]:
            # A previously failed/retryable meeting may be re-submitted: route it to reprocessing.
            existing_state = str(row.get("state") or "")
            if existing_state in {MeetingState.FAILED.value, MeetingState.RETRY_PENDING.value}:
                row["reprocess"] = True
                self._event("meeting.retry_requested", row["meeting_id"], previous_state=existing_state)
            else:
                stored.unlink(missing_ok=True)   # known content: do not keep a second copy
                self._event("meeting.duplicate", row["meeting_id"], requested=meeting_id)
            return row
        self._event("meeting.received", meeting_id, filename=safe_filename(original_name),
                    source=source, size_bytes=len(raw))
        return row

    # ------------------------------------------------------------- process

    def process(self, meeting_id: str, *, resume: bool = False) -> dict[str, Any]:
        """Run the full lifecycle; safe to re-run (resume) after a restart."""
        started = time.monotonic()
        meeting = self.store.get_meeting(meeting_id)
        if not meeting:
            raise MeetingPipelineError("MEETING_NOT_FOUND", meeting_id)
        try:
            # Resume path: a stored transcript means polling already finished; the source
            # media may legitimately be gone (retention) and must not be required again.
            stored_transcript = self.store.load_transcript(meeting_id)
            if resume and stored_transcript is not None and not self.store.load_analysis(meeting_id):
                return self._finish_analysis(meeting_id, stored_transcript, started)
            media_path = self._locate_media(meeting)
            # ---- 1) validation + audio preparation
            self.store.set_state(meeting_id, MeetingState.MEDIA_PREPARATION)
            self._event("media.preparation", meeting_id, stage="validation")
            validated = validate_media(media_path, max_bytes=self.settings.max_media_bytes,
                                        max_seconds=self.settings.max_audio_seconds,
                                        ffprobe_bin=self.settings.ffprobe_bin)
            self._event("media.validated", meeting_id, kind=validated.kind, duration=round(validated.duration_seconds),
                        size_bytes=validated.size_bytes, format=validated.format_name)
            self.store.record_file(meeting_id, kind=validated.kind, size_bytes=validated.size_bytes,
                                   format_name=validated.format_name, stored_path=str(media_path))
            audio_path = media_path
            if validated.kind == "video":
                audio_path = extract_audio(media_path, self.settings.media_dir, ffmpeg_bin=self.settings.ffmpeg_bin)
                self._event("audio.extracted", meeting_id, target=audio_path.name)
            # ---- 2) transcription (skipped on resume with transcript already stored)
            transcript = None if resume else None
            if meeting["state"] == "TRANSCRIBED" or (resume and self.store.load_transcript(meeting_id)):
                transcript = self.store.load_transcript(meeting_id)
                self._event("transcription.reused", meeting_id)
            if transcript is None:
                self.store.set_state(meeting_id, MeetingState.TRANSCRIBING)
                self._event("transcription.created", meeting_id, provider="assemblyai", diarization=True)
                try:
                    provider = self.gateway.speech().transcribe(
                        audio_path, speaker_labels=True, language_detection=True)  # speakers_expected: run-scoped only
                except BrokerGatewayError as exc:
                    raise MeetingPipelineError(exc.code, exc.detail, retryable=exc.retryable) from None
                self.bus.count("assemblyai_calls")
                transcript = normalize_transcript(provider, meeting_id=meeting_id, source=meeting["correlation_id"],
                                                  filename=meeting["filename"],
                                                  duration_seconds=validated.duration_seconds)
                self.store.save_transcript(transcript, transcript_id=str(provider.get("transcript_id") or ""),
                                           media_meta={"audio_duration": provider.get("audio_duration"),
                                                       "assemblyai_calls": 1})
                self.store.set_state(meeting_id, MeetingState.TRANSCRIBED)
                self._event("transcription.completed", meeting_id, utterances=len(transcript.utterances),
                            speakers=transcript.speaker_count, chars=len(" ".join(u.text for u in transcript.utterances)))
                if transcript.speaker_count >= 2:
                    self._event("diarization.completed", meeting_id, speakers=transcript.speaker_count)
            # Transcript artifact is durable proof even when analysis is deferred.
            write_artifacts(self.settings.artifacts_dir / meeting_id, transcript, None)
            # ---- 3) analysis + 4) artifacts/completion
            return self._finish_analysis(meeting_id, transcript, started,
                                         media_paths=(media_path, audio_path))
        except MeetingPipelineError as exc:
            retryable = exc.retryable
            self.store.set_state(meeting_id, MeetingState.RETRY_PENDING if retryable else MeetingState.FAILED,
                                 error_code=exc.code, error_detail=exc.detail)
            self._event("meeting.failed", meeting_id, error_code=exc.code, detail=exc.detail[:120],
                        retryable=retryable)
            raise
        except Exception as exc:  # noqa: BLE001 — typed boundary for unexpected failures
            self.store.set_state(meeting_id, MeetingState.FAILED, error_code="UNEXPECTED",
                                 error_detail=f"{type(exc).__name__}: {exc}"[:200])
            self._event("meeting.failed", meeting_id, error_code="UNEXPECTED",
                        detail=f"{type(exc).__name__}"[:80])
            raise MeetingPipelineError("UNEXPECTED", f"{type(exc).__name__}") from exc

    # ------------------------------------------------------------ helpers

    def _finish_analysis(self, meeting_id: str, transcript, started: float,
                         media_paths: tuple[Path, ...] = ()) -> dict[str, Any]:
        """Analysis -> artifacts -> completion; shared by fresh runs and resume."""
        if self.store.load_analysis(meeting_id):
            self._event("analysis.reused", meeting_id)
        else:
            self.store.set_state(meeting_id, MeetingState.ANALYZING)
            self._event("analysis.started", meeting_id, profile=self.profile.profile_id)
            analysis, retries = run_analysis(self.gateway, transcript, self.profile, self.bus,
                                             max_retries=self.settings.analysis_max_retries)
            analysis.model_meta = {**analysis.model_meta,
                                   "processing_seconds": round(time.monotonic() - started, 1),
                                   "retry_count": retries}
            self.store.save_analysis(analysis)
            self._event("analysis.completed", meeting_id, actions=len(analysis.actions),
                        risks=len(analysis.risks), status=analysis.overall_status)
        analysis = self.store.load_analysis(meeting_id)
        paths = write_artifacts(self.settings.artifacts_dir / meeting_id, transcript, analysis)
        self.store.set_state(meeting_id, MeetingState.COMPLETED)
        self._event("report.created", meeting_id, artifacts=[p.name for p in paths.values()])
        from .n8n_event import send_completion_event
        send_completion_event(meeting_id, "COMPLETED", len(analysis.actions), len(analysis.risks),
                              meeting_id, self.bus)
        self.bus.count("processing_seconds", int(time.monotonic() - started))
        self._apply_retention(meeting_id, *media_paths)
        return {"meeting_id": meeting_id, "state": "COMPLETED", "speaker_count": transcript.speaker_count,
                "transcript": transcript, "analysis": analysis}

    def _locate_media(self, meeting: dict[str, Any]) -> Path:
        with self.store._conn() as conn:
            row = conn.execute(
                "SELECT stored_path FROM meeting_files WHERE meeting_id=%s ORDER BY file_id ASC LIMIT 1",
                (meeting["meeting_id"],)).fetchone()
        if row and Path(row[0]).is_file():
            return Path(row[0])
        candidate = self.settings.media_dir / meeting["filename"]
        if candidate.is_file():
            return candidate
        raise MeetingPipelineError("MEDIA_NOT_FOUND", meeting["filename"])

    def _apply_retention(self, meeting_id: str, *paths: Path) -> None:
        """Configurable retention: originals may be dropped after results are persisted."""
        analysis = self.store.load_analysis(meeting_id)
        if not analysis or self.settings.source_retention_seconds <= 0:
            return
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def resume_pending(self) -> list[dict[str, Any]]:
        """After restart: continue resumable meetings instead of re-uploading.

        Returns completed rows (meeting_id, source) so the runtime can deliver
        the finished result to the originating Telegram chat automatically.
        """
        completed: list[dict[str, Any]] = []
        for row in self.store.resumable_meetings():
            try:
                result = self.process(row["meeting_id"], resume=True)
            except MeetingPipelineError:
                continue
            if result.get("state") == "COMPLETED":
                meeting = self.store.get_meeting(row["meeting_id"]) or {}
                completed.append({"meeting_id": row["meeting_id"], "source": meeting.get("source") or ""})
        return completed
