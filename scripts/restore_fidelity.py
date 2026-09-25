#!/usr/bin/env python3
"""Absolute pre/post fingerprints for the backup-restore gate (release gate G9-4).

What exists today cannot catch a wrong-restore: scripts/verify-backup.sh only checks the
tarball's own bytes, and the acceptance rerun after restore asserts ledger *deltas*
(services/acceptance/test_acceptance.py:57, :61, :80), which any uniform drift satisfies.
Nothing compared an asset's audio bytes across the restore at all.

snapshot   record absolute ledger balances per workspace and a sha256 of every asset's
           exported master audio, keyed by asset id.
compare    re-read the same keys and fail on any value that moved.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = "http://localhost:8000"
BASE = ROOT + "/api"
STATE = Path(".restore-fidelity.json")

TENANTS = (("owner@example.local", "demo-owner", "seller"),
           ("viewer@other.local", "demo-viewer", "buyer"))
# Only the seller creates asset_snapshots; a buyer holds deliveries and is fingerprinted
# through its ledger here, so demanding an asset from every tenant would be the wrong
# denominator (and did fail, correctly, on a clean database).
ASSET_OWNERS = {"seller"}


def login(email, password):
    r = httpx.post(BASE + "/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data["workspaces"][0]["id"]


def get(path, token, workspace, binary=False, timeout=90):
    r = httpx.get(BASE + path, headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace}, timeout=timeout)
    r.raise_for_status()
    return r.content if binary else r.json()


def ledger_fingerprint(token, workspace):
    """Absolute balances, not deltas: a restore that silently shifted every account must move this."""
    balances = get("/ledger", token, workspace)["balances"]
    return {k: str(v) for k, v in sorted(balances.items())}


def asset_media_hashes(token, workspace):
    """sha256 of the master audio inside each export, plus the hash the platform recorded."""
    out = {}
    for asset in get("/assets", token, workspace).get("items", []):
        asset_id = asset["id"]
        try:
            export = get(f"/assets/{asset_id}/export", token, workspace, binary=True)
        except httpx.HTTPStatusError as exc:
            # A missing or unreadable object IS the failure mode this gate exists to catch,
            # so report it as a fingerprint difference rather than aborting the run.
            out[asset_id] = {"error": f"export {exc.response.status_code}"}
            continue
        detail = get(f"/assets/{asset_id}", token, workspace)
        archive = zipfile.ZipFile(io.BytesIO(export))
        members = [n for n in archive.namelist() if n.startswith("audio/master.")]
        if len(members) != 1:
            raise SystemExit(f"asset {asset_id}: expected one master member, got {members}")
        out[asset_id] = {
            "audio_sha256": hashlib.sha256(archive.read(members[0])).hexdigest(),
            "snapshot_sha256": hashlib.sha256(
                archive.read("metadata/asset-snapshot.json") + archive.read("metadata/rights-manifest.json")
            ).hexdigest(),
            "recorded_media_hash": detail["asset"].get("media_hash"),
            "rights_version": (detail.get("rights") or {}).get("version"),
        }
    return out


def take():
    state = {}
    for email, password, label in TENANTS:
        token, workspace = login(email, password)
        state[label] = {"workspace": workspace, "ledger": ledger_fingerprint(token, workspace),
                        "assets": asset_media_hashes(token, workspace)}
    for label, entry in state.items():
        broken = [a for a, v in entry["assets"].items() if "error" in v]
        if broken:
            raise SystemExit(f"{label}: cannot fingerprint assets {broken}; fix the stack before snapshotting")
        if not entry["ledger"]:
            raise SystemExit(f"{label}: snapshot has no ledger accounts, so the balance check would prove nothing")
        if label in ASSET_OWNERS and not entry["assets"]:
            raise SystemExit(f"{label}: expected asset fingerprints but the tenant owns no assets")
    total = sum(len(v["assets"]) for v in state.values())
    if total == 0:
        raise SystemExit("snapshot covers zero assets across all tenants; nothing to compare")
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))
    counts = ", ".join(f"{label}={len(v['assets'])} assets/{len(v['ledger'])} accounts"
                       for label, v in sorted(state.items()))
    print(f"restore-fidelity snapshot written: {total} assets across {len(state)} workspaces ({counts})", flush=True)


def compare():
    if not STATE.exists():
        raise SystemExit("no snapshot to compare against; run 'snapshot' before scripts/backup.sh")
    before = json.loads(STATE.read_text())
    problems = []
    for email, password, label in TENANTS:
        token, workspace = login(email, password)
        old = before.get(label)
        if not old:
            problems.append(f"{label}: absent from the snapshot")
            continue
        if old["workspace"] != workspace:
            problems.append(f"{label}: workspace id changed {old['workspace']} -> {workspace}")
        if old["ledger"] != (new_ledger := ledger_fingerprint(token, workspace)):
            problems.append(f"{label}: ledger balances changed {old['ledger']} -> {new_ledger}")
        new_assets = asset_media_hashes(token, workspace)
        for asset_id, expected in old["assets"].items():
            actual = new_assets.get(asset_id)
            if actual is None:
                problems.append(f"{label}/{asset_id}: asset vanished after restore")
                continue
            for field in sorted(set(expected) | set(actual)):
                if expected.get(field) != actual.get(field):
                    problems.append(f"{label}/{asset_id}: {field} changed "
                                    f"{expected.get(field)} -> {actual.get(field)}")
    checked = sum(len(v["assets"]) for v in before.values())
    if checked == 0:
        problems.append("snapshot covered zero assets; nothing was actually compared")
    if problems:
        for line in problems:
            print(f"RESTORE FIDELITY FAIL: {line}", flush=True)
        raise SystemExit(1)
    print(f"restore fidelity passed: {len(before)} workspaces, {checked} assets byte-identical, ledgers unchanged", flush=True)


if __name__ == "__main__":
    {"snapshot": take, "compare": compare}[sys.argv[1]]()
