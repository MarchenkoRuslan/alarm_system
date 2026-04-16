FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN python -m pip install --upgrade pip build && \
    python -m build --wheel --outdir /tmp/dist

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system alarm && adduser --system --ingroup alarm alarm

COPY --from=builder /tmp/dist /tmp/dist
COPY deploy /app/deploy

RUN python -m pip install --upgrade pip && \
    pip install /tmp/dist/*.whl "websockets>=12.0" && \
    rm -rf /tmp/dist

USER alarm

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import os,sys; required=['ALARM_ASSET_IDS','ALARM_REDIS_URL','ALARM_RULES_PATH','ALARM_ALERTS_PATH','ALARM_CHANNEL_BINDINGS_PATH']; missing=[k for k in required if not os.getenv(k)]; sys.exit(1 if missing else 0)"

CMD ["run-service"]
