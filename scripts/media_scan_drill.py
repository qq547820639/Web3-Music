#!/usr/bin/env python3
"""Resident drill for the media-scan boundary (release checklist「病毒扫描」那一半).

What is proved here is not "an antivirus ran". On this host a real engine is not available to the
reference stack -- measured: the Docker VM is 4 vCPU / 5.77 GiB (`docker info` MemTotal 6198632448)
while ClamAV's own documentation states a 3 GiB+ minimum, and `docker pull clamav/clamav:1.4` answers
`no matching manifest for linux/arm64/v8` on this arm64 host. What ships is therefore the
*boundary*, and what this drill proves is that the boundary has teeth in the three places a lie could
still get through:

  * the database refuses a 'clean' row that cannot name the engine and the moment (so no code path,
    including this one, can write the literal again -- that is how the 020 defect was born);
  * the narrowed domain refuses 'rejected'/'pending' rows the table cannot honestly represent;
  * the serving path still works for an asset truthfully recorded as `unscanned`, and still refuses a
    forged token -- i.e. removing the decorative `scan_status != 'clean'` condition did not widen access.

The protocol half (what an engine's answer means) is proven against a spec-faithful double in
tests/unit/test_media_scan_seam.py, because a unit test must not need a 3 GiB daemon to exist.

Every destructive probe runs in BEGIN...ROLLBACK: the demo roster and `media_assets` come out of this
drill exactly as they went in, and the last check counts that nothing was left behind.
"""
from __future__ import annotations

import os
import subprocess
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_client  # noqa: E402

ORIGIN = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
BASE = ORIGIN + "/api"
PROBE_SHA = "f" * 64
checks: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = ""):
    checks.append((name, bool(passed), detail))


def sql(statement: str) -> str:
    """One psql trip as the migration role. Errors come back as text, not exceptions: half of this
    drill's assertions are about statements that must fail."""
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin",
                          "-d", "music", "-tAc", " ".join(statement.split())],
                         capture_output=True, text=True)
    return ((out.stdout or "") + (out.stderr or "")).strip()


def probe_insert(workspace: str, status: str, engine: str = "", detail: str = "", when: str = "") -> str:
    """An asset row with the chosen status and the chosen provenance, inside a rolled-back transaction.

    `when` is passed as the raw expression `now()` because a timestamp is not a string literal.
    """
    columns = {"scan_engine": engine, "scan_detail": detail, "scanned_at": when}
    named = {key: value for key, value in columns.items() if value}
    values = [value if key == "scanned_at" else f"'{value}'" for key, value in named.items()]
    return sql("BEGIN; INSERT INTO media_assets"
               f"(workspace_id,kind,bucket,object_key,sha256,mime_type,bytes,scan_status"
               f"{''.join(',' + k for k in named)})"
               f" VALUES ('{workspace}','audio','probe-bucket','probe/media-scan','{PROBE_SHA}','audio/wav',1,"
               f"'{status}'{''.join(',' + v for v in values)}); ROLLBACK;")


def main() -> int:
    reported = subprocess.run(["docker", "compose", "exec", "-T", "worker",
                               "printenv", "MEDIA_SCAN_ENDPOINT"], capture_output=True, text=True)
    # `printenv` exits 1 for an unset variable, which is a legitimate answer here: this deployment
    # simply has no engine. Anything else (a missing container, a broken exec) is not.
    check("the worker can be asked what scan endpoint it runs with",
          reported.returncode in (0, 1), f"rc={reported.returncode} err={(reported.stderr or '').strip()[:80]}")
    endpoint = (reported.stdout or "").strip()

    census = sql("SELECT coalesce(string_agg(DISTINCT scan_status, ',' ORDER BY scan_status),'none'),"
                 " count(*),"
                 " count(*) FILTER (WHERE scan_status='clean' AND (scan_engine IS NULL OR scanned_at IS NULL)),"
                 " count(*) FILTER (WHERE scan_status='unscanned' AND (scan_engine IS NOT NULL"
                 " OR scanned_at IS NOT NULL OR scan_detail IS NOT NULL)) FROM media_assets;")
    fields = census.split("|")
    statuses, rows = (fields + ["none", "0"])[:2]
    check("assets carry only the two states 020 allows, and there is something to look at",
          int(rows or 0) > 0 and set(statuses.split(",")) <= {"clean", "unscanned"},
          f"statuses={statuses} rows={rows}")
    check("no 'clean' asset is missing the engine and moment it claims to have",
          len(fields) > 2 and fields[2] == "0", f"unproven clean rows={fields[2:3]}")
    check("no 'unscanned' asset smuggles scan provenance",
          len(fields) > 3 and fields[3] == "0", f"contradictory rows={fields[3:4]}")
    if not endpoint:
        check("with no engine configured, nothing in the table claims to have been scanned",
              "clean" not in statuses.split(","), f"statuses={statuses}")

    token, workspace = e2e_client.login(BASE, "owner@example.local", "demo-owner")

    refused = [
        ("a 'clean' row that names no engine", probe_insert(workspace, "clean"), "must name the engine"),
        ("a 'clean' row with no scan moment", probe_insert(workspace, "clean", engine="clamd 1.4"),
         "must say when"),
        ("a 'rejected' asset row, which this table cannot represent", probe_insert(workspace, "rejected"),
         "media_assets_scan_status_check"),
        ("an 'unscanned' row that still carries an engine", probe_insert(workspace, "unscanned",
                                                                          engine="clamd 1.4"),
         "cannot carry scan provenance"),
    ]
    for name, statement, needle in refused:
        output = statement
        check(name + " -- the database refuses it", "ERROR" in output and needle in output,
              output[:180].replace("\n", " "))
    accepted = probe_insert(workspace, "clean", engine="clamd 1.4.2/27830", detail="stream(1) OK",
                            when="now()")
    check("the same row with a verdict, an engine and a moment is accepted",
          "ERROR" not in accepted and "INSERT 0 1" in accepted, accepted[:180].replace("\n", " "))
    check("the rolled-back probes left no asset behind",
          sql(f"SELECT count(*) FROM media_assets WHERE sha256='{PROBE_SHA}';") == "0",
          sql(f"SELECT count(*) FROM media_assets WHERE sha256='{PROBE_SHA}';"))

    ready = sql("SELECT c.id FROM audio_candidates c JOIN media_assets m ON m.id=c.media_asset_id"
                f" WHERE c.workspace_id='{workspace}' AND c.status='ready'"
                " ORDER BY c.created_at DESC LIMIT 1;")
    if not ready:
        check("the demo workspace has a ready candidate to serve", False, "no ready candidate found")
        return finish()
    issued = httpx.post(BASE + f"/candidates/{ready}/media-token",
                        headers=e2e_client.headers(token, workspace), timeout=30)
    url = issued.json().get("url") if issued.status_code == 200 else None
    check("a ready candidate yields a media url", issued.status_code == 200 and bool(url),
          f"{issued.status_code} {issued.text[:140]}")
    if url:
        fetched = httpx.get(ORIGIN + url, timeout=60)
        check("the asset is served even though this deployment records it unscanned",
              fetched.status_code == 200 and len(fetched.content) > 0,
              f"{fetched.status_code} {len(fetched.content)} bytes")
    forged = httpx.get(ORIGIN + f"/media/{ready}?token=not-a-real-token", timeout=30)
    check("a forged token is still refused", forged.status_code in {401, 404},
          f"{forged.status_code} {forged.text[:80]}")
    return finish()


def finish() -> int:
    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        if not ok:
            print(f"  FAIL {name} :: {detail}")
    print(f"media scan drill: {passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
