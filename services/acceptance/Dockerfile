FROM python:3.12-slim
WORKDIR /tests
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY test_acceptance.py test_commercial.py test_provider_contract.py test_payment_contract.py test_tenant_isolation.py ./
CMD ["pytest","-q","-s","test_acceptance.py"]
