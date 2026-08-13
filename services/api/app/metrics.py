"""Small dependency-free Prometheus exposition for the reference stack."""
from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_requests: Counter[tuple[str, str, int]] = Counter()
_duration_sum: Counter[tuple[str, str]] = Counter()
_duration_count: Counter[tuple[str, str]] = Counter()


def observe_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    route = route or "unmatched"
    with _lock:
        _requests[(method, route, status)] += 1
        _duration_sum[(method, route)] += duration_seconds
        _duration_count[(method, route)] += 1


def render() -> str:
    lines = [
        "# HELP ai_music_api_info Static API build information.",
        "# TYPE ai_music_api_info gauge",
        'ai_music_api_info{version="13.0.0"} 1',
        "# HELP ai_music_api_http_requests_total HTTP requests handled by method, route and status.",
        "# TYPE ai_music_api_http_requests_total counter",
    ]
    with _lock:
        for (method, route, status), value in sorted(_requests.items()):
            lines.append(f'ai_music_api_http_requests_total{{method="{_esc(method)}",route="{_esc(route)}",status="{status}"}} {value}')
        lines.extend([
            "# HELP ai_music_api_http_request_duration_seconds_sum Accumulated request duration.",
            "# TYPE ai_music_api_http_request_duration_seconds_sum counter",
        ])
        for (method, route), value in sorted(_duration_sum.items()):
            lines.append(f'ai_music_api_http_request_duration_seconds_sum{{method="{_esc(method)}",route="{_esc(route)}"}} {value:.9f}')
        lines.extend([
            "# HELP ai_music_api_http_request_duration_seconds_count Number of timed requests.",
            "# TYPE ai_music_api_http_request_duration_seconds_count counter",
        ])
        for (method, route), value in sorted(_duration_count.items()):
            lines.append(f'ai_music_api_http_request_duration_seconds_count{{method="{_esc(method)}",route="{_esc(route)}"}} {value}')
    return "\n".join(lines) + "\n"


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
