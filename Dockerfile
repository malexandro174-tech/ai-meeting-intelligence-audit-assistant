# AI Meeting Intelligence & Audit Assistant — production-like image.
# Bot + pipeline in one service (single trigger source; durable state lives in PostgreSQL).
FROM python:3.12-slim

# ffmpeg for audio extraction/normalization/probing.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/meeting
COPY pyproject.toml ./
COPY app ./app
COPY profiles ./profiles
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 BROKER_TRANSPORT=container_env \
    MEETING_DATABASE_URL=postgresql://meeting:meeting@meeting-db:5432/meeting_intelligence \
    MEETING_DATA_DIR=/srv/meeting/data
VOLUME ["/srv/meeting/data"]
HEALTHCHECK --interval=60s --timeout=10s --retries=5 \
    CMD python -c "import pathlib,sys; sys.exit(0 if pathlib.Path('/srv/meeting/data').exists() else 1)"
CMD ["python", "-m", "app.main"]
