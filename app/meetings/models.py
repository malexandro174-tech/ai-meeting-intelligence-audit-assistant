"""Domain models: structured transcript, audit analysis, extracted meeting entities."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MeetingState(str, Enum):
    RECEIVED = "RECEIVED"
    MEDIA_PREPARATION = "MEDIA_PREPARATION"
    TRANSCRIBING = "TRANSCRIBING"
    TRANSCRIBED = "TRANSCRIBED"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"


class Utterance(BaseModel):
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class Transcript(BaseModel):
    meeting_id: str
    source: str
    filename: str
    duration_seconds: float
    language: str | None = None
    speaker_count: int
    created_at: str = Field(default_factory=utc_now_iso)
    utterances: list[Utterance] = []

    def render_speaker_turns(self, limit: int | None = None) -> str:
        """Normalized 'Speaker A: …' view with timestamps; provider payload never leaks downstream."""
        rows = self.utterances if limit is None else self.utterances[:limit]
        lines = []
        for u in rows:
            stamp = f"[{u.start_ms//1000:02d}:{(u.start_ms % 1000)//10:02d}]"
            lines.append(f"{stamp} Speaker {u.speaker}: {u.text}")
        return "\n".join(lines)


class ActionItem(BaseModel):
    text: str
    owner: str = "UNKNOWN"
    deadline: str = "NOT_SPECIFIED"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    source_utterance: str | None = None
    status: Literal["OPEN", "IN_PROGRESS", "DONE"] = "OPEN"


class Decision(BaseModel):
    text: str
    participants: list[str] = []
    evidence: str | None = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)


class Commitment(BaseModel):
    speaker: str
    commitment: str
    deadline: str = "NOT_SPECIFIED"
    evidence: str | None = None


class OpenQuestion(BaseModel):
    question: str
    raised_by: str = "UNKNOWN"
    assigned_to: str = "NOT_SPECIFIED"
    status: Literal["OPEN", "ANSWERED"] = "OPEN"


class Risk(BaseModel):
    category: Literal["COMMERCIAL", "TECHNICAL", "FINANCIAL", "LEGAL", "DELIVERY", "SCOPE", "COMMUNICATION", "OTHER"] = "OTHER"
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    description: str
    evidence: str | None = None
    recommended_action: str | None = None


class CriterionResult(BaseModel):
    criterion: str
    value: str          # concrete finding, NOT_DISCLOSED / NOT_SPECIFIED / UNKNOWN when absent
    evidence: str | None = None


class AuditResult(BaseModel):
    profile_id: str
    criteria: list[CriterionResult] = []
    overall_status: Literal["GREEN", "YELLOW", "RED"] = "YELLOW"


class MeetingAnalysis(BaseModel):
    meeting_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    meeting_summary: str
    audit_results: AuditResult
    actions: list[ActionItem] = []
    decisions: list[Decision] = []
    commitments: list[Commitment] = []
    open_questions: list[OpenQuestion] = []
    risks: list[Risk] = []
    recommendations: list[str] = []
    overall_status: Literal["GREEN", "YELLOW", "RED"] = "YELLOW"
    model_meta: dict[str, Any] = Field(default_factory=dict)


class CostSnapshot(BaseModel):
    audio_duration_seconds: float = 0.0
    assemblyai_calls: int = 0
    deepseek_calls: int = 0
    processing_seconds: float = 0.0
    retry_count: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
