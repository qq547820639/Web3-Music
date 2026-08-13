FROM python:3.12-slim
WORKDIR /load
RUN pip install --no-cache-dir httpx==0.28.1
COPY scripts/load-test-500.py /load/load-test-500.py
ENTRYPOINT ["python","/load/load-test-500.py"]
