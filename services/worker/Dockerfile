FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# apt over TLS: deb.debian.org plain HTTP:80 is unreachable from some CI/dev networks
# (Colima VMs, corporate egress), and an encrypted index also stops a network observer
# from choosing which package versions resolve.
# The retries and the long per-request timeout are for the same failure the pip loop above got:
# measured 2026-09-26T16:5Z, this step could not fetch the 9.6 MB arm64 index at all -- apt logged
# `Ign:` (its own three attempts spent) while a 43 kB InRelease and a 264 kB security index came
# through, i.e. large objects over this path die slowly rather than failing fast. Skipping the
# Translation indices removes a second pile of requests nobody needs to install two packages.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; \
    apt-get -o Acquire::Retries=8 -o Acquire::http::Timeout=180 -o Acquire::Languages=none update \
    && apt-get -o Acquire::Retries=8 -o Acquire::Languages=none install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 -r requirements.txt && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY . .
CMD ["python","worker.py"]
