"""AuditProfile architecture: prompt profiles stored as YAML, separated from runtime code."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..core.events import MeetingPipelineError

REQUIRED_SECTIONS = ("role", "task_objective", "criteria", "audit_process",
                     "core_instructions", "constraints", "output_schema")


@dataclass(frozen=True)
class AuditProfile:
    profile_id: str
    language: str
    role: str
    task_objective: str
    criteria: list[dict]
    audit_process: list[str]
    core_instructions: list[str]
    constraints: list[str]
    output_schema: dict
    finance_events: list[str] = field(default_factory=lambda: [
        "BUDGET_MENTIONED", "PRICE_DISCUSSION", "REFUND_REQUEST", "PAYMENT_RISK", "REVENUE_OPPORTUNITY"])
    engineering_events: list[str] = field(default_factory=lambda: [
        "TECHNICAL_BLOCKER", "INTEGRATION_REQUIRED", "SECURITY_CONCERN", "INFRASTRUCTURE_DEPENDENCY"])

    @classmethod
    def load(cls, profiles_dir: Path, profile_id: str) -> "AuditProfile":
        path = profiles_dir / f"{profile_id}.yaml"
        if not path.is_file():
            raise MeetingPipelineError("PROFILE_NOT_FOUND", str(path))
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        missing = [s for s in REQUIRED_SECTIONS if s not in data]
        if missing:
            raise MeetingPipelineError("PROFILE_INVALID", f"missing sections: {','.join(missing)}")
        return cls(profile_id=profile_id, language=str(data.get("language", "ru")),
                   role=str(data["role"]), task_objective=str(data["task_objective"]),
                   criteria=list(data["criteria"]), audit_process=[str(s) for s in data["audit_process"]],
                   core_instructions=[str(s) for s in data["core_instructions"]],
                   constraints=[str(s) for s in data["constraints"]], output_schema=dict(data["output_schema"]),
                   finance_events=[str(s) for s in data.get("finance_events", [])],
                   engineering_events=[str(s) for s in data.get("engineering_events", [])])

    def system_prompt(self) -> str:
        """System instructions ONLY; meeting data is delivered separately as untrusted input."""
        sections = [
            f"РОЛЬ: {self.role}",
            f"ЗАДАЧА: {self.task_objective}",
            "ПРОЦЕСС АУДИТА:\n- " + "\n- ".join(self.audit_process),
            "КЛЮЧЕВЫЕ ИНСТРУКЦИИ:\n- " + "\n- ".join(self.core_instructions),
            "ОГРАНИЧЕНИЯ:\n- " + "\n- ".join(self.constraints),
            "КРИТЕРИИ АУДИТА (для каждого: значение из встречи ИЛИ NOT_DISCLOSED/NOT_SPECIFIED/UNKNOWN + короткая цитата-доказательство):",
        ]
        sections += [f"- {c.get('id')}: {c.get('description')}" for c in self.criteria]
        sections.append("Отвечай ТОЛЬКО валидным JSON по schema. Никакого текста вне JSON.")
        return "\n\n".join(sections)

    def criterion_ids(self) -> list[str]:
        return [str(c.get("id")) for c in self.criteria]
