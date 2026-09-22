# MAG_OS Reuse Notes

## Future placement

Продукт спроектирован как reusable-компонент Mag_OS. Целевое размещение:

- **Shared Intelligence Services** — как общий speech-intelligence/meeting-audit сервис, либо
- **Commercial Directorate support employee** — как AI Employee «Meeting Intelligence & Audit Employee» (scope: транскрибация встреч, анализ, извлечение действий/рисков, директорская отчётность).

Слот для будущего сотрудника уже зарезервирован: requester `meeting-intelligence-agent` (AI_EMPLOYEE) в Broker-профиле `assemblyai`; requester модуля `ai-meeting-intelligence-assistant` (MAG_OS_MODULE) активен сейчас.

## Possible consumers

- Commercial Director — коммерческий свод по встречам, открытые обязательства клиентов;
- Financial Director — события BUDGET_MENTIONED / PRICE_DISCUSSION / REFUND_REQUEST / PAYMENT_RISK / REVENUE_OPPORTUNITY;
- Engineering Director — события TECHNICAL_BLOCKER / INTEGRATION_REQUIRED / SECURITY_CONCERN / INFRASTRUCTURE_DEPENDENCY;
- HR / Training — аудит встреч обучения;
- Writer OS — voice intake (голосовые заметки → текст);
- Customer Support / Sales — анализ звонков и переговоров.

## Integration surface

- Contracts: `contracts/meeting_contracts.schema.json` (MeetingUpwardReport, KpiSnapshot, MeetingEscalation, Finance/Engineering events);
- repository API: `get_recent_meetings()`, `get_open_actions()`, `get_critical_risks()`, `get_customer_commitments()`, `get_commercial_summary()`, `get_meeting_kpi()`;
- профили аудита: `profiles/*.yaml` — новые сценарии без изменения кода;
- deploy: изолированный meeting-db (не Mag_OS core PostgreSQL), лимиты памяти, internal network.
