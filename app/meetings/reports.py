"""Human-readable and JSON report artifacts per meeting."""
from __future__ import annotations

import json
from pathlib import Path

from .models import MeetingAnalysis, Transcript

STATUS_ICONS = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}


def transcript_markdown(transcript: Transcript) -> str:
    header = (f"# Транскрипт встречи\n\n- Файл: `{transcript.filename}`\n"
              f"- Длительность: {transcript.duration_seconds:.0f} c\n"
              f"- Спикеров: {transcript.speaker_count}\n"
              f"- Язык: {transcript.language or '—'}\n\n---\n\n")
    return header + transcript.render_speaker_turns() + "\n"


def meeting_report_markdown(transcript: Transcript, analysis: MeetingAnalysis) -> str:
    lines: list[str] = []
    icon = STATUS_ICONS.get(analysis.overall_status, "🟡")
    lines.append(f"# Отчёт по встрече {icon}\n")
    lines.append(f"**Встреча:** `{analysis.meeting_id}` · профиль аудита: `{analysis.audit_results.profile_id}`\n")
    lines.append("## Краткое резюме\n")
    lines.append(analysis.meeting_summary + "\n")
    lines.append("## Участники\n")
    speakers = sorted({u.speaker for u in transcript.utterances})
    lines.append(", ".join(f"Speaker {s}" for s in speakers) + f" ( всего: {transcript.speaker_count} )\n")
    lines.append("## Что обсуждали\n")
    lines.append(transcript.render_speaker_turns(limit=6) + ("\n…" if len(transcript.utterances) > 6 else "") + "\n")

    lines.append("## Решения\n")
    lines.extend(f"- {d.text} _(участники: {', '.join(d.participants) or '—'})" +
                 (f" · доказательство: «{d.evidence}»" if d.evidence else "") + ")" for d in analysis.decisions)
    if not analysis.decisions:
        lines.append("- Решения не зафиксированы.")
    lines.append("")

    lines.append("## Обязательства\n")
    lines.extend(f"- Speaker {c.speaker}: {c.commitment}" +
                 (f" (срок: {c.deadline})" if c.deadline != "NOT_SPECIFIED" else "") for c in analysis.commitments)
    if not analysis.commitments:
        lines.append("- Обязательства не зафиксированы.")
    lines.append("")

    lines.append("## Следующие действия\n")
    lines.extend(f"- [ ] {a.text} — отв.: {a.owner}" +
                 (f", срок: {a.deadline}" if a.deadline != "NOT_SPECIFIED" else "") for a in analysis.actions)
    if not analysis.actions:
        lines.append("- Действия не зафиксированы.")
    lines.append("")

    lines.append("## Незакрытые вопросы\n")
    lines.extend(f"- ❓ {q.question} (поднял: {q.raised_by}; адресат: {q.assigned_to})" for q in analysis.open_questions)
    if not analysis.open_questions:
        lines.append("- Открытых вопросов нет.")
    lines.append("")

    lines.append("## Риски\n")
    lines.extend(f"- **{r.severity} / {r.category}**: {r.description}" +
                 (f" → {r.recommended_action}" if r.recommended_action else "") for r in analysis.risks)
    if not analysis.risks:
        lines.append("- Риски не зафиксированы.")
    lines.append("")

    lines.append("## Аудит встречи\n")
    lines.append("| Критерий | Значение | Доказательство |")
    lines.append("|---|---|---|")
    for c in analysis.audit_results.criteria:
        evidence = f"«{c.evidence[:80]}»" if c.evidence else "—"
        lines.append(f"| {c.criterion} | {c.value} | {evidence} |")
    lines.append("")
    lines.append("## Рекомендации\n")
    lines.extend(f"{i + 1}. {r}" for i, r in enumerate(analysis.recommendations))
    if not analysis.recommendations:
        lines.append("1. —")
    lines.append("")
    return "\n".join(lines)


def write_artifacts(directory: Path, transcript: Transcript, analysis: MeetingAnalysis | None) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {"transcript_md": directory / "transcript.md",
             "report_md": directory / "meeting_report.md",
             "report_json": directory / "meeting_report.json"}
    paths["transcript_md"].write_text(transcript_markdown(transcript), encoding="utf-8")
    if analysis:
        paths["report_md"].write_text(meeting_report_markdown(transcript, analysis), encoding="utf-8")
        paths["report_json"].write_text(
            json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return paths
