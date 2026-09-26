FROM python:3.12-slim
WORKDIR /tests
COPY requirements.txt .
RUN set -e; for attempt in 1 2 3 4 5; do \
      pip install --no-cache-dir --timeout 60 --retries 10 -r requirements.txt && exit 0; \
      echo "pip attempt $attempt failed, retrying"; sleep 15; \
    done; echo "pip install failed after 5 attempts"; exit 1
COPY test_acceptance.py test_commercial.py test_provider_contract.py test_payment_contract.py test_tenant_isolation.py ./
CMD ["pytest","-q","-s","test_acceptance.py"]
