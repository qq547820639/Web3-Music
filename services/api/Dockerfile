FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    API_WORKERS=1 API_LIMIT_CONCURRENCY=512 API_KEEPALIVE_SECONDS=5
COPY requirements.txt .
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 -r requirements.txt && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY app ./app
RUN mkdir -p /data/media
EXPOSE 8000
# --forwarded-allow-ips is required, not defaulted: uvicorn's own fallback is 127.0.0.1, which behind a
# gateway means "trust nobody", and every session would silently record the gateway container as the
# client again. ${VAR:?} makes that a boot failure instead of a quiet loss of the audit trail.
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${API_WORKERS} --limit-concurrency ${API_LIMIT_CONCURRENCY} --timeout-keep-alive ${API_KEEPALIVE_SECONDS} --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:?FORWARDED_ALLOW_IPS must name the proxy peer; see .env.example}\""]
