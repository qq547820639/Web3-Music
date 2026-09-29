"""Recompute, from checked-in sources, every figure the subagent census flagged as stale.

Reads only. Prints CLAIM (the doc's own line, truncated) against TRUTH (my recomputation) so each
candidate can be adjudicated by opening the same objects, not by trusting the report.
"""
import collections
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path("/Volumes/Extra/CodeProj/web3_music")
import yaml


def doc_line(rel, n):
    lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    return lines[n - 1] if n - 1 < len(lines) else "<no such line>"


def show(rel, n, truth, needle=None):
    line = doc_line(rel, n)
    hit = needle in line if needle else True
    print(f"--- {rel}:{n}  [{'needle seen' if hit else 'NEEDLE ABSENT'}]")
    print(f"    CLAIM: …{line[max(0, line.find(needle) - 60) if needle else 0:][:260]}…")
    print(f"    TRUTH: {truth}")


routes = json.loads((ROOT / "shared/contracts/authority-matrix.json").read_text())["routes"]
writes = [r for r in routes if r["writes"]]
buckets = collections.Counter(r["authority"] for r in writes)
step_up = sum(1 for r in routes if r["step_up"])
print("routes/writes/step_up:", len(routes), len(writes), step_up)
print("write authority buckets:", dict(buckets))
print("app-level-only writes paths:", [r["path"] for r in writes if r["authority"] == "app-level-only"])

summ = (ROOT / "release-evidence/acceptance-20260928T230940Z/SUMMARY.txt").read_text(encoding="utf-8")
metrics = dict(re.findall(r"metrics (\S+) (\S+)=(\S+)", summ.replace("=", " ", 0)) and [])
rows = re.findall(r"^metrics (\S+) (.*)$", summ, re.M)
m = {}
for step, kv in rows:
    for k, v in re.findall(r"(\w+)=(\S+)", kv):
        m[f"{step}.{k}"] = v
print("SUMMARY metrics:", json.dumps(m, indent=0))
print("SUMMARY git_commit:", re.search(r"git_commit=(\w+)", summ).group(1)[:7])

rep = json.loads((ROOT / "release-evidence/browser-a11y-20260928T232127Z/report.json").read_text())
keys = {k: rep[k] for k in rep if not isinstance(rep[k], (dict, list))}
print("report scalars:", json.dumps(keys, indent=0))
print("violations_by_impact:", rep.get("violations_by_impact"))
print("views count:", len(rep.get("views", [])) if isinstance(rep.get("views"), list) else "n/a")
for name in ("scans", "audit_count", "view_count"):
    if name in rep:
        print(name, "=", rep[name])
print("privacy_states:", rep.get("privacy_states"))
print("second_factor_states:", rep.get("second_factor_states"))
print("export sizes:", json.dumps(rep.get("export_sizes", rep.get("exports", "")))[:300])

jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
print("ci jobs:", len(jobs), sorted(jobs))
print("migrations:", len(list((ROOT / "db/migrations").glob("*.sql"))))
print("schema files:", len(list((ROOT / "shared/contracts").glob("*.schema.json"))))
print("compose files:", sorted(p.name for p in ROOT.glob("docker-compose*.yml")))
print("acceptance tests:", sorted(p.name for p in (ROOT / "services/acceptance").rglob("test_*.py")))
openapi = json.loads((ROOT / "shared/contracts/openapi-v13.json").read_text())
meth = ("get", "post", "put", "patch", "delete", "head", "options")
print("openapi paths/ops:", len(openapi["paths"]),
      sum(1 for v in openapi["paths"].values() for verb in v if verb in meth))
gate = (ROOT / "tests/unit/test_browser_a11y_gate.py").read_text(encoding="utf-8")
print("browser gate cases:", sum(1 for l in gate.splitlines() if l.startswith("def test_")))
print("reference doc figures cases:",
      sum(1 for l in (ROOT / "tests/unit/test_reference_doc_figures.py").read_text().splitlines()
          if l.startswith("def test_")))
collect = subprocess.run(["pytest", "-q", "--collect-only", "tests/unit"], cwd=ROOT,
                         capture_output=True, text=True)
print("collect tail:", collect.stdout.strip().splitlines()[-1])
