"""Job-outcome helpers.

Dependency-free on purpose: worker.py builds a Redis client and an S3 client at import time,
so keeping the outcome logic here lets the unit suite exercise it directly.
"""


def terminal_error(final, provider_error=None, ready=0, failed=0, requested=0, candidate_errors=()):
    """Return the reason a job should carry, or None when it genuinely completed.

    A provider payload is only one of three ways a job fails to deliver. The other two --
    candidate ingest raising (stored on the audio_candidates row) and the provider returning
    fewer candidates than requested (stored as synthetic 'missing-N' rows) -- left the job row
    at status='failed', error=NULL, so GET /api/jobs/{id} reported a failure with no cause.
    Measured that way on 2026-09-25: 13 of 15 failed jobs had error=NULL and the real reason
    ("audio decode validation failed: ffprobe ... timed out after 15 seconds") was only
    reachable by joining audio_candidates.
    """
    counts = {"ready": ready, "failed": failed, "requested": requested}
    if final == "completed":
        return provider_error
    if isinstance(provider_error, dict):
        error = dict(provider_error)
    elif provider_error:
        error = {"provider": provider_error}
    else:
        error = {}
    error.setdefault(
        "code",
        "provider_reported_failure" if provider_error else ("no_ready_candidates" if not ready else "incomplete_candidate_set"),
    )
    if candidate_errors and "candidates" not in error:
        error["candidates"] = list(candidate_errors[:3])
    error.update(counts)
    return error


def error_signature(exc: BaseException) -> dict:
    """What to store when a candidate cannot be ingested.

    `str(exc)` alone was the dead end: httpx builds its transport errors with an empty message, so
    the record read `{"type": "ReadError", "message": ""}` and the next question -- reset, closed,
    or protocol? -- had no answer in the database. The underlying exception and the args are
    captured when present, and `message` falls back to the type name rather than to "" so a reader
    never has to distinguish "no reason" from "no reason recorded".
    """
    cause = getattr(exc, "__cause__", None)
    signature = {
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
    }
    if cause is not None:
        signature["cause"] = f"{type(cause).__name__}: {cause or type(cause).__name__}"
    return signature
