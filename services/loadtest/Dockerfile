FROM python:3.12-slim
WORKDIR /load
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 httpx==0.28.1 && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY scripts/load-test-500.py /load/load-test-500.py
ENTRYPOINT ["python","/load/load-test-500.py"]
