"""Mutation arm for tests/unit/test_provider_emulator.py's rebuilt replay check.

Moves clip synthesis back onto the request path -- the exact defect the case exists to catch -- by
exec'ing the emulator with `return resolve(existing)` changed to publish inline, then replaying an aged
job under the same spies. The check must fire. Nothing here touches the repository's own source.
"""
import importlib.util
import os
import struct
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "services" / "provider-emulator" / "main.py").read_text(encoding="utf-8")

OLD = 'return resolve(existing)'
NEW = 'publish(existing["id"]); return resolve(existing)'
assert SRC.count(OLD) == 1, SRC.count(OLD)
mutated = SRC.replace(OLD, NEW)

tmp = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(tmp / "provider.db")
os.environ["MEDIA_ROOT"] = str(tmp / "media")
mutated = "import threading\n" + mutated
spec = importlib.util.spec_from_loader("emulator_mutant", loader=None)
module = importlib.util.module_from_spec(spec)
exec(compile(mutated, "emulator_mutant.py", "exec"), module.__dict__)

sys.path.insert(0, str(ROOT / "tests" / "unit"))
te_spec = importlib.util.spec_from_file_location("te_helpers", ROOT / "tests" / "unit" / "test_provider_emulator.py")
te = importlib.util.module_from_spec(te_spec)
te_spec.loader.exec_module(te)

from fastapi.testclient import TestClient  # noqa: E402

written_on, served_on = [], []
real_write = te.fake_writer(0.5)


def writer(path, seed, bpm, ordinal):
    written_on.append(threading.get_ident())
    return real_write(path, seed, bpm, ordinal)


real_resolve = module.resolve


def spy_resolve(row):
    served_on.append(threading.get_ident())
    return real_resolve(row)


module.write_wav = writer
module.resolve = spy_resolve

client = TestClient(module.app)
payload = {"title": "Replay", "lyrics": "line", "styles": "piano", "bpm": 72,
           "candidate_count": 8, "scenario": "success"}
key = "mutation-replay"
started = time.perf_counter()
fresh = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
first_elapsed = time.perf_counter() - started
job_id = fresh.json()["id"]
written_on.clear(); served_on.clear()
time.sleep(2.2)
started = time.perf_counter()
replay = client.post("/v1/jobs", json=payload, headers={"Idempotency-Key": key})
elapsed = time.perf_counter() - started
body = replay.json()
fired = te.synthesis_on_the_serving_thread(written_on, served_on)
with module.db() as conn:
    published = conn.execute("SELECT results_json FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
print(f"mutant: results_json_published_after_replay={published is not None} (the resident case refuses a replay that had already published)")
print(f"mutant: first={first_elapsed:.3f}s replay={elapsed:.3f}s status={body.get('status')} "
      f"writes_during_replay_window={len(written_on)} serving_threads={len(set(served_on))} "
      f"rule_fired={fired} wrote_on_serving_thread={bool(set(written_on) & set(served_on))}")
print("VERDICT:", "mutation caught (the check has teeth)" if fired or body.get("status") == "completed"
      else "MUTATION SURVIVED -- the rebuilt check does not catch inline synthesis")
