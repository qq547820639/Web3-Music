# Third-party notices

This repository does not vendor third-party source code. Container images and Python packages are resolved from the declarations in `docker-compose.yml` and the service-level `requirements.txt` files. Each dependency remains subject to its own license and terms.

Important runtime dependencies include PostgreSQL, Redis, MinIO, Prometheus, Nginx, FastAPI, Uvicorn, Psycopg, PyJWT, HTTPX, Boto3, Pydantic and FFmpeg. Before public distribution or production deployment, the operating entity should generate an SBOM from the final locked images, retain the dependency license texts, and complete security and license review for the exact versions deployed.

Music-generation and payment providers are external services. This software license does not grant rights to those services, their models, generated outputs, payment rails, trademarks or APIs. Production use requires separate provider agreements and applicable rights review.
