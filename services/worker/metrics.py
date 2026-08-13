"""Dependency-free Prometheus endpoint for the reference worker."""
from __future__ import annotations

import threading
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def inc(name: str, value: float = 1, **labels: str) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with _lock:
        _counters[key] += value


def set_gauge(name: str, value: float, **labels: str) -> None:
    key = (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))
    with _lock:
        _gauges[key] = value


def render() -> bytes:
    lines = [
        "# HELP ai_music_worker_up Worker process is running.",
        "# TYPE ai_music_worker_up gauge",
        "ai_music_worker_up 1",
    ]
    with _lock:
        counter_groups: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = defaultdict(list)
        gauge_groups: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = defaultdict(list)
        for (name, labels), value in _counters.items():
            counter_groups[name].append((labels, value))
        for (name, labels), value in _gauges.items():
            gauge_groups[name].append((labels, value))
        for name in sorted(counter_groups):
            lines.append(f"# TYPE {name} counter")
            for labels, value in sorted(counter_groups[name]):
                lines.append(_sample(name, labels, value))
        for name in sorted(gauge_groups):
            lines.append(f"# TYPE {name} gauge")
            for labels, value in sorted(gauge_groups[name]):
                lines.append(_sample(name, labels, value))
    return ("\n".join(lines) + "\n").encode()


def _sample(name, labels, value):
    if labels:
        text = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
        return f"{name}{{{text}}} {value}"
    return f"{name} {value}"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/metrics", "/health"}:
            self.send_response(404)
            self.end_headers()
            return
        body = render() if self.path == "/metrics" else b'{"status":"ok"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4" if self.path == "/metrics" else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def start_server(port: int = 9101) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="worker-metrics").start()
    return server
