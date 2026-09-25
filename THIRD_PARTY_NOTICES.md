# Third-party notices

This repository does not vendor third-party source code. Container images and Python packages are resolved from the declarations in `docker-compose.yml` and the service-level `requirements.txt` files. Each dependency remains subject to its own license and terms.

Important runtime dependencies include PostgreSQL, Redis, MinIO, Prometheus, Nginx, FastAPI, Uvicorn, Psycopg, PyJWT, HTTPX, Boto3, Pydantic and FFmpeg. Before public distribution or production deployment, the operating entity should generate an SBOM from the final locked images, retain the dependency license texts, and complete security and license review for the exact versions deployed.

Music-generation and payment providers are external services. This software license does not grant rights to those services, their models, generated outputs, payment rails, trademarks or APIs. Production use requires separate provider agreements and applicable rights review.

## Test-only dependency fetched at run time

`scripts/browser_a11y.py` downloads one file, `axe.min.js` from the `axe-core` npm package (Deque Systems, **MPL-2.0**), pins it to version 4.13.0 and verifies it against sha256 `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1` before use, caching it under the git-ignored `.cache/`. It is injected only into the local browser audit and is never served to users, linked from the application, or included in any image. Playwright (Microsoft, **Apache-2.0**) and the Chromium build it drives are likewise test-harness-only; see `scripts/requirements-browser.txt`. MPL-2.0 permits this unmodified use; the file's own license text ships inside the downloaded npm tarball (`package/LICENSE`) and should be retained with the audit record if that record is distributed.
