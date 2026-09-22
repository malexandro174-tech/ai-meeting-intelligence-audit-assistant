# Отчёт по заданию

**Проект:** AI Meeting Intelligence & Audit Assistant
**Репозиторий:** ai-meeting-intelligence-audit-assistant
**Статус:** v1.0.0

## Что сделано

Вместо узкого сценария «аудит собеседования» из урока построен полноценный продукт: ассистент аудита коммерческих встреч. Пользователь отправляет аудио/видео встречи в Telegram → конвейер извлекает аудио (ffmpeg), транскрибирует и разделяет участников (AssemblyAI: Speaker A/B с таймкодами), нормализует транскрипт в доменную модель, проводит аудит по профилю (DeepSeek) и возвращает структурированный отчёт: решения, обязательства, действия, открытые вопросы, риски и аудит-таблицу с доказательствами. Всё состояние — в PostgreSQL; артефакты (transcript.md, meeting_report.md/json) сохраняются на диск.

## Обоснование изменений промпта

Исходный профиль урока адаптирован под аудит коммерческой/клиентской встречи. Критерии собеседования заменены на: customer context, needs, current process, desired result, budget, timeline, integrations, next steps, commitments, risks. Структура профиля сохранена полностью: `role`, `task_objective`, `criteria` (internal), `audit_process`, `core_instructions`, `constraints`. Профиль хранится в `profiles/commercial_meeting_v1.yaml` — отдельно от runtime-кода; критерии можно менять, не трогая pipeline.

## Модульная структура prompt — сохранена и усилена

- секции профиля разделены (role / task_objective / criteria / audit_process / core_instructions / constraints / output_schema);
- system prompt собирается только из профиля; транскрипт передаётся отдельно как ДАННЫЕ между маркерами `===TRANSCRIPT_DATA_BEGIN/END===` — prompt-injection-изоляция (попытки «игнорируй инструкции» внутри записи не меняют поведение);
- LLM-вывод валидируется Pydantic-схемой; каждый важный вывод обязан иметь evidence — короткую цитату из транскрипта; выводы без подтверждения схлопываются в `NOT_DISCLOSED` / `NOT_SPECIFIED` / `UNKNOWN` (анти-галлюцинации: бюджет не обсуждали → `BUDGET=NOT_DISCLOSED`).

## Распределение ответственности

- **AssemblyAI** — только speech intelligence: upload, transcription, diarization, utterances, timestamps;
- **DeepSeek** — только аудит/анализ по профилю (resolved provider/model не хардкожен);
- **PostgreSQL** — персистентность (meetings, transcripts, utterances, analyses, action_items, decisions, risks, processing_events);
- **Telegram** — интерфейс (TEST-профиль, allowlist-чат).

## Инженерные аспекты

- Broker-first: все внешние сервисы через Access & Integration Broker Mag_OS; raw-ключи не покидают transport-адаптеры;
- идемпотентность по content hash (повторная загрузка не анализирует заново; упавшие встречи ретраятся);
- resume после рестарта: незавершённые встречи продолжаются без повторной загрузки в AssemblyAI;
- bounded retry + backoff, типизированные ошибки (AUTH_FAILED, RATE_LIMITED, PROVIDER_UNAVAILABLE…);
- observability: structured events (meeting.received → … → report.created), один correlation_id на встречу, cost-счётчики;
- Director-ready контракты: MeetingUpwardReport, KpiSnapshot, MeetingEscalation, Finance/Engineering события;
- безопасность: валидация медиа, safe filenames, path-traversal защита, secret-scan, приватность (реальные записи не публикуются).

## Живые проверки

- Транскрибация+диаризация реального диалога 154 с: Speaker A/B, 16 реплик, 1681 символ — PASS;
- полная цепочка локально: Telegram(TEST) → валидация → AssemblyAI → PostgreSQL → артефакты — PASS (анализ — RETRY_PENDING до восстановления доставки DeepSeek-ключа);
- серверный деплой: docker compose prod, healthchecks, рестарт-тест с сохранением истории.

## Выводы

Сценарий «аудит встречи» доказанно полезнее учебного «аудита собеседования»: продукт закрывает реальную боль (потерянные обязательства и риски) и готов к встраиванию в директорскую вертикаль Mag_OS.
