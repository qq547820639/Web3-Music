#!/usr/bin/env python3
"""Resident drill for subject access export and right to erasure (release gate G10).

The fixture accounts are created by SQL because this deployment has no registration
endpoint, and adding one just to test erasure would be a wider security change than the
feature under test. Everything destructive is aimed at those fixtures; the two guards that
must refuse to touch a real account are asserted and then re-checked by logging into that
account again, so a guard that half-wrote cannot pass as a pass.

Teardown is deliberately asymmetric, because the feature is: the sole-owner probe is fully
deleted, while the erased probe is left in place. Its pseudonymous provenance rows are
append-only by schema design (001_production_candidate.sql:468 and friends refuse DELETE
even to a superuser), so "the trail survives erasure" is asserted here twice — once by
counting the rows after erasure, once by trying to delete them and requiring the database to
say no. Run the drill on a fresh stack for release evidence; on a reused stack each run adds
one erased account.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import e2e_client  # noqa: E402

BASE = os.getenv("API_BASE_URL", "http://localhost:8000") + "/api"
STAMP = time.strftime("%H%M%S", time.gmtime()) + uuid.uuid4().hex[:4]
PROBE_EMAIL = f"erasure-probe-{STAMP}@example.local"
SOLE_OWNER_EMAIL = f"sole-owner-probe-{STAMP}@example.local"
MARKER = f"Erasure probe {STAMP}"
checks: list[tuple[str, bool, str]] = []


def sql(statement: str) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin", "-d", "music",
                          "-tAc", " ".join(statement.split())], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def scalar(statement: str) -> str:
    """psql -c echoes the command tag after a RETURNING value; only the first line is data."""
    return sql(statement).splitlines()[0].strip()


def attempt(statement: str) -> str | None:
    """Run a statement that may be refused; return None on success or the database's error."""
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin", "-d", "music",
                          "-tAc", " ".join(statement.split())], capture_output=True, text=True)
    return None if out.returncode == 0 else (out.stderr.strip().splitlines() or ["unknown"])[0][:200]


def check(name: str, passed: bool, detail: str = ""):
    checks.append((name, bool(passed), detail))
    print(f"  {'OK  ' if passed else 'FAIL'} {name}" + ("" if passed else f" — {detail}"))


def make_user(email: str, display: str) -> str:
    return scalar(f"""INSERT INTO users(email,display_name,password_hash,is_platform_admin)
      SELECT '{email}','{display}',password_hash,false FROM users WHERE email='owner@example.local'
      RETURNING id""")


def users_key_census() -> set[str]:
    """Every (table.column) in the live schema that points at a person, from the constraints."""
    rows = sql("""SELECT kcu.table_name||'.'||kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name=tc.constraint_name AND kcu.table_name=tc.table_name
        JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name
        WHERE tc.constraint_type='FOREIGN KEY' AND ccu.table_name='users' AND ccu.column_name='id'""")
    return {r for r in rows.splitlines() if r}


def declared_columns() -> set[str]:
    """Every table.column that actually exists, so coverage cannot name a dead store."""
    rows = sql("SELECT table_name||'.'||column_name FROM information_schema.columns WHERE table_schema='public'")
    return {r for r in rows.splitlines() if r}


