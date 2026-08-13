# Kubernetes deployment reference

`resonance-apps.yaml` deploys Gateway, Web, Admin, API and Worker. PostgreSQL, Redis and S3-compatible object storage are intentionally external dependencies. Replace every image, Secret and endpoint before use. Run database migrations as a separate release Job before rolling API/Worker.

Required Secrets: `DATABASE_URL`, `WORKER_DATABASE_URL`, `REDIS_URL`, S3 credentials, JWT/media/webhook secrets and approved Provider credentials. Production should source these from an external secret controller backed by KMS/Secret Manager.
