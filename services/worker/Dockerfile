FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# apt over TLS: deb.debian.org plain HTTP:80 is unreachable from some CI/dev networks
# (Colima VMs, corporate egress), and an encrypted index also stops a network observer
# from choosing which package versions resolve.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; \
    apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 -r requirements.txt && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY . .
CMD ["python","worker.py"]
