#!/usr/bin/env python3
"""Resident drill for the public rights intake (migration 021) against the running stack.

What this is: the one write path in this platform that a caller can reach with no account, no session
and no workspace. So the assertions are about the three things that make that safe rather than about
happy-path acceptance -- (1) it cannot be used to ask whether an object exists, (2) the person who
files never ends up visible to the party they file against, and (3) the notice is evidence, not a
ticket: nothing rewrites it afterwards.

Each of those is measured on both polarities against the live server and the live database, and the
table's own immutability is tested as the owner role (music_admin is superuser, so a GRANT-only claim
would be worth nothing -- see the header of db/migrations/021_public_rights_report.sql).

Fixtures are provisioned by SQL because this deployment has no registration endpoint, and they are
torn down with `session_replication_role = replica`: the report table refuses DELETE by trigger, which
is the property under test, so the only honest cleanup is the one that says it disabled a trigger to
do it. Anything the teardown could not remove is printed rather than hidden.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx
import metric_line as metrics
from window_rules import MIN_TRAFFIC_SPAN, refusals_count_down

BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000") + "/api"
STAMP = time.strftime("%m%dT%H%M%SZ", time.gmtime())
OWNER = os.getenv("E2E_EMAIL", "owner@example.local")
PASSWORD = os.getenv("E2E_PASSWORD", "demo-owner")
WS_NAME = f"report-drill-{STAMP}"
checks: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = ""):
    checks.append((name, bool(passed), detail))
    print(f"  {'OK  ' if passed else 'FAIL'} {name}" + ("" if passed else f" — {detail}"))


TAG_LINE = re.compile(r"(INSERT \d+ \d+|UPDATE \d+|DELETE \d+|SELECT \d+|SET|BEGIN|COMMIT|ROLLBACK|"
                      r"RESET|GRANT|REVOKE|SAVEPOINT \S+|RELEASE \S+)")


def psql_argv(statement: str, db: str = "music", guc: str | None = None) -> list[str]:
    """One shape for reaching the database, so sql(), run() and sql_err() cannot drift apart."""
    argv = ["docker", "compose", "exec", "-T"]
    if guc:
        argv += ["-e", f"PGOPTIONS={guc}"]
    argv += ["postgres", "psql", "-U", "music_admin", "-d", db, "-tAc", " ".join(statement.split())]
    return argv


def sql(statement: str, db: str = "music", guc: str | None = None) -> str:
    """One statement, one connection, one value back.

    Measured on this stack rather than assumed: psql prints a command tag for a data-modifying
    statement even in tuples-only mode, so a lone `INSERT ... RETURNING id` answers with its row and
    then `INSERT 0 1`, while `SET a; SET b; SELECT 42` answers `SET`, `SET`, `42`. Reading the first
    line suits the first shape and not the second -- which is how the word "SET" once travelled into a
    teardown line in place of a count. So the rule is: anything given to sql() ends in a top-level
    SELECT, whose tag never follows its row; bare writes go through run(); and a first line that *is* a
    tag raises rather than being reported as a value.

    `guc` asks for a session-only setting (the teardown's `session_replication_role`, nothing else) via
    PGOPTIONS, so no extra statement is needed to carry it -- and since every call here is its own
    connection, the setting cannot outlive the call that asked for it.
    """
    out = subprocess.run(psql_argv(statement, db, guc), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    lines = [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
    if not lines:
        return ""
    if TAG_LINE.fullmatch(lines[0]):
        raise SystemExit(f"psql handed back the command tag {lines[0]!r} instead of a value for: "
                         f"{statement[:120]}")
    return lines[0]


def run(statement: str, guc: str | None = None) -> None:
    """Do a statement and discard its output, because it has none worth keeping.

    A bare `DELETE FROM ...` with no RETURNING answers with the command tag alone, which sql() refuses
    on purpose -- so the cleanup statements come through here, where "no value" is the point, while a
    real failure still raises with the statement that caused it.
    """
    out = subprocess.run(psql_argv(statement, guc=guc), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"teardown statement failed: {out.stderr.strip()[:300]}\n  {statement[:160]}")


def sql_err(statement: str) -> str:
    """Run a statement expected to be refused, and hand back the database's own words."""
    out = subprocess.run(psql_argv(statement), capture_output=True, text=True)
    return (out.stderr or "").strip()[:220]


def redis_cli(*arguments: str) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "redis", "redis-cli", *arguments],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"redis-cli failed: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def deployed_per_address_key(email: str) -> str:
    """Ask the running API which Redis key it counts this address under.

    Re-deriving the key in this file is how the measurement drifts away from the thing it measures: the
    first version hashed the fixture's own spelling while the endpoint hashes the lower-cased one, so it
    read a key the server never wrote and reported a filled window as an absent one. The service states
    its own key; an unreadable answer comes back as text that cannot match, so the next check goes red
    instead of the window being measured against the wrong name.
    """
    code = "import sys; from app.main import _report_keys; print(_report_keys(sys.argv[1])[0])"
    out = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code, email],
                         capture_output=True, text=True)
    lines = (out.stdout or "").strip().splitlines()
    return lines[-1].strip() if lines else f"<unreadable: {(out.stderr or '').strip()[:160]}>"


