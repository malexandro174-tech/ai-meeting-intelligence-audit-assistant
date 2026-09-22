# AI Meeting Intelligence & Audit Assistant

Портфельный продукт Mag_OS: ассистент аудита встреч. Отправьте аудио или видео встречи в Telegram — ассистент транскрибирует разговор, разделит участников (Speaker A/B), проведёт аудит по профилю, извлечёт решения, обязательства, действия, риски и открытые вопросы, сохранит всё в PostgreSQL и вернёт структурированный отчёт.

## О проекте

Команды проводят десятки встреч, но решения и обязательства остаются «в воздухе»: никто не фиксирует, кто что обещал и какие риски прозвучали. Этот ассистент превращает запись встречи в управляемый артефакт: структурированный отчёт с доказательствами (цитаты + таймкоды) и базу данных встреч, готовую для директорской аналитики.

## Проблема

- Содержание встреч не фиксируется системно;
- действия и обязательства теряются;
- риски и незакрытые вопросы не эскалируются;
- у руководства нет объективной картины по клиентским переговорам.

## Решение

Полный конвейер от медиафайла до аудита: media intake → валидация → извлечение аудио (ffmpeg) → транскрибация и диаризация (AssemblyAI) → нормализация → аудит (DeepSeek) → структурированный отчёт (Pydantic) → PostgreSQL → Telegram.

## Как работает

1. Пользователь отправляет MP3/WAV/M4A/MP4 в Telegram-бота (до ~45 МБ).
2. Файл валидируется (тип, размер, сигнатура, длительность, декодируемость) и безопасно именуется.
3. Для видео извлекается аудиодорожка (визуальную часть не отправляем).
4. AssemblyAI транскрибирует с диаризацией: реплики Speaker A/B с таймкодами.
5. Транскрипт нормализуется в доменную модель (provider-специфика изолирована в адаптере).
6. DeepSeek проводит аудит по профилю `commercial_meeting_v1`: 15 критериев + действия, решения, обязательства, вопросы, риски.
7. Каждый вывод проверяется на доказательность: без подтверждения в транскрипте значение схлопывается в UNKNOWN (анти-галлюцинации).
8. Отчёты (Markdown + JSON) и транскрипт сохраняются как артефакты; всё состояние — в PostgreSQL.
9. Пользователь получает в Telegram короткий итог; полный отчёт — артефактами.

## Архитектура

```
Telegram / File
   ↓
Media Intake (валидация, safe filenames)
   ↓
Audio Extractor (ffmpeg: видео → аудио)
   ↓
Broker Gateway → AssemblyAI (upload, transcription)
   ↓
Speaker Diarization (Speaker A/B + timestamps)
   ↓
Transcript Normalizer (доменная модель)
   ↓
AuditProfile (commercial_meeting_v1) + DeepSeek Analysis
   ↓
Structured Meeting Report (Pydantic) + артефакты
   ↓
PostgreSQL (meetings, transcripts, actions, risks…)
   ↓
Telegram Result (chunked) / Operator View
   ↓
n8n event (optional, non-blocking)
   ↓
Director-ready Contracts (Upward Report, KPI, Escalation)
```

## Speech Intelligence

AssemblyAI используется строго по назначению: загрузка аудио, транскрибация, диаризация, utterances с таймкодами. Бизнес-анализ встречи выполняет LLM-слой, а не провайдер транскрипции.

## Speaker Diarization

- Поддерживается автодетект числа спикеров (по умолчанию);
- `speakers_expected` передаётся только как run-scoped опция (например, `--speakers-expected 2` в smoke-тесте) и никогда не зашит в общий pipeline;
- реальные диалоги разделяются корректно: приёмка на живой записи 154 с дала Speaker A/B, 16 реплик, распределение 50/50.

## Meeting Audit Profiles

Профили аудита хранятся отдельно от кода (`profiles/*.yaml`), со структурой: `role`, `task_objective`, `criteria`, `audit_process`, `core_instructions`, `constraints`, `output_schema`. Критерии меняются без изменения runtime. Первый профиль — `commercial_meeting_v1` (15 критериев коммерческой встречи: цель, контекст клиента, потребность, текущий процесс, желаемый результат, ограничения, системы, интеграции, бюджет, сроки, ЛПР, риски, следующий шаг, ответственный, согласованное действие).

## Structured Reports

- Pydantic-валидированный JSON (`meeting_report.json`);
- человекочитаемый Markdown (`meeting_report.md`: резюме, участники, решения, обязательства, действия, вопросы, риски, аудит-таблица с доказательствами, рекомендации);
- `transcript.md` с таймкодами по спикерам.

