"""Which audio-validation failures are the machine's fault, and which are the bytes'.

`worker.py` already draws this line twice: the media scanner raises ``Retryable`` when the engine is
unreachable rather than recording a candidate it could not scan as dirty (see the comment at that call
site), and the media transport retries ``httpx.TimeoutException``/``TransportError`` instead of blaming
the provider's audio. The third place that touches bytes -- ``ffprobe`` measuring the duration of a
downloaded candidate -- did not follow it: one ``except Exception`` wrapped *everything* into a
``RuntimeError``, so an ffprobe that never got a CPU slice settled the whole job as ``partial`` with
``attempt_count=1`` and the candidate marked failed forever.

Measured on this host at acceptance-20260928T051817Z, where the acceptance suite reddened for exactly
that reason::

    {"code": "incomplete_candidate_set", "ready": 1, "failed": 1, "requested": 2,
     "candidates": [{"type": "RuntimeError",
                     "cause": "TimeoutExpired: Command '['ffprobe', ...]' timed out after 15 seconds",
                     "message": "audio decode validation failed: ...", "ordinal": 1}]}

with the host at load average 11.5 (rising to 25) while the tooling on it ran a mutation battery, two
drills and this chain. Nothing about those bytes differed between the attempt that failed and an attempt
that would have succeeded -- which is the definition of a failure that should be retried.

The predicate is here rather than inline so it can be tested without importing ``worker.py``, which needs
redis, boto3 and the provider adapter at import time -- the same reason ``media_scan.py`` is its own module.
"""
from __future__ import annotations

import subprocess


def validation_is_infrastructure(exc: BaseException) -> bool:
    """True when the failure says something about the machine, not about the audio.

    Two shapes count as the machine's fault:

    * ``TimeoutExpired`` -- the probe process did not answer in its budget. A child that never ran is
      not evidence that the file is unreadable, and the retry sees different conditions;
    * ``FileNotFoundError`` -- there is no ``ffprobe`` to run at all, which is an image/PATH defect.

    Everything else stays a verdict on the bytes. The shape to guard against is the tidy-looking one:
    ``TimeoutExpired`` and ``CalledProcessError`` are siblings under ``subprocess.SubprocessError`` (measured
    MROs: ``SubprocessError -> Exception`` for both), so widening either clause to ``SubprocessError`` -- or
    deleting the ``CalledProcessError`` veto below -- would put "ffprobe looked at this file and refused it"
    into the retry bucket, i.e. retry the same unreadable audio forever. ``FileNotFoundError`` sits on the
    other side of the tree (``OSError``), which is why the two machine shapes are named separately rather
    than caught as one family.
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        return False
    return isinstance(exc, FileNotFoundError)
