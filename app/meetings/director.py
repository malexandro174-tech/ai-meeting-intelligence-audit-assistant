"""Director-ready contracts: upward report, KPI snapshot, escalations, commercial summary.

The real Directors Framework is NOT connected here; these are the stable
contracts it will consume (see contracts/*.schema.json).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import MeetingAnalysis
from .storage import MeetingStore

FUTURE_TARGETS = ("CommercialDirector", "FinancialDirector", "EngineeringDirector", "HumanOwner")


@dataclass
class UpwardReport:
    agent_id: str
    period: str
    meetings_processed: int
    total_minutes: float
    failed_meetings: int
    open_actions: int
    critical_risks: int
    important_decisions: int
    unresolved_questions: int
    recommendations: list[str] = field(default_factory=list)

    def to_contract(self) -> dict[str, Any]:
        return {"contract": "MeetingUpwardReport", "version": "1.0",
                "agent_id": self.agent_id, "period": self.period,
                "meetings_processed": self.meetings_processed, "total_minutes": self.total_minutes,
                "failed_meetings": self.failed_meetings, "open_actions": self.open_actions,
                "critical_risks": self.critical_risks, "important_decisions": self.important_decisions,
                "unresolved_questions": self.unresolved_questions, "recommendations": self.recommendations}


@dataclass
class Escalation:
    meeting_id: str
    target_role: str
    severity: str
    reason: str
    evidence: str
    recommended_action: str

    def to_contract(self) -> dict[str, Any]:
        return {"contract": "MeetingEscalation", "version": "1.0", **self.__dict__}


def build_upward_report(store: MeetingStore, agent_id: str) -> UpwardReport:
    kpi = store.kpi_snapshot()
    with store._conn() as conn:
        failed = conn.execute("SELECT count(*) FROM meetings WHERE state IN ('FAILED')").fetchone()[0]
        decisions = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
        questions = conn.execute("SELECT count(*) FROM action_items WHERE status='OPEN'").fetchone()[0]
    return UpwardReport(
        agent_id=agent_id, period=datetime.now(timezone.utc).strftime("%Y-%m"),
        meetings_processed=kpi["meetings_count"], total_minutes=kpi["total_duration_minutes"],
        failed_meetings=failed, open_actions=kpi["open_actions_count"],
        critical_risks=kpi["critical_risks_count"], important_decisions=decisions,
        unresolved_questions=questions,
        recommendations=["Закрыть открытые действия по последним встречам",
                         "Проверить HIGH-риски с клиентами"])


def commercial_summary(store: MeetingStore, analyses: dict[str, MeetingAnalysis] | None = None) -> dict[str, Any]:
    """Compatibility surface for a future Commercial Director consumer."""
    return {"contract": "CommercialSummary", "version": "1.0",
            "recent_meetings": store.recent_meetings(limit=10),
            "open_actions": store.open_actions(),
            "critical_risks": store.critical_risks()}


def detect_finance_events(analysis: MeetingAnalysis, markers: dict[str, tuple[str, ...]]) -> list[str]:
    """Emit future FinancialDirector events based on audit criteria/risks text."""
    haystack = " ".join([c.value or "" for c in analysis.audit_results.criteria] +
                        [r.description for r in analysis.risks]).lower()
    events = []
    for event, keys in markers.items():
        if any(k in haystack for k in keys):
            events.append(event)
    return events
