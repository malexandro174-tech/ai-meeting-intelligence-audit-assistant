"""PostgreSQL persistence: schema, meetings lifecycle, idempotency, resume, processing events."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from .models import MeetingAnalysis, MeetingState, Transcript

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id      TEXT PRIMARY KEY,
    correlation_id  TEXT NOT NULL,
    source          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_unique_id  TEXT,
    content_hash    TEXT NOT NULL,
    state           TEXT NOT NULL,
    error_code      TEXT,
    error_detail    TEXT,
    duration_seconds DOUBLE PRECISION,
    speaker_count   INTEGER,
    transcript_id   TEXT,
    audit_profile   TEXT,
    overall_status  TEXT,
    audio_duration_seconds DOUBLE PRECISION,
    assemblyai_calls INTEGER DEFAULT 0,
    deepseek_calls  INTEGER DEFAULT 0,
    processing_seconds DOUBLE PRECISION DEFAULT 0,
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS meetings_content_hash_key ON meetings (content_hash);
CREATE TABLE IF NOT EXISTS meeting_files (
    file_id    BIGSERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    format     TEXT,
    stored_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS transcripts (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    language   TEXT,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS utterances (
    utterance_id BIGSERIAL PRIMARY KEY,
    meeting_id   TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    speaker      TEXT NOT NULL,
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    text         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_analyses (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    payload    JSONB NOT NULL,
    overall_status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS action_items (
    item_id   BIGSERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    owner     TEXT,
    deadline  TEXT,
    status    TEXT NOT NULL DEFAULT 'OPEN',
    confidence DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    participants TEXT[],
    evidence   TEXT,
    confidence DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS risks (
    risk_id   BIGSERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    category  TEXT NOT NULL,
    severity  TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence  TEXT,
    recommended_action TEXT
);
CREATE TABLE IF NOT EXISTS processing_events (
    event_id  BIGSERIAL PRIMARY KEY,
    meeting_id TEXT,
    event     TEXT NOT NULL,
    metadata  JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class MeetingStore:
    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 4) -> None:
        self.pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size,
                                   open=True, kwargs={"autocommit": True})

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def _conn(self):
        with self.pool.connection() as conn:
            yield conn

    def migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(SCHEMA_SQL)

    def create_meeting(self, *, meeting_id: str, correlation_id: str, source: str, filename: str,
                       content_hash: str, file_unique_id: str | None) -> dict[str, Any] | None:
        """Idempotent intake: same content hash returns the existing meeting row."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT meeting_id, state FROM meetings WHERE content_hash = %s", (content_hash,)).fetchone()
            if row:
                return {"meeting_id": row[0], "state": row[1], "duplicate": True}
            conn.execute(
                """INSERT INTO meetings (meeting_id, correlation_id, source, filename, file_unique_id,
                   content_hash, state) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (meeting_id, correlation_id, source, filename, file_unique_id, content_hash,
                 MeetingState.RECEIVED.value))
        return {"meeting_id": meeting_id, "state": MeetingState.RECEIVED.value, "duplicate": False}

    def set_state(self, meeting_id: str, state: MeetingState, *, error_code: str | None = None,
                  error_detail: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE meetings SET state=%s, error_code=COALESCE(%s, error_code),
                   error_detail=COALESCE(%s, error_detail), updated_at=now() WHERE meeting_id=%s""",
                (state.value, error_code, error_detail, meeting_id))

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT meeting_id, correlation_id, state, transcript_id, filename, duration_seconds, "
                "speaker_count, overall_status, error_code, content_hash FROM meetings WHERE meeting_id=%s",
                (meeting_id,)).fetchone()
            if not row:
                return None
            keys = ("meeting_id", "correlation_id", "state", "transcript_id", "filename",
                    "duration_seconds", "speaker_count", "overall_status", "error_code", "content_hash")
            return dict(zip(keys, row))

    def find_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT meeting_id FROM meetings WHERE content_hash=%s", (content_hash,)).fetchone()
            return {"meeting_id": row[0]} if row else None

    def record_file(self, meeting_id: str, *, kind: str, size_bytes: int, format_name: str, stored_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meeting_files (meeting_id, kind, size_bytes, format, stored_path) VALUES (%s,%s,%s,%s,%s)",
                (meeting_id, kind, size_bytes, format_name, stored_path))

    def save_transcript(self, transcript: Transcript, *, transcript_id: str | None = None,
                        media_meta: dict[str, Any] | None = None) -> None:
        payload = transcript.model_dump()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO transcripts (meeting_id, language, payload) VALUES (%s,%s,%s) "
                "ON CONFLICT (meeting_id) DO UPDATE SET payload=EXCLUDED.payload, language=EXCLUDED.language",
                (transcript.meeting_id, transcript.language, json.dumps(payload, ensure_ascii=False)))
            conn.execute("DELETE FROM utterances WHERE meeting_id=%s", (transcript.meeting_id,))
            for seq, u in enumerate(transcript.utterances):
                conn.execute(
                    "INSERT INTO utterances (meeting_id, seq, speaker, start_ms, end_ms, text) VALUES (%s,%s,%s,%s,%s,%s)",
                    (transcript.meeting_id, seq, u.speaker, u.start_ms, u.end_ms, u.text))
            meta = media_meta or {}
            conn.execute(
                """UPDATE meetings SET transcript_id=COALESCE(%s, transcript_id),
                   duration_seconds=COALESCE(%s, duration_seconds), speaker_count=%s,
                   audio_duration_seconds=%s, assemblyai_calls=assemblyai_calls+%s, updated_at=now()
                   WHERE meeting_id=%s""",
                (transcript_id, meta.get("audio_duration"), transcript.speaker_count,
                 meta.get("audio_duration"), int(meta.get("assemblyai_calls") or 1), transcript.meeting_id))

    def load_transcript(self, meeting_id: str) -> Transcript | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM transcripts WHERE meeting_id=%s", (meeting_id,)).fetchone()
        if not row:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return Transcript.model_validate(payload)

    def save_analysis(self, analysis: MeetingAnalysis) -> None:
        payload = analysis.model_dump()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO meeting_analyses (meeting_id, payload, overall_status) VALUES (%s,%s,%s) "
                "ON CONFLICT (meeting_id) DO UPDATE SET payload=EXCLUDED.payload, overall_status=EXCLUDED.overall_status",
                (analysis.meeting_id, json.dumps(payload, ensure_ascii=False), analysis.overall_status))
            for table in ("action_items", "decisions", "risks"):
                conn.execute(f"DELETE FROM {table} WHERE meeting_id=%s", (analysis.meeting_id,))
            for a in analysis.actions:
                conn.execute(
                    "INSERT INTO action_items (meeting_id, text, owner, deadline, status, confidence) VALUES (%s,%s,%s,%s,%s,%s)",
                    (analysis.meeting_id, a.text, a.owner, a.deadline, a.status, a.confidence))
            for d in analysis.decisions:
                conn.execute(
                    "INSERT INTO decisions (meeting_id, text, participants, evidence, confidence) VALUES (%s,%s,%s,%s,%s)",
                    (analysis.meeting_id, d.text, d.participants, d.evidence, d.confidence))
            for r in analysis.risks:
                conn.execute(
                    "INSERT INTO risks (meeting_id, category, severity, description, evidence, recommended_action) VALUES (%s,%s,%s,%s,%s,%s)",
                    (analysis.meeting_id, r.category, r.severity, r.description, r.evidence, r.recommended_action))
            conn.execute(
                """UPDATE meetings SET overall_status=%s, deepseek_calls=deepseek_calls+1,
                   processing_seconds=%s, retry_count=%s, updated_at=now() WHERE meeting_id=%s""",
                (analysis.overall_status, analysis.model_meta.get("processing_seconds") or 0,
                 int(analysis.model_meta.get("retry_count") or 0), analysis.meeting_id))

    def load_analysis(self, meeting_id: str) -> MeetingAnalysis | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM meeting_analyses WHERE meeting_id=%s", (meeting_id,)).fetchone()
        if not row:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return MeetingAnalysis.model_validate(payload)

    def record_event(self, meeting_id: str | None, event: str, metadata: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute("INSERT INTO processing_events (meeting_id, event, metadata) VALUES (%s,%s,%s)",
                         (meeting_id, event, json.dumps(metadata or {}, ensure_ascii=False)))

    def resumable_meetings(self) -> list[dict[str, Any]]:
        """Rows whose state allows resume after restart (polling continuation, re-analysis)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT meeting_id, state, transcript_id FROM meetings WHERE state IN (%s,%s,%s,%s)",
                (MeetingState.MEDIA_PREPARATION.value, MeetingState.TRANSCRIBING.value,
                 MeetingState.TRANSCRIBED.value, MeetingState.RETRY_PENDING.value)).fetchall()
        return [{"meeting_id": r[0], "state": r[1], "transcript_id": r[2]} for r in rows]

    def recent_meetings(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT m.meeting_id, m.filename, m.state, m.duration_seconds, m.speaker_count,
                          m.overall_status, m.created_at,
                          (SELECT count(*) FROM action_items a WHERE a.meeting_id=m.meeting_id) AS actions,
                          (SELECT count(*) FROM risks r WHERE r.meeting_id=m.meeting_id) AS risks
                   FROM meetings m ORDER BY m.created_at DESC LIMIT %s""", (limit,)).fetchall()
        keys = ("meeting_id", "filename", "state", "duration_seconds", "speaker_count",
                "overall_status", "created_at", "actions", "risks")
        return [dict(zip(keys, row)) for row in rows]

    def open_actions(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.text, a.owner, a.deadline, m.meeting_id FROM action_items a
                   JOIN meetings m ON m.meeting_id=a.meeting_id
                   WHERE a.status='OPEN' ORDER BY m.created_at DESC LIMIT 100""").fetchall()
        return [{"text": r[0], "owner": r[1], "deadline": r[2], "meeting_id": r[3]} for r in rows]

    def critical_risks(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r.category, r.severity, r.description, m.meeting_id FROM risks r
                   JOIN meetings m ON m.meeting_id=r.meeting_id
                   WHERE r.severity IN ('HIGH','CRITICAL') ORDER BY r.severity DESC LIMIT 100""").fetchall()
        return [{"category": r[0], "severity": r[1], "description": r[2], "meeting_id": r[3]} for r in rows]

    def kpi_snapshot(self) -> dict[str, Any]:
        status_rank = {"GREEN": 1.0, "YELLOW": 2.0, "RED": 3.0}
        with self._conn() as conn:
            row = conn.execute(
                """SELECT count(*),
                          COALESCE(sum(audio_duration_seconds),0)/60.0,
                          COALESCE(avg(processing_seconds),0),
                          COALESCE(avg(CASE WHEN transcript_id IS NOT NULL THEN 1.0 ELSE 0 END),1.0),
                          COALESCE(avg(CASE WHEN overall_status IS NOT NULL THEN 1.0 ELSE 0 END),0.0),
                          (SELECT count(*) FROM action_items WHERE status='OPEN'),
                          (SELECT count(*) FROM risks WHERE severity IN ('HIGH','CRITICAL'))
                   FROM meetings""").fetchone()
            statuses = [r[0] for r in conn.execute(
                "SELECT overall_status FROM meeting_analyses WHERE overall_status IN ('GREEN','YELLOW','RED')").fetchall()]
        avg_audit = round(sum(status_rank[s] for s in statuses) / len(statuses), 2) if statuses else 0.0
        return {"meetings_count": row[0], "total_duration_minutes": round(float(row[1]), 1),
                "avg_processing_time": round(float(row[2]), 1),
                "transcription_success_rate": round(float(row[3]), 2),
                "analysis_success_rate": round(float(row[4]), 2),
                "open_actions_count": row[5], "critical_risks_count": row[6],
                "avg_audit_score": avg_audit}
