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


# Columns that may keep a value on an erased account. Anything else the schema makes nullable has
# to be NULL once erase_user_identity has run -- the point of deriving the list from
# information_schema rather than keeping it here is that a future ALTER TABLE users ADD COLUMN
# fails this drill instead of quietly escaping it, which is exactly how 015's six authentication
# columns got past a 34-check erasure drill that had just been certified.
RETAINED_NULLABLE = {"erased_at": "the erasure record itself, written by erase_user_identity"}
SENTINELS = {
    "text": lambda c: f"'seed:{c}'",
    "character varying": lambda c: f"'seed:{c}'",
    "timestamp with time zone": lambda c: "'2019-01-02T03:04:05+00:00'",
    "date": lambda c: "'2019-01-02'",
    "bigint": lambda c: "72340",
    "integer": lambda c: "72340",
    "numeric": lambda c: "1",
    "boolean": lambda c: "true",
    "uuid": lambda c: "'00000000-0000-0000-0000-0000000000fe'",
    "json": lambda c: """'{"seed":1}'::json""",
    "jsonb": lambda c: """'{"seed":1}'::jsonb""",
}


def nullable_columns() -> dict[str, str]:
    rows = sql("SELECT column_name||'|'||data_type FROM information_schema.columns "
               "WHERE table_schema='public' AND table_name='users' AND is_nullable='YES'")
    return dict(r.split("|", 1) for r in rows.splitlines() if r)


def row_json(user_id: str) -> dict:
    import json
    return json.loads(sql(f"SELECT row_to_json(u) FROM users u WHERE id='{user_id}'"))


def seed_nullable(user_id: str) -> list[str]:
    """Give every currently-empty nullable column a value, so 'it was NULL after erasure' can only
    mean the eraser cleared it."""
    empty = [name for name, value in row_json(user_id).items() if value is None]
    unseedable = [name for name in empty if nullable_columns()[name] not in SENTINELS]
    if unseedable:
        return unseedable
    if empty:
        assignments = ", ".join(f"{name}={SENTINELS[nullable_columns()[name]](name)}" for name in empty)
        sql(f"UPDATE users SET {assignments} WHERE id='{user_id}'")
    return []


