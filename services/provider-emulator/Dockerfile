FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 -r requirements.txt && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY main.py .
RUN mkdir -p /data/provider-media
EXPOSE 8010
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8010"]
