import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_api_metrics_exposition_counts_requests():
    metrics = load("api_metrics_under_test", ROOT / "services/api/app/metrics.py")
    metrics.observe_request("GET", "/health", 200, 0.125)
    text = metrics.render()
    assert 'ai_music_api_http_requests_total{method="GET",route="/health",status="200"} 1' in text
    assert 'ai_music_api_http_request_duration_seconds_sum{method="GET",route="/health"} 0.125000000' in text


def test_worker_metrics_exposition_contains_labels():
    metrics = load("worker_metrics_under_test", ROOT / "services/worker/metrics.py")
    metrics.inc("ai_music_worker_jobs_terminal_total", status="completed")
    metrics.inc("ai_music_worker_jobs_terminal_total", status="failed")
    metrics.set_gauge("ai_music_worker_build_info", 1, version="13.0.0")
    text = metrics.render().decode()
    assert 'ai_music_worker_jobs_terminal_total{status="completed"} 1' in text
    assert text.count("# TYPE ai_music_worker_jobs_terminal_total counter") == 1
    assert 'ai_music_worker_build_info{version="13.0.0"} 1' in text
