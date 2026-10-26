# Hardened runtime image: pinned versions, non-root user, read-friendly defaults.
FROM python:3.13.5-alpine3.22

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user (matches the Kubernetes securityContext runAsUser).
RUN addgroup -g 10001 app && adduser -S -D -u 10001 -G app app

WORKDIR /app

# Install dependencies first to maximise layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Writable heartbeat directory (mounted as emptyDir in Kubernetes).
RUN mkdir -p /app/state && chown -R app:app /app

USER 10001
ENV HEARTBEAT_FILE=/app/state/heartbeat

# Exec form: python runs as PID 1 and receives SIGTERM for graceful shutdown.
ENTRYPOINT ["python", "-m", "bot"]