## Director-ready Architecture

Реальный Directors Framework не подключён; подготовлены стабильные контракты (`contracts/meeting_contracts.schema.json`): `MeetingUpwardReport`, `KpiSnapshot`, `MeetingEscalation`, Finance/Engineering события. Совместимость: `get_recent_meetings()`, `get_open_actions()`, `get_critical_risks()`, `get_commercial_summary()`, `get_meeting_kpi()`.

## Broker Integrations

Broker-first: все внешние сервисы через Access & Integration Broker Mag_OS, где существует профиль:

| Сервис | Профиль Broker | Доставка секрета |
|---|---|---|
| AssemblyAI | `assemblyai` (LOCAL_ENV) | env-инъекция в one-shot воркер |
| DeepSeek | `deepseek` | Broker-owned inference |
| Telegram TEST | `telegram_course_test_bot` | env + allowlist чата |
| PostgreSQL | `postgresql` | собственный контейнер meeting-db (изоляция от Mag_OS core) |
| n8n | `n8n` | optional event, non-blocking |
| NocoDB | `nocodb` | battle-test read-only (LIMITED политикой) |

Проект никогда не читает raw API-ключи за пределами transport-адаптеров, не логирует их и не отдаёт вызывающим агентам — только scoped handle и capability.

## Безопасность

- Валидация типа/размера/сигнатуры медиа; безопасные имена; защита от path traversal;
- транскрипт — untrusted data: prompt-injection-изоляция (system/profile/data разделены);
- анти-галлюцинации: критерии без доказательств → NOT_DISCLOSED / NOT_SPECIFIED / UNKNOWN;
- секреты только через env-доставку; secret-scan перед публикацией;
- PostgreSQL во внутренней Docker-сети, порт наружу не публикуется;
- логи-санитизация (токены/ключи редачятся на уровне EventBus);
- приватность: реальные записи и транскрипты никогда не публикуются (в репозитории только synthetic fixtures).

## Docker

`Dockerfile` (python:3.12-slim + ffmpeg) и два стека: `docker-compose.yml` (локальная разработка) и `docker-compose.prod.yml` (VPS: лимиты памяти 384m/160m, restart policy, healthcheck, volumes для данных). Обоснование объединённого bot+worker: единственный триггер — поллинг бота, всё состояние в PostgreSQL, рестарт безопасен и возобновляет незавершённые встречи.

## Тестирование

- Юнит: безопасность имён, magic-byte sniffing, нормализация, чанкинг, evidence-guard, prompt-injection-изоляция, профили;
- Интеграционные (PostgreSQL): полный lifecycle, идемпотентность (content hash), retryable-состояния, resume после рестарта, KPI;
- Живые: Broker matrix (assemblyai/deepseek/telegram/postgresql/n8n/nocodb), E2E на реальном аудио;
- `fixtures/make_synth_media.py` — синтетические MP3/WAV/MP4 (включая видео-контейнер для теста извлечения аудио).

## Demo

1. Откройте TEST-чат Telegram (бот `@test_joba2026_bot`);
2. `/start` → отправьте короткий MP3 диалога;
3. статусы: «Файл получен» → … → готовый итог с действиями/рисками;
4. Operator view: `http://127.0.0.1:8081/` (тёмная тема: список встреч, статусы, спикеры, действия, риски).

## Quick Start

```bash
# локальная разработка
docker compose up -d meeting-db
pip install -e ".[dev]"
python -m pytest tests/

# запуск ассистента (magos_local transport, внутри Mag_OS)
python -m app.main

# контейнерный запуск (scoped env доставка)
cp .env.example .env   # заполните значения
docker compose up -d --build
```

## Limitations

- DeepSeek-анализ требует доставки credentials (в момент написания vault-слот заблокирован; встреча переходит в RETRY_PENDING и доанализируется автоматически после восстановления);
- Telegram TEST-бот ограничен зарегистрированным тест-чатом (policy);
- diarization синтетических голосов одного движка сливается в одного спикера (ограничение провайдера, на живых диалогах не воспроизводится);
- NocoDB — read-only по политике Broker.

## Roadmap

- Подключение реального Directors Framework (контракты готовы);
- выгрузка DOCX-отчётов;
- веб-загрузка больших файлов (сейчас лимит Telegram);
- Speaker Identification по именам участников;
- ретроспективная аналитика по базе встреч.

---
Технический стек: Python 3.12, FastAPI-free stdlib runtime, Pydantic v2, psycopg 3, PostgreSQL 16, ffmpeg, Docker. Управление доступом — Mag_OS Access & Integration Broker.
