FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    API_WORKERS=1 API_LIMIT_CONCURRENCY=512 API_KEEPALIVE_SECONDS=5
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /data/media
EXPOSE 8000
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${API_WORKERS} --limit-concurrency ${API_LIMIT_CONCURRENCY} --timeout-keep-alive ${API_KEEPALIVE_SECONDS}"]