def sign_in(email: str, password: str = PASSWORD) -> str:
    """A bearer token for an existing demo account, refusing a second factor out loud."""
    for _ in range(3):
        r = httpx.post(BASE + "/auth/login", json={"email": email, "password": password}, timeout=40)
        if r.status_code == 200:
            return r.json()["access_token"]
        if r.status_code == 429:
            time.sleep(61)
            continue
        raise SystemExit(f"login for {email} returned {r.status_code}: {r.text[:200]}")
    raise SystemExit(f"login for {email} kept being throttled")


def file(payload: dict) -> httpx.Response:
    return httpx.post(BASE + "/reports", json=payload, timeout=40)


def notice(email: str, subject_id: str, subject_type: str = "asset") -> dict:
    return {"subject_type": subject_type, "subject_id": subject_id,
            "work_identification": f"A Wayfaring Song (c) {STAMP}",
            "location": f"https://example.test/work/{subject_id}",
            "grounds": "copyright", "reporter_name": "Drill Reporter",
            "reporter_email": email, "attest_good_faith": True, "attest_accuracy": True}


def main() -> int:
    # A workspace of its own, with the demo owner added to it: the case is restricted through
    # /api/moderation/cases/{id}, which resolves the caller's membership, so the fixture has to be a
    # workspace the owner belongs to -- and it must not be one of the demo ones, because this drill's
    # teardown deletes the workspace's rows and a shared workspace would take real media with it.
    ws = sql(f"INSERT INTO workspaces(name,plan) VALUES ('{WS_NAME}','standard') RETURNING id")
    owner_id = sql(f"SELECT id FROM users WHERE email='{OWNER}'")
    run(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{ws}','{owner_id}','owner')")
    asset = sql(f"INSERT INTO media_assets(workspace_id,kind,bucket,object_key,sha256,mime_type,bytes,scan_status)"
                f" VALUES ('{ws}','master','drill-bucket','{WS_NAME}.wav','{'d'*64}','audio/wav',1024,'unscanned') RETURNING id")
    outsider_ws = sql(f"INSERT INTO workspaces(name,plan) VALUES ('{WS_NAME}','standard') RETURNING id")
    outsider = sql(f"INSERT INTO users(email,display_name,password_hash,is_platform_admin)"
                   f" VALUES ('report-outsider-{STAMP}@example.local','Outsider',"
                   f"(SELECT password_hash FROM users WHERE email='{OWNER}'),false) RETURNING id")
    # The role name is read out of the database's own CHECK rather than typed here, because restating
    # that vocabulary is how 017's list and the interface's list drifted apart once already.
    role = sql("SELECT (regexp_matches(pg_get_constraintdef(oid),'''([a-z_]+)''','g'))[1] "
               "FROM pg_constraint WHERE conname='workspace_members_role_check' "
               "AND conrelid='workspace_members'::regclass OFFSET 1 LIMIT 1")
    check("a non-owner role name is derived from the constraint, not hardcoded", role not in ("", "owner"),
          f"derived role: {role!r}")
    run(f"INSERT INTO workspace_members(workspace_id,user_id,role) "
        f"VALUES ('{outsider_ws}','{outsider}','{role}')")
    token = sign_in(OWNER)
    print(f"  fixtures: workspace {ws[:8]} asset {asset[:8]} outsider {outsider[:8]}")

    try:
        # ---- the two branches must be indistinguishable from outside ----
        real = file(notice(f"real-{STAMP}@example.local", asset))
        ghost_id = str(uuid.uuid4())
        ghost = file(notice(f"ghost-{STAMP}@example.local", ghost_id))
        junk = file(notice(f"junk-{STAMP}@example.local", "definitely-not-a-uuid"))
        check("filing against a real asset is accepted", real.status_code == 201, f"{real.status_code} {real.text[:160]}")
        check("filing against an unknown id is accepted too, not refused", ghost.status_code == 201,
              f"{ghost.status_code} {ghost.text[:160]}")
        check("and a malformed id is accepted as well (a notice is not validated against a catalogue)",
              junk.status_code == 201, f"{junk.status_code} {junk.text[:160]}")
        shapes = {tuple(sorted(json.loads(r.text))) for r in (real, ghost, junk)}
        check("all three responses carry exactly the same keys", len(shapes) == 1,
              f"key sets seen: {shapes}")
        statuses = {json.loads(r.text)["status"] for r in (real, ghost, junk)}
        check("and the same status word, so none of them answers 'does this exist'",
              statuses == {"received"}, f"statuses: {statuses}")
        # Blank only the fields that are unique by construction (a reference, a timestamp, a fresh
        # receipt); everything left must be identical, or the response is telling the filer something.
        blank = ("reference", "received_at", "receipt")
        norm = [{k: ("" if k in blank else v) for k, v in json.loads(r.text).items()} for r in (real, ghost, junk)]
        check("the three bodies differ only in the fields that must be unique to a filing",
              norm[0] == norm[1] == norm[2], json.dumps(norm)[:300])

        # Scoped to this run's own filing rather than to every `opened_by LIKE 'report:%'` row: a
        # stack-wide census here would read an aborted earlier run's leftover as this one opening two
        # cases, which is the wrong verdict to hand a fresh chain run.
        opened = sql(f"SELECT count(*) FROM moderation_cases WHERE opened_by IN "
                     f"(SELECT 'report:' || id::text FROM rights_reports "
                     f"WHERE reporter_email = lower('real-{STAMP}@example.local'))")
        # The intake stores the address folded to lower case (021 does lower(btrim(...))), which this
        # fixture's stamp would otherwise miss -- so the fold is asserted rather than assumed.
        check("the stored address is normalised, so a filing cannot be duplicated by capitalisation",
              sql(f"SELECT count(*) FROM rights_reports WHERE reporter_email = "
                  f"'real-{STAMP}@example.local'") == "0"
              and sql(f"SELECT count(*) FROM rights_reports WHERE reporter_email = "
                      f"lower('real-{STAMP}@example.local')") == "1")
        case_for_real = sql(f"SELECT coalesce((SELECT case_id::text FROM rights_reports "
                            f"WHERE reporter_email=lower('real-{STAMP}@example.local')),'')")
        case_for_ghost = sql(f"SELECT coalesce((SELECT case_id::text FROM rights_reports "
                             f"WHERE reporter_email=lower('ghost-{STAMP}@example.local')),'')")
        check("the resolvable filing opened exactly one case", case_for_real != "" and int(opened) == 1,
              f"case_id {case_for_real[:8]!r}, cases opened by reports: {opened}")
        check("the unresolvable ones opened none", case_for_ghost == "", f"case_id read {case_for_ghost!r}")
        leak = sql(f"SELECT count(*) FROM moderation_cases WHERE id='{case_for_real}'::uuid "
                   f"AND evidence::text ILIKE lower('%real-{STAMP}@example.local%')")
        check("the case a tenant can list carries no reporter address", leak == "0",
              f"evidence rows containing the address: {leak}")
        shown = sql(f"SELECT evidence->>'work_identification' FROM moderation_cases WHERE id='{case_for_real}'::uuid")
        check("and the case does carry what the notice is about", shown.startswith("A Wayfaring Song"),
              f"evidence.work_identification: {shown!r}")

        # ---- the two statements are the notice ----
        no_attest = file(notice(f"noattest-{STAMP}@example.local", asset) | {"attest_good_faith": False})
        check("a submission without both statements is refused", no_attest.status_code == 422,
              f"{no_attest.status_code} {no_attest.text[:160]}")
        check("and it wrote nothing", sql(f"SELECT count(*) FROM rights_reports "
                                         f"WHERE reporter_email=lower('noattest-{STAMP}@example.local')") == "0")
        bad_type = file(notice(f"badtype-{STAMP}@example.local", asset) | {"subject_type": "spaceship"})
        check("an unknown subject type is refused with the allowed list", bad_type.status_code == 422
              and "unsupported_subject_type" in bad_type.text, f"{bad_type.status_code} {bad_type.text[:160]}")
        bad_mail = file(notice(f"badmail-{STAMP}@example.local", asset) | {"reporter_email": "not-an-address"})
        check("an unusable contact address is refused (a notice nobody can answer is not a notice)",
              bad_mail.status_code == 422, f"{bad_mail.status_code} {bad_mail.text[:120]}")
        # The field rules are written twice on purpose -- the request model for the message, the CHECK
        # constraints as the authority -- and these four bodies are exactly where the two disagree:
        # `min_length=3` counts the spaces, `length(btrim(...))` does not. Each of these arrived as a
        # 500 with a constraint name in the server log until the intake learned to report the
        # database's own refusal; the assertion requires the constraint's name to come back, so it also
        # says *which* rule answered rather than accepting any 422.
        for field, constraint in (("subject_id", "report_subject_id_shape"),
                                  ("work_identification", "report_work_identification_present"),
                                  ("location", "report_location_present"),
                                  ("reporter_name", "report_name_shape")):
            blank = file(notice(f"blank-{field}-{STAMP}@example.local", asset) | {field: "   "})
            check(f"a {field} that is blank once trimmed is refused as a body problem, not a fault",
                  blank.status_code == 422 and constraint in blank.text,
                  f"{blank.status_code} {blank.text[:200]}")
            check(f"and it wrote nothing",
                  sql(f"SELECT count(*) FROM rights_reports WHERE reporter_email="
                      f"lower('blank-{field}-{STAMP}@example.local')") == "0")

        # ---- the receipt is the only way back in ----
        receipt = real.json()["receipt"]
        st = httpx.post(BASE + "/reports/status", json={"receipt": receipt}, timeout=40)
        check("the receipt returns the filing", st.status_code == 200
              and st.json()["reference"] == real.json()["reference"], f"{st.status_code} {st.text[:160]}")
        check("while a case is merely open, the reporter is told 'received' and nothing more",
              st.json()["status"] == "received" and not st.json()["restrictions"], f"{st.text[:200]}")
        bogus = httpx.post(BASE + "/reports/status", json={"receipt": "x" * 40}, timeout=40)
        check("a receipt that is not one gets 404, not a listing", bogus.status_code == 404,
              f"{bogus.status_code} {bogus.text[:120]}")

        # ---- a human decision becomes a statement of reasons ----
        put = httpx.put(f"{BASE}/moderation/cases/{case_for_real}",
                        json={"status": "restricted", "legal_hold": True,
                              "restrictions": {"download": "blocked", "licence": "frozen"}},
                        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
        check("staff can restrict the case the notice opened", put.status_code == 200,
              f"{put.status_code} {put.text[:200]}")
        st2 = httpx.post(BASE + "/reports/status", json={"receipt": receipt}, timeout=40)
        check("the reporter is then told the outcome and the restrictions behind it",
              st2.status_code == 200 and st2.json()["status"] == "restricted"
              and st2.json()["restrictions"].get("download") == "blocked", f"{st2.text[:240]}")

        # ---- the queue, and who may see it ----
        queue = httpx.get(BASE + "/moderation/reports", headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
        refs = [r["reference"] for r in queue.json().get("reports", [])] if queue.status_code == 200 else []
        check("the platform queue lists every filing, including the ones that named nothing",
              queue.status_code == 200 and real.json()["reference"] in refs
              and ghost.json()["reference"] in refs, f"{queue.status_code} {len(refs)} rows")
        check("and it is the queue that carries the address, with the case link beside it",
              any(r.get("reporter_email") == f"ghost-{STAMP}@example.local".lower() and r.get("case_id") is None
                  for r in queue.json().get("reports", [])),
              json.dumps([r for r in queue.json().get("reports", []) if r.get("case_id") is None])[:220])
        outsider_token = sign_in(f"report-outsider-{STAMP}@example.local")
        denied = httpx.get(BASE + "/moderation/reports",
                           headers={"Authorization": f"Bearer {outsider_token}",
                                    "X-Workspace-Id": outsider_ws}, timeout=40)
        check("a workspace administrator who is not a platform administrator sees nothing",
              denied.status_code == 403, f"{denied.status_code} {denied.text[:160]}")

        # ---- link the unresolvable notice to a case, once ----
        ghost_report = sql(f"SELECT id FROM rights_reports WHERE reporter_email=lower('ghost-{STAMP}@example.local')")
        linked = httpx.post(f"{BASE}/moderation/reports/{ghost_report}/link", json={"case_id": case_for_real},
                            headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
        check("a queued notice can be attached to a case by staff", linked.status_code == 200
              and linked.json().get("linked") is True, f"{linked.status_code} {linked.text[:200]}")
        again = httpx.post(f"{BASE}/moderation/reports/{ghost_report}/link", json={"case_id": case_for_real},
                           headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
        check("and only once: a second link is refused with the database's own reason",
              again.status_code == 409 and "linked once" in again.text, f"{again.status_code} {again.text[:200]}")
        no_case = httpx.post(f"{BASE}/moderation/reports/{ghost_report}/link", json={},
                             headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": ws}, timeout=40)
        check("a link with no case named is refused before anything is asked of the database",
              no_case.status_code == 422, f"{no_case.status_code} {no_case.text[:120]}")

        # ---- the notice itself is evidence ----
        ref = real.json()["reference"]
        for label, statement in (
                ("rewrite the contact", f"UPDATE rights_reports SET reporter_email='evil@example.local' WHERE reference='{ref}'"),
                ("rewrite the claim", f"UPDATE rights_reports SET work_identification='rewritten' WHERE reference='{ref}'"),
                ("re-date the filing", f"UPDATE rights_reports SET created_at=now()-interval '1 year' WHERE reference='{ref}'"),
                ("delete it", f"DELETE FROM rights_reports WHERE reference='{ref}'")):
            err = sql_err(statement)
            check(f"not even the owning superuser role can {label}", "immutable" in err or "cannot be deleted" in err,
                  err or "the statement succeeded -- no trigger fired")
        app_read = sql_err(f"SET ROLE music_app; SELECT reporter_email FROM rights_reports LIMIT 1")
        check("the application role cannot read the table at all (only the functions, and they filter)",
              "permission denied" in app_read, app_read or "the SELECT succeeded")
        app_write = sql_err(f"SET ROLE music_app; UPDATE rights_reports SET grounds='other'")
        check("and cannot write it either", "permission denied" in app_write, app_write or "the UPDATE succeeded")

        # ---- arrival windows: bounded, and a refusal spends nothing ----
        deployed = subprocess.run(["docker", "compose", "exec", "-T", "api", "printenv",
                                   "REPORT_RATE_LIMIT_PER_MINUTE"], capture_output=True, text=True).stdout.strip()
        limit = int(deployed)
        check("the deployed container states the intake window it will be judged against",
              deployed.isdigit() and 0 < limit <= 60, f"printenv REPORT_RATE_LIMIT_PER_MINUTE -> {deployed!r}")
        flood_mail = f"flood-{STAMP}@example.local"
        # Two windows guard this door, and either can be the one that refuses: the per-address one this
        # fixture filled, and the stack-wide one that other filings in this run also spend. So the
        # assertion reads both keys and requires that *neither* grew while requests were being refused,
        # and that the one that is full is counting down.
        per_key = deployed_per_address_key(flood_mail)
        check("the window is measured under the key the service itself names",
              re.fullmatch(r"report:[0-9a-f]{64}", per_key) is not None, f"api said {per_key!r}")
        # Both windows are cleared before the flood, because the global one is stack-wide and the
        # filings earlier in this drill had already spent part of it -- measuring a per-address
        # window while a shared window is half full is not a single-variable experiment, and the
        # first version of this check read exactly that as "the per-address counter never filled".
        global_key = "report:global"
        redis_cli("DEL", per_key)
        redis_cli("DEL", global_key)
        codes = []
        for _ in range(limit + 2):
            codes.append(file(notice(flood_mail, asset)).status_code)
        first_429 = codes.index(429) + 1 if 429 in codes else None
        check("repeated filings from one address are throttled", first_429 is not None, f"statuses: {codes}")
        check(f"the window opens after no more than {limit} accepted filings from one address",
              first_429 is not None and first_429 <= limit + 1,
              f"first refusal at attempt {first_429}, statuses: {codes}")
        keys = {"per-address": per_key, "global": global_key}
        before = {n: (redis_cli("GET", k), int(redis_cli("TTL", k) or -2)) for n, k in keys.items()}
        # The refusals are spaced instead of fired back to back. With three quick filings the two TTL
        # samples sat 0.94 s to 1.84 s apart on this machine (`.scratch/probe_report_decay.py`, four runs;
        # the two `docker compose exec` round trips alone cost 0.20-0.46 s each), and a Redis second only
        # rolls about once per second: the old strict decrease could therefore read equal on a limiter that
        # had done nothing wrong -- and it read equal on a *re-arming* limiter too, which is the case the
        # check exists for. Flaky and toothless in the same regime, so the traffic now has to fill the
        # interval the verdict consumes.
        waits, stamps = [], []
        while True:
            refused = file(notice(flood_mail, asset))
            stated = refused.headers.get("Retry-After", "")
            waits.append(int(stated) if refused.status_code == 429 and stated.isdigit() else None)
            stamps.append(time.monotonic())
            span = stamps[-1] - stamps[0]
            if span >= MIN_TRAFFIC_SPAN:
                break
            time.sleep(MIN_TRAFFIC_SPAN / 3.0)
        after = {n: (redis_cli("GET", k), int(redis_cli("TTL", k) or -2)) for n, k in keys.items()}
        check("a refusal spends none of the window it reports",
              all(before[n][0] == after[n][0] for n in keys),
              f"counters {before} then {after}")
        check(f"the window counted down across {len(waits)} refusals and {span:.1f}s of traffic and never "
              "re-armed",
              refusals_count_down(waits, span, before["per-address"][1], after["per-address"][1]),
              f"Retry-After {waits[:3]} … {waits[-3:]} over {span:.1f}s against the per-address window "
              f"{before['per-address'][1]} then {after['per-address'][1]}; global "
              f"{before['global'][1]} then {after['global'][1]}")
    finally:
        mine = f"reporter_email ILIKE lower('%-{STAMP}@example.local')"
        residue = sql(f"SELECT count(*) FROM rights_reports WHERE {mine}")
        # Which cases this run opened, read out *before* the reports go: `opened_by` is
        # `'report:' || id`, so the case is only findable through the report row, and
        # `rights_reports.case_id` carries a real FK onto the case -- the reports have to be deleted
        # first, which leaves the join empty by the time the cases would be deleted.
        # Splitting on whitespace is safe because a uuid has none, and the values are re-quoted as
        # literals below rather than interpolated into the predicate's shape.
        listing = sql("SELECT coalesce(string_agg(opened_by, ' ' ORDER BY opened_by), '') "
                      "FROM moderation_cases WHERE opened_by IN "
                      f"(SELECT 'report:' || id::text FROM rights_reports WHERE {mine})")
        opened_by = [x for x in listing.split() if x]
        # The reports table refuses DELETE by trigger, and the trigger's function just RAISEs -- so even
        # the superuser role this drill connects as cannot outvote it. `session_replication_role =
        # replica` is the only way, and it is asked for as a per-connection startup option rather than a
        # `SET` inside the statement: that keeps the call a single statement (see sql()), and it means
        # the disarm cannot leak into any other session -- there is no `SET ... = origin` to forget, and
        # no second connection that would leave the DELETE running with the guard still armed.
        deleted = sql(f"WITH gone AS (DELETE FROM rights_reports WHERE {mine} RETURNING id) "
                      "SELECT count(*) FROM gone",
                      guc="-c session_replication_role=replica")
        after = sql(f"SELECT count(*) FROM rights_reports WHERE {mine}")
        if opened_by:
            cases = sql("WITH gone AS (DELETE FROM moderation_cases WHERE opened_by IN ("
                        + ",".join(f"'{x}'" for x in opened_by) + ") RETURNING id) SELECT count(*) FROM gone")
        else:
            cases = "0"
        print(f"  teardown: {deleted} drill reports removed with the immutability trigger disarmed for that "
              f"one connection ({after} left); {cases} cases this run opened removed ({len(opened_by)} "
              f"named); residue before: {residue}. Audit rows for these filings stay: audit_events is "
              f"append-only by 001's trigger, which this drill does not outvote.")
        run(f"DELETE FROM moderation_cases WHERE workspace_id='{ws}'")
        run(f"DELETE FROM media_assets WHERE workspace_id='{ws}'")
        run(f"DELETE FROM workspace_members WHERE workspace_id='{ws}'")
        run(f"DELETE FROM auth_sessions WHERE user_id='{outsider}'")
        run(f"DELETE FROM workspace_members WHERE user_id='{outsider}'")
        run(f"DELETE FROM users WHERE id='{outsider}'")
        run(f"DELETE FROM workspaces WHERE id='{outsider_ws}'")
        # The fixture workspace itself, last: until its members and media are gone nothing lets it go,
        # and its absence is what a repeated run counts (eight of these accumulated before this line).
        run(f"DELETE FROM workspaces WHERE id='{ws}'")
        for name, probe in (("fixture workspace", f"SELECT count(*) FROM workspaces WHERE id='{ws}'"),
                            ("report", f"SELECT count(*) FROM rights_reports WHERE {mine}"),
                            ("case opened by this run",
                             "SELECT count(*) FROM moderation_cases WHERE opened_by IN ("
                             + (",".join(f"'{x}'" for x in opened_by) or "NULL") + ")")):
            left = sql(probe)
            check(f"teardown left no {name} behind", left == "0", f"{probe} -> {left}")

    failed = [name for name, passed, _ in checks if not passed]
    print(f"\nreport drill: {len(checks) - len(failed)}/{len(checks)} checks passed")
    metrics.emit(checks_passed=len(checks) - len(failed), checks_total=len(checks))
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
