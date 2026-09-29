"""One owner for the judgements a rate-limit window is certified with.

Two permanent drills need the same thing: a way to say "this window spent its own seconds while
clients kept knocking on it" without measuring the host. `mfa_drill` and `report_drill` both read a
Redis TTL across `docker compose exec` round trips whose latency nothing here controls, so a bare
strict decrease is a coin flip -- measured on this machine today, the interval between the two
samples of `report_drill`'s flood block ranged 0.94 s to 1.84 s (`.scratch/probe_report_decay.py`,
four runs; the two execs alone cost 0.20-0.46 s each). Keeping the rule in one file is what stops the
two drills from drifting into two rulers with one name.
"""

from __future__ import annotations

MIN_TRAFFIC_SPAN = 2.0
MIN_TRAFFIC_REQUESTS = 5


def traffic_floor_met(stamps: list, knocked: int, min_span: float = MIN_TRAFFIC_SPAN,
                      min_requests: int = MIN_TRAFFIC_REQUESTS) -> bool:
    """The traffic loop's exit condition, stated on the same quantity the verdict then judges.

    Both ends of the span are *response receipts*: the first request's own round trip is not inside the
    interval whose decay is read, so a floor applied to a budget measured from the issue instant can be
    satisfied while the judged interval is still short. That is how this check reddened its own first live
    run (`acceptance-20260929T095633Z`: 47 refused requests, `Retry-After [58, 58, 58] … [56, 56, 56]`,
    a window that had spent two of itself) -- the loop stopped at 2.0 s from the first send, the span the
    verdict received was 1.96 s, and `span >= 2.0` was false. One origin, two uses.
    """
    return knocked >= min_requests and len(stamps) >= 2 and stamps[-1] - stamps[0] >= min_span


def refusals_count_down(waits: list, span: float, ttl_before: int, ttl_after: int,
                        min_span: float = MIN_TRAFFIC_SPAN) -> bool:
    """Whether the waits the api itself stated describe one window running down under the traffic.

    Every sample is a `Retry-After` header read off a refusal the api answered, so the two ends of the
    comparison are computed server-side at answer time and carried on the same transport: no `docker exec`
    round trip sits between them. That matters because the reading the check used to make -- the store's TTL
    sampled before and after the traffic -- is taken across two exec round trips whose own latency this drill
    does not control, and on a busy host it is those seconds, not the limiter, that set the answer. The store
    is still consulted, but only for the one thing its read latency cannot fake: a key with a fixed deadline
    never reads higher later on, so `ttl_after > ttl_before` is a re-arm or a replaced window wherever the
    host sits.

    A limiter that re-arms on a refusal cannot pass: it restates the full window on every knock, so the
    sequence never spends anything. A window that expired and was replaced mid-traffic cannot pass either,
    because the traffic stops being refused and the non-429 answer enters `waits` as `None`.

    The floors are durations, not rates. The check this replaces required five refused requests inside a
    two-second budget, which is a throughput criterion wearing a behaviour test: the same limiter code read
    14 to 169 refused requests in that budget across nineteen chain transcripts (working files under
    `.scratch/`, quoted with their figures in `docs/FINAL_RELEASE_STATUS.md`), and
    `acceptance-20260929T080910Z` (host load 50.45 against 10 cpus) read 3 and reddened. Here a slower host
    just knocks for longer.
    """
    if len(waits) < 2 or span < min_span:
        return False
    if not all(isinstance(w, int) and 0 < w <= 60 for w in waits):
        return False
    if any(before < after for before, after in zip(waits, waits[1:])):
        return False
    if not 0 < ttl_after <= ttl_before <= 60:
        return False
    return waits[0] - waits[-1] >= 1
