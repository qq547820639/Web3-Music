"""Measure what tests/unit/test_provider_emulator.py:101 actually bounds.

The resident case asserts `elapsed < 1.0` for an idempotent replay, claiming clip synthesis has moved back
onto the request path. This probe repeats that replay and prints the observed span, plus two host-free
observables the claim could be stated on instead: whether the replay enqueued any generation work, and
whether the stored job's created_at moved.

Usage: python .scratch/probe_replay_margin.py <label> [--load]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
os.environ.setdefault("PATH", "")
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "te", ROOT / "tests" / "unit" / "test_provider_emulator.py")
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)

from fastapi.testclient import TestClient  # noqa: E402

label = sys.argv[1] if len(sys.argv) > 1 else "idle"
spinners = []
if "--load" in sys.argv:
    for _ in range(8):
        spinners.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
    time.sleep(3.0)

rows = []
try:
    for repeat in range(10):
        tmp = Path(tempfile.mkdtemp())
        module = te.load_module(tmp)
        client = TestClient(module.app)
        payload = {"title": "Replay", "lyrics": "line", "styles": "piano", "bpm": 72,
                   "candidate_count": 8, "scenario": "success"}
        key = f"probe-{repeat}"
        fresh = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
        assert fresh.status_code == 202, fresh.text
        job_id = fresh.json()["id"]
        time.sleep(2.2)

        # Observable A: how many times the request path handed work to the generator pool.
        submits = []
        real_submit = module._GENERATOR.submit

        def counting(fn, *a, **kw):
            submits.append(1)
            return real_submit(fn, *a, **kw)

        module._GENERATOR.submit = counting
        # Observable B: the stored creation stamp, which a replay must not move.
        conn = module.connect() if hasattr(module, "connect") else None
        created_before = None
        for name in ("connect", "db", "get_conn"):
            fn = getattr(module, name, None)
            if callable(fn):
                try:
                    c = fn()
                    created_before = c.execute("SELECT created_at FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                    break
                except Exception:
                    continue
        started = time.perf_counter()
        replay = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
        elapsed = time.perf_counter() - started
        module._GENERATOR.submit = real_submit
        created_after = created_before
        if conn is not None or created_before is not None:
            for name in ("connect", "db", "get_conn"):
                fn = getattr(module, name, None)
                if callable(fn):
                    try:
                        c = fn()
                        created_after = c.execute("SELECT created_at FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                        break
                    except Exception:
                        continue
        files = len(list((tmp / "media").glob("*.wav")))
        rows.append({"repeat": repeat, "elapsed_s": round(elapsed, 4), "status": replay.status_code,
                     "same_job": replay.json().get("id") == job_id, "submits_during_replay": len(submits),
                     "created_moved": (created_after != created_before) if created_before is not None else None,
                     "wav_files": files})
        print(f"{label} repeat={repeat} elapsed_s={elapsed:.4f} status={replay.status_code} "
              f"same_job={rows[-1]['same_job']} submits={len(submits)} created_moved={rows[-1]['created_moved']} "
              f"wavs={files}")
finally:
    for p in spinners:
        p.kill()

values = sorted(r["elapsed_s"] for r in rows)
print(json.dumps({"label": label, "n": len(values), "min": values[0], "median": values[len(values) // 2],
                  "max": values[-1], "breach_1s": sum(1 for v in values if v >= 1.0),
                  "all_same_job": all(r["same_job"] for r in rows),
                  "any_submit": any(r["submits_during_replay"] for r in rows),
                  "any_created_moved": any(r["created_moved"] for r in rows)}))