def main() -> int:
    owner_ws = scalar("SELECT m.workspace_id FROM workspace_members m JOIN users u ON u.id=m.user_id "
                      "WHERE u.email='owner@example.local' LIMIT 1")
    probe_id = make_user(PROBE_EMAIL, "Erasure Probe")
    sole_id = None
    sole_ws = None
    try:
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{owner_ws}','{probe_id}','creator')")
        sql(f"INSERT INTO user_preferences(workspace_id,user_id,scope,preferences) VALUES ('{owner_ws}','{probe_id}','global','{{\"probe\":true}}')")

        token, workspace = e2e_client.login(BASE, PROBE_EMAIL, "demo-owner")
        headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace, "Content-Type": "application/json"}
        check("fixture lands in the tenant it was added to", workspace == owner_ws, f"{workspace} != {owner_ws}")
        created = httpx.post(BASE + "/projects", json={"title": MARKER}, headers=headers, timeout=40)
        check("fixture can create a project", created.status_code == 201, f"{created.status_code} {created.text[:200]}")
        # POST /api/events (routers/creation.py:211) is the only writer product_events ever has,
        # and no other suite calls it — so the table sits empty and the export's events key is a
        # path nothing exercises. One event here makes that read real, and it is the same
        # FORCE-RLS read shape as preferences, which is how that defect was found.
        posted = httpx.post(BASE + "/events", headers=headers, timeout=40,
                            json={"event_name": "candidate_played", "project_id": created.json()["id"],
                                  "properties": {"probe": STAMP}})
        check("fixture can record a product event", posted.status_code == 202, f"{posted.status_code} {posted.text[:200]}")

        export = httpx.get(BASE + "/account/export", headers=headers, timeout=60)
        body = export.json() if export.status_code == 200 else {}
        records = body.get("records") or {}
        markers = [row.get("title") for ws_rows in records.values()
                   for row in ws_rows.get("song_projects.created_by", [])]
        check("export answers 200", export.status_code == 200, f"{export.status_code} {export.text[:300]}")
        check("export names the requesting account", (body.get("account") or {}).get("email") == PROBE_EMAIL)
        check("export carries the marker project", MARKER in markers, f"titles seen: {markers}")
        check("export is limited to the caller's own workspaces", set(records) <= {workspace}, f"{set(records)}")
        check("export lists the caller's sessions", len(body.get("sessions") or []) >= 1, str(body.get("sessions")))
        check("export lists the caller's stored preferences", len(body.get("preferences") or []) == 1, str(body.get("preferences")))
        check("export lists the caller's product events",
              [e.get("event_name") for e in body.get("events") or []] == ["candidate_played"], str(body.get("events")))

        # A subject access response has to state its own scope: coverage lists every store the
        # endpoint reads, and the schema is the authority on which stores hold personal keys.
        coverage = set(body.get("coverage") or [])
        census = users_key_census()
        check("export coverage covers every user key the schema has", not (census - coverage),
              f"missing: {sorted(census - coverage)}")
        check("every store the export claims exists in the schema", not (coverage - declared_columns()),
              f"dead entries: {sorted(coverage - declared_columns())}")

        expiry_before = scalar(f"SELECT expires_at FROM auth_sessions WHERE user_id='{probe_id}'")
        refused = httpx.post(BASE + "/account/erasure", json={"confirmation": "wrong@example.local"}, headers=headers, timeout=40)
        check("erasure refuses a confirmation that is not the account email", refused.status_code == 409,
              f"{refused.status_code} {refused.text[:200]}")
        check("the refused attempt changed nothing", scalar(f"SELECT email FROM users WHERE id='{probe_id}'") == PROBE_EMAIL)

        erased = httpx.post(BASE + "/account/erasure", json={"confirmation": PROBE_EMAIL}, headers=headers, timeout=40)
        payload = erased.json() if erased.status_code == 200 else {}
        detail = payload.get("detail") if isinstance(payload, dict) else {}
        check("erasure succeeds with the matching confirmation", erased.status_code == 200,
              f"{erased.status_code} {erased.text[:200]}")
        check("erasure revoked the live session", int(detail.get("sessions_revoked") or 0) >= 1, str(detail))
        check("erasure removed the membership", int(detail.get("memberships_removed") or 0) >= 1, str(detail))
        check("erasure removed the stored preferences", int(detail.get("preferences_removed") or 0) == 1, str(detail))

        after = httpx.get(BASE + "/account/export", headers=headers, timeout=40)
        check("the erased account's token no longer reads anything", after.status_code == 401, f"{after.status_code}")
        again = httpx.post(BASE + "/auth/login", json={"email": PROBE_EMAIL, "password": "demo-owner"}, timeout=40)
        check("the erased account cannot sign in again", again.status_code == 401, f"{again.status_code} {again.text[:160]}")

        email, display_name, status = scalar(f"SELECT email||'|'||display_name||'|'||status FROM users WHERE id='{probe_id}'").split("|")
        check("identity columns are anonymised",
              email == f"erased-{probe_id}@invalid.invalid" and display_name == "已删除用户" and status == "erased",
              email + "|" + display_name + "|" + status)
        check("memberships are gone from the database",
              scalar(f"SELECT count(*) FROM workspace_members WHERE user_id='{probe_id}'") == "0")
        check("preferences are gone from the database",
              scalar(f"SELECT count(*) FROM user_preferences WHERE user_id='{probe_id}'") == "0")
        # Both halves in one assertion: the revocation must have landed, and it must have landed
        # without rewriting expires_at, which 005_final_release.sql:44-54 makes immutable and which
        # 013 tried to shorten — that is what made every erasure fail with 409.
        session_state = scalar(f"SELECT coalesce(revoke_reason,'-')||'|'||expires_at::text||'|'||(revoked_at IS NOT NULL)::text "
                               f"FROM auth_sessions WHERE user_id='{probe_id}'")
        check("the session is revoked without its immutable expiry moving",
              session_state == f"account erased|{expiry_before}|true",
              f"{session_state} vs account erased|{expiry_before}|true")
        check("provenance rows survive under the pseudonymous actor id",
              scalar(f"SELECT count(*) FROM song_projects WHERE created_by='{probe_id}'") == "1")
        check("the erasure itself is auditable",
              scalar(f"SELECT count(*) FROM audit_events WHERE action='privacy.account.erase' AND actor_id='{probe_id}'") == "1")
        second = scalar(f"SELECT erase_user_identity('{probe_id}'::uuid,'{email}')").replace(" ", "")
        check("re-running erasure is a no-op", '"already_erased":true' in second, second[:200])

        # The residue is only real if nothing downstream can scrub it, so ask the database.
        error = attempt(f"DELETE FROM song_projects WHERE created_by='{probe_id}'")
        check("the surviving provenance is append-only, not merely unreferenced",
              error is not None and "immutable" in error, f"delete was accepted: {error}")

        sole_ws = scalar("INSERT INTO workspaces(name,plan) VALUES ('Sole Owner Probe','trial') RETURNING id")
        sole_id = make_user(SOLE_OWNER_EMAIL, "Sole Owner Probe")
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{sole_ws}','{sole_id}','owner')")
        sole_token, sole_workspace = e2e_client.login(BASE, SOLE_OWNER_EMAIL, "demo-owner")
        sole_headers = {"Authorization": f"Bearer {sole_token}", "X-Workspace-Id": sole_workspace, "Content-Type": "application/json"}
        orphan = httpx.post(BASE + "/account/erasure", json={"confirmation": SOLE_OWNER_EMAIL}, headers=sole_headers, timeout=40)
        check("erasing the only owner of a workspace is refused", orphan.status_code == 409 and "only owner" in orphan.text,
              f"{orphan.status_code} {orphan.text[:200]}")
        check("the guard left that account untouched", scalar(f"SELECT status FROM users WHERE id='{sole_id}'") == "active")
        check("the guard revoked nothing", scalar(f"SELECT count(*) FROM auth_sessions WHERE user_id='{sole_id}' AND revoked_at IS NOT NULL") == "0")

        admin_token, admin_ws = e2e_client.login(BASE, "owner@example.local", "demo-owner")
        admin_headers = {"Authorization": f"Bearer {admin_token}", "X-Workspace-Id": admin_ws, "Content-Type": "application/json"}
        platform = httpx.post(BASE + "/account/erasure", json={"confirmation": "owner@example.local"}, headers=admin_headers, timeout=40)
        check("a platform administrator cannot self-erase", platform.status_code == 409 and "platform administrator" in platform.text,
              f"{platform.status_code} {platform.text[:200]}")
        survivor = httpx.post(BASE + "/auth/login", json={"email": "owner@example.local", "password": "demo-owner"}, timeout=40)
        check("the demo owner still works after those refusals", survivor.status_code == 200, f"{survivor.status_code}")
        check("the demo owner kept its membership",
              scalar("SELECT count(*) FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE u.email='owner@example.local'") == "1")
    finally:
        if sole_id:
            attempt(f"DELETE FROM user_preferences WHERE user_id='{sole_id}'")
            attempt(f"DELETE FROM auth_sessions WHERE user_id='{sole_id}'")
            attempt(f"DELETE FROM workspace_members WHERE user_id='{sole_id}'")
            attempt(f"DELETE FROM users WHERE id='{sole_id}'")
        if sole_ws:
            attempt(f"DELETE FROM workspaces WHERE id='{sole_ws}'")
        ids = ",".join(f"'{i}'" for i in (probe_id, sole_id) if i)
        left = scalar(f"SELECT count(*) FROM users WHERE id IN ({ids})")
        projects = scalar(f"SELECT count(*) FROM song_projects WHERE created_by='{probe_id}'")
        print(f"  teardown: sole-owner probe deleted; erased probe kept as designed residue "
              f"(accounts still present: {left}, provenance projects still present: {projects})")

    failed = [name for name, passed, _ in checks if not passed]
    print(f"\nerasure drill: {len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