def erasure_violations(after: dict, seeded: dict) -> list[str]:
    """Pure function, so the must-fire control can hand it a row the database would never produce.

    Only nullable columns are judged. A NOT NULL column is already self-policing -- the eraser has
    to write something into it or the UPDATE raises -- and its contract (email, display_name,
    password_hash, status, is_platform_admin) is asserted by name further down. Nullable columns are
    the ones that can quietly keep a value, which is what 015 introduced six of.
    """
    problems = []
    for name, value in after.items():
        if name not in seeded or value is None:
            continue
        if name in RETAINED_NULLABLE:
            if value == seeded[name]:
                problems.append(f"{name} still holds the seeded value, so nothing rewrote it")
            continue
        problems.append(f"{name} still holds {value!r}")
    return problems


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
        # The account record the export hands back, plus what coverage declares, is what the column
        # census is checked against -- so a new column on users fails here rather than going unread.
        declared_user_columns = {entry.split(".", 1)[1] for entry in coverage if entry.startswith("users.")} \
            | set((body.get("account") or {}).keys())

        # Arm a second factor on the probe before erasing it. 015 added six authentication columns
        # to `users` after this drill had already been certified, and neither the export nor the
        # eraser touched them -- which is precisely the drift the census below is built to catch, so
        # it is exercised through the real endpoints rather than by hand-writing values.
        enrol = httpx.post(BASE + "/auth/mfa/enroll", headers=headers, timeout=40)
        seed = enrol.json().get("secret", "") if enrol.status_code == 200 else ""
        confirmed = httpx.post(BASE + "/auth/mfa/enroll/verify", json={"code": e2e_client.totp_code(seed)},
                               headers=headers, timeout=40)
        check("a probe can arm a second factor through the real endpoints",
              enrol.status_code == 200 and confirmed.status_code == 200 and confirmed.json().get("armed") is True,
              f"{enrol.status_code}/{confirmed.status_code} {confirmed.text[:160]}")
        check("and arming does not lock out the session that proved the code",
              httpx.get(BASE + "/account/export", headers=headers, timeout=40).status_code == 200)

        # The subject-access response has to cover every column of the account record, not just the
        # ones that existed when the endpoint was written.
        account_columns = {r for r in sql("SELECT column_name FROM information_schema.columns "
                                          "WHERE table_schema='public' AND table_name='users'").splitlines() if r}
        excluded = set((body.get("excluded") or {}).keys())
        check("every column users has is exported, or declared excluded with a reason",
              not (account_columns - declared_user_columns - excluded),
              f"undeclared: {sorted(account_columns - declared_user_columns - excluded)}")
        check("and an exclusion is only creditable if it says why",
              all(str((body.get("excluded") or {}).get(name) or "").strip() for name in excluded),
              str(body.get("excluded")))
        check("and an exclusion names a column that actually exists, or it excuses nothing",
              not (excluded - account_columns), f"dead exclusions: {sorted(excluded - account_columns)}")

        nullable = nullable_columns()
        check("the nullable census is non-empty, or the erasure guard below would be blind",
              len(nullable) >= 1, str(nullable))
        unseedable = seed_nullable(probe_id)
        check("every nullable users column has a type the drill can seed, or it cannot be judged",
              not unseedable, f"unseedable: {unseedable}")
        armed_row = row_json(probe_id)
        seeded = {name: value for name, value in armed_row.items() if name in nullable and value is not None}
        check("the probe holds a value in every nullable users column before erasure",
              len(seeded) == len(nullable), f"{len(seeded)}/{len(nullable)}: "
                                            f"{sorted(set(nullable) - set(seeded))}")

        expiry_before = scalar(f"SELECT expires_at FROM auth_sessions WHERE user_id='{probe_id}'")
        # The typed email is not proof of presence: anyone inside the session can read it back off
        # /auth/me. The probe armed a real second factor above, so this account owes two credentials
        # at the moment of erasure and not one -- the strength ladder, applied to the most destructive
        # self-service call the platform has.
        codes = e2e_client.Codes(seed, used=int(time.time() // 30))
        bare = httpx.post(BASE + "/account/erasure", json={"confirmation": PROBE_EMAIL}, headers=headers, timeout=40)
        check("erasure is refused while the password has not been re-presented", bare.status_code == 401,
              f"{bare.status_code} {bare.text[:200]}")
        check("the refusal names the credential it is asking for", "password" in bare.text, bare.text[:200])
        check("and the challenge left the account intact",
              scalar(f"SELECT email FROM users WHERE id='{probe_id}'") == PROBE_EMAIL)
        password_only = httpx.post(BASE + "/account/erasure", json={"confirmation": PROBE_EMAIL, "password": "demo-owner"},
                                   headers=headers, timeout=40)
        check("an armed account is also refused until the second factor comes with it",
              password_only.status_code == 401 and "code" in password_only.text,
              f"{password_only.status_code} {password_only.text[:200]}")
        unspent = codes.next()
        wrong = httpx.post(BASE + "/account/erasure",
                           json={"confirmation": PROBE_EMAIL, "password": "not-the-password", "code": unspent},
                           headers=headers, timeout=40)
        check("a wrong password is refused as a failure rather than as a challenge", wrong.status_code == 403,
              f"{wrong.status_code} {wrong.text[:200]}")
        check("which also left the account intact",
              scalar(f"SELECT status FROM users WHERE id='{probe_id}'") == "active")
        check("three refusals are on the record against the account itself",
              scalar(f"SELECT count(*) FROM audit_events WHERE action='auth.step_up.denied' AND actor_id='{probe_id}'") == "3",
              scalar(f"SELECT string_agg(payload->>'reason','|') FROM audit_events "
                     f"WHERE action='auth.step_up.denied' AND subject_id='{probe_id}'"))
        # The password is checked before the code, so the code above is still unspent: `refused` here
        # presents that very same code and only gets its 409 from the confirmation mismatch, which is
        # the proof that a refusal at the first wall did not quietly consume the second factor.
        refused = httpx.post(BASE + "/account/erasure",
                             json={"confirmation": "wrong@example.local", "password": "demo-owner", "code": unspent},
                             headers=headers, timeout=40)
        check("erasure refuses a confirmation that is not the account email", refused.status_code == 409,
              f"{refused.status_code} {refused.text[:200]}")
        check("the refused attempt changed nothing", scalar(f"SELECT email FROM users WHERE id='{probe_id}'") == PROBE_EMAIL)

        erased = httpx.post(BASE + "/account/erasure",
                            json={"confirmation": PROBE_EMAIL, "password": "demo-owner", "code": codes.next()},
                            headers=headers, timeout=40)
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
        # Derived from information_schema, so this is the check that fails when somebody adds a
        # nullable column to users and forgets the eraser. 015 did exactly that, and its six
        # authentication columns -- one of them an openable authenticator seed -- survived a
        # 34-check drill that had already been certified; 016 clears them and this closes the door.
        erased_row = row_json(probe_id)
        violations = erasure_violations(erased_row, seeded)
        check("erasure clears every nullable users column but the declared retention list",
              not violations, "; ".join(violations))
        check("the eraser says it destroyed an armed second factor",
              str(detail.get("second_factor_removed")).lower() == "true", str(detail))
        # The same guard has to be able to fail, so hand it the row it is meant to refuse: a
        # recovery set that was merely unreferenced rather than removed.
        left_behind = dict(erased_row, mfa_recovery=seeded["mfa_recovery"])
        fired = erasure_violations(left_behind, seeded)
        check("and the classifier does report a nullable column the eraser left behind",
              len(fired) == 1 and fired[0].startswith("mfa_recovery "), str(fired))
        retained = erasure_violations(dict(erased_row, erased_at=seeded["erased_at"]), seeded)
        check("and it separates a retained column from an inherited one",
              len(retained) == 1 and retained[0].startswith("erased_at "), str(retained))

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
        orphan = httpx.post(BASE + "/account/erasure",
                            json={"confirmation": SOLE_OWNER_EMAIL, "password": "demo-owner"},
                            headers=sole_headers, timeout=40)
        check("erasing the only owner of a workspace is refused", orphan.status_code == 409 and "only owner" in orphan.text,
              f"{orphan.status_code} {orphan.text[:200]}")
        check("the guard left that account untouched", scalar(f"SELECT status FROM users WHERE id='{sole_id}'") == "active")
        check("the guard revoked nothing", scalar(f"SELECT count(*) FROM auth_sessions WHERE user_id='{sole_id}' AND revoked_at IS NOT NULL") == "0")

        admin_token, admin_ws = e2e_client.login(BASE, "owner@example.local", "demo-owner")
        admin_headers = {"Authorization": f"Bearer {admin_token}", "X-Workspace-Id": admin_ws, "Content-Type": "application/json"}
        platform = httpx.post(BASE + "/account/erasure",
                              json={"confirmation": "owner@example.local", "password": "demo-owner"},
                              headers=admin_headers, timeout=40)
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
