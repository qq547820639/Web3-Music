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
