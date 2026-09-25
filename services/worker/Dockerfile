FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# apt over TLS: deb.debian.org plain HTTP:80 is unreachable from some CI/dev networks
# (Colima VMs, corporate egress), and an encrypted index also stops a network observer
# from choosing which package versions resolve.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; \
    apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python","worker.py"]
