#!/usr/bin/env python3
"""Resident drill for workspace membership and role management (release gate G11).

Everything here runs against the live stack through the HTTP surface, with two deliberate
excursions into the database, because the claim being tested is not "the endpoint checks the role"
but "the write cannot happen without the authority":

  * a statement issued as music_app directly is required to be refused, which is the premise that
    makes the SECURITY DEFINER functions in db/migrations/017 the only carrier for these writes
    (measured, not assumed: music_app holds SELECT only on workspace_members); and
  * the function is called as music_app with a forged actor id, so a future caller that skips the
    endpoint's role check cannot pass this drill.

Fixtures are provisioned by SQL (no registration endpoint) and torn down at the end, except the
erased probe, which stays as designed residue -- the same asymmetry scripts/erasure_drill.py
documents.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import httpx

BASE = os.getenv("API_BASE_URL", "http://localhost:8000") + "/api"
STAMP = time.strftime("%H%M%S", time.gmtime()) + uuid.uuid4().hex[:6]
OWNER = f"member-owner-{STAMP}@example.local"
JOINER = f"member-joiner-{STAMP}@example.local"
OUTSIDER = f"member-outsider-{STAMP}@example.local"
ERASER = f"member-eraser-{STAMP}@example.local"
TRANSFEREE = f"member-transferee-{STAMP}@example.local"
PASSWORD = "demo-owner"
WORKSPACE = f"Member Drill Workspace {STAMP}"
OTHER_WORKSPACE = f"Member Drill Foreign {STAMP}"
checks: list[tuple[str, bool, str]] = []


def sql(statement: str) -> str:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin", "-d", "music",
                          "-tAc", " ".join(statement.split())], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""


def refused_as_app(statement: str) -> str:
    """Run a statement with the application's own privileges and return the database's refusal."""
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "music_admin", "-d", "music",
                          "-tAc", "SET ROLE music_app; " + " ".join(statement.split())],
                         capture_output=True, text=True)
    return "" if out.returncode == 0 else (out.stderr.strip().splitlines() or ["unknown"])[0][:200]


def check(name: str, passed: bool, detail: str = ""):
    checks.append((name, bool(passed), detail))
    print(f"  {'OK  ' if passed else 'FAIL'} {name}" + ("" if passed else f" — {detail}"))


def sign_in(email: str) -> str:
    """Access token only. e2e_client.login also returns a workspace id, and the accounts here have
    none until the drill adds them -- which is the point of half of these checks."""
    for _ in range(5):
        response = httpx.post(BASE + "/auth/login", json={"email": email, "password": PASSWORD}, timeout=40)
        if response.status_code == 200:
            return response.json()["access_token"]
        if response.status_code != 429:
            response.raise_for_status()
        time.sleep(75)  # the login window is per account and slides on every attempt
    raise SystemExit(f"login for {email} kept getting rate limited")


def make_user(email: str, display: str) -> str:
    return sql(f"""INSERT INTO users(email,display_name,password_hash,is_platform_admin)
      SELECT '{email}','{display}',password_hash,false FROM users WHERE email='owner@example.local'
      RETURNING id""")


def role_of(workspace: str, user_id: str) -> str:
    return sql(f"SELECT coalesce((SELECT role FROM workspace_members WHERE workspace_id='{workspace}' "
               f"AND user_id='{user_id}'),'-')")


def members(workspace: str, token: str) -> list[dict]:
    response = httpx.get(BASE + "/workspace/members",
                         headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace}, timeout=40)
    response.raise_for_status()
    return response.json()["members"]


def main() -> int:
    owner_id = make_user(OWNER, "Member Owner")
    joiner_id = make_user(JOINER, "Member Joiner")
    outsider_id = make_user(OUTSIDER, "Member Outsider")
    eraser_id = make_user(ERASER, "Member Eraser")
    transferee_id = make_user(TRANSFEREE, "Member Transferee")
    ws = sql(f"INSERT INTO workspaces(name,plan) VALUES ('{WORKSPACE}','trial') RETURNING id")
    other_ws = sql(f"INSERT INTO workspaces(name,plan) VALUES ('{OTHER_WORKSPACE}','trial') RETURNING id")
    sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{ws}','{owner_id}','owner')")
    sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{other_ws}','{owner_id}','viewer')")
    tokens: dict[str, str] = {}
    try:
        tokens["owner"] = sign_in(OWNER)
        tokens["joiner"] = sign_in(JOINER)
        owner_h = {"Authorization": f"Bearer {tokens['owner']}", "X-Workspace-Id": ws, "Content-Type": "application/json"}

        # ---- the premise: the application role cannot write this table at all ---------
        error = refused_as_app(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{ws}','{joiner_id}','viewer')")
        check("music_app cannot write workspace_members directly, which is why 017 is the carrier",
              "permission denied" in error, f"insert was accepted: {error}")
        error = refused_as_app(f"UPDATE workspace_members SET role='owner' WHERE workspace_id='{ws}' AND user_id='{owner_id}'")
        check("and it cannot promote itself either", "permission denied" in error, f"update was accepted: {error}")

        # ---- add ---------------------------------------------------------------------
        added = httpx.post(BASE + "/workspace/members", json={"email": JOINER, "role": "creator"}, headers=owner_h, timeout=40)
        check("an owner can add a member", added.status_code == 200 and added.json().get("added") is True,
              f"{added.status_code} {added.text[:200]}")
        check("the membership is really there", role_of(ws, joiner_id) == "creator", role_of(ws, joiner_id))
        again = httpx.post(BASE + "/workspace/members", json={"email": JOINER, "role": "admin"}, headers=owner_h, timeout=40)
        check("adding the same account twice is an idempotent no-op, not a second row",
              again.status_code == 200 and again.json().get("already_member") is True and role_of(ws, joiner_id) == "creator",
              f"{again.status_code} {again.text[:160]} role={role_of(ws, joiner_id)}")
        bad_role = httpx.post(BASE + "/workspace/members", json={"email": OUTSIDER, "role": "superuser"}, headers=owner_h, timeout=40)
        check("a role the CHECK constraint does not allow is refused",
              bad_role.status_code in (400, 409) and "role" in bad_role.text.lower(), f"{bad_role.status_code} {bad_role.text[:160]}")
        check("and the refused account was not added", role_of(ws, outsider_id) == "-")
        unknown = httpx.post(BASE + "/workspace/members", json={"email": f"nobody-{STAMP}@example.local", "role": "viewer"},
                             headers=owner_h, timeout=40)
        check("an email with no account here is refused rather than invited into a void",
              unknown.status_code in (404, 409) and "account" in unknown.text.lower(), f"{unknown.status_code} {unknown.text[:160]}")

        # ---- who may act -------------------------------------------------------------
        joiner_h = {"Authorization": f"Bearer {tokens['joiner']}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        refused = httpx.post(BASE + "/workspace/members", json={"email": OUTSIDER, "role": "viewer"}, headers=joiner_h, timeout=40)
        check("a creator cannot add members", refused.status_code == 403, f"{refused.status_code} {refused.text[:160]}")
        check("the refusal left nothing behind", role_of(ws, outsider_id) == "-")
        forged = refused_as_app(f"SELECT add_workspace_member('{ws}','{joiner_id}','{OUTSIDER}','owner')")
        check("and the function refuses the same actor even when the endpoint is skipped",
              "workspace owner or admin required" in forged or "permission denied" in forged, forged[:200])
        check("which is why no membership appeared", role_of(ws, outsider_id) == "-")
        cross = httpx.post(BASE + "/workspace/members", json={"email": OUTSIDER, "role": "viewer"},
                           headers={"Authorization": f"Bearer {tokens['owner']}", "X-Workspace-Id": other_ws,
                                    "Content-Type": "application/json"}, timeout=40)
        check("being the owner of one workspace buys nothing in another",
              cross.status_code == 403 and role_of(other_ws, outsider_id) == "-", f"{cross.status_code} {cross.text[:160]}")

        # ---- change, and the last-owner guard ----------------------------------------
        changed = httpx.patch(BASE + f"/workspace/members/{joiner_id}", json={"role": "viewer"}, headers=owner_h, timeout=40)
        check("an owner can change a role", changed.status_code == 200 and role_of(ws, joiner_id) == "viewer",
              f"{changed.status_code} {changed.text[:160]}")
        demote_self = httpx.patch(BASE + f"/workspace/members/{owner_id}", json={"role": "admin"}, headers=owner_h, timeout=40)
        check("the only owner cannot demote themselves",
              demote_self.status_code == 409 and "transfer ownership" in demote_self.text, f"{demote_self.status_code} {demote_self.text[:160]}")
        check("and that refusal changed nothing", role_of(ws, owner_id) == "owner")
        remove_self = httpx.delete(BASE + f"/workspace/members/{owner_id}", headers=owner_h, timeout=40)
        check("the only owner cannot remove themselves either",
              remove_self.status_code == 409 and "transfer ownership" in remove_self.text, f"{remove_self.status_code} {remove_self.text[:160]}")
        check("the workspace still has exactly its one owner",
              sql(f"SELECT count(*) FROM workspace_members WHERE workspace_id='{ws}' AND role='owner'") == "1")

        # ---- transfer, which is the way out of both guards ---------------------------
        transfer = httpx.post(BASE + "/workspace/members/transfer", json={"user_id": joiner_id}, headers=owner_h, timeout=40)
        check("ownership moves to an existing member",
              transfer.status_code == 200 and role_of(ws, joiner_id) == "owner" and role_of(ws, owner_id) == "admin",
              f"{transfer.status_code} {transfer.text[:160]} owner={role_of(ws, joiner_id)} previous={role_of(ws, owner_id)}")
        check("and never for a moment was the workspace without one",
              sql(f"SELECT count(*) FROM workspace_members WHERE workspace_id='{ws}' AND role='owner'") == "1")
        outsider_h = {"Authorization": f"Bearer {tokens['joiner']}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        tokens["joiner_owner"] = outsider_h
        back = httpx.post(BASE + "/workspace/members/transfer", json={"user_id": owner_id}, headers=owner_h, timeout=40)
        check("the account that gave up ownership cannot take it back by itself",
              back.status_code == 409 and "only the current owner" in back.text, f"{back.status_code} {back.text[:160]}")
        check("the new owner is still the new owner", role_of(ws, joiner_id) == "owner")
        evict = httpx.post(BASE + "/workspace/members/transfer", json={"user_id": outsider_id}, headers=outsider_h, timeout=40)
        check("ownership cannot move to someone who is not a member",
              evict.status_code == 409 and "must already be a member" in evict.text, f"{evict.status_code} {evict.text[:160]}")
        # the ex-owner is now an admin, and an admin must not be able to evict an owner
        removed_by_admin = httpx.delete(BASE + f"/workspace/members/{joiner_id}", headers=owner_h, timeout=40)
        check("an admin who is not an owner cannot remove the owner",
              removed_by_admin.status_code == 409 and "only an owner" in removed_by_admin.text,
              f"{removed_by_admin.status_code} {removed_by_admin.text[:160]}")
        check("the owner is still there", role_of(ws, joiner_id) == "owner")

        # ---- the loop 014's error message promised -----------------------------------
        # erase_user_identity refuses a sole owner and says "transfer ownership first". Before 017
        # there was no way to do that from the API, so the privacy path dead-ended at a superuser.
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{ws}','{eraser_id}','creator')")
        eraser_token = sign_in(ERASER)
        eraser_h = {"Authorization": f"Bearer {eraser_token}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{other_ws}','{eraser_id}','owner')")
        blocked = httpx.post(BASE + "/account/erasure", json={"confirmation": ERASER}, headers=eraser_h, timeout=40)
        check("erasure is refused while the account is the only owner of a workspace",
              blocked.status_code == 409 and "only owner" in blocked.text, f"{blocked.status_code} {blocked.text[:160]}")
        # that account owns other_ws, so the transfer must be made there with that membership
        other_h = {"Authorization": f"Bearer {eraser_token}", "X-Workspace-Id": other_ws, "Content-Type": "application/json"}
        httpx.post(BASE + "/workspace/members", json={"email": TRANSFEREE, "role": "creator"}, headers=other_h, timeout=40)
        moved = httpx.post(BASE + "/workspace/members/transfer", json={"user_id": transferee_id}, headers=other_h, timeout=40)
        check("the same account can move ownership of the workspace that blocks it",
              moved.status_code == 200 and role_of(other_ws, transferee_id) == "owner",
              f"{moved.status_code} {moved.text[:160]} now={role_of(other_ws, transferee_id)}")
        erased = httpx.post(BASE + "/account/erasure", json={"confirmation": ERASER}, headers=eraser_h, timeout=40)
        check("and the erasure the message promised now actually goes through",
              erased.status_code == 200, f"{erased.status_code} {erased.text[:200]}")
        check("with the membership it no longer owns gone from its own record",
              sql(f"SELECT count(*) FROM workspace_members WHERE user_id='{eraser_id}'") == "0")
        check("and the other account really did become the owner",
              sql(f"SELECT status FROM users WHERE id='{eraser_id}'") == "erased")

        # ---- listing -----------------------------------------------------------------
        listed = members(ws, tokens["owner"])
        check("the member list is what the table says",
              {m["user_id"] for m in listed} == {owner_id, joiner_id} or {m["user_id"] for m in listed} >= {owner_id, joiner_id},
              json.dumps(listed)[:200])
        check("each row carries a role and an identity",
              all({"role", "user_id", "email"} <= set(m) for m in listed), json.dumps(listed[:1])[:200])
        outsider_read = httpx.get(BASE + "/workspace/members",
                                  headers={"Authorization": f"Bearer {tokens['joiner']}", "X-Workspace-Id": other_ws}, timeout=40)
        check("a non-member cannot read another workspace's roster",
              outsider_read.status_code in (403, 400), f"{outsider_read.status_code}")
    finally:
        for user_id in (owner_id, joiner_id, outsider_id, transferee_id):
            sql(f"DELETE FROM auth_sessions WHERE user_id='{user_id}'")
            sql(f"DELETE FROM workspace_members WHERE user_id='{user_id}'")
            sql(f"DELETE FROM users WHERE id='{user_id}'")
        for workspace in (ws, other_ws):
            sql(f"DELETE FROM workspaces WHERE id='{workspace}'")
        left_users = sql(f"SELECT count(*) FROM users WHERE id IN ('{owner_id}','{joiner_id}','{outsider_id}','{transferee_id}')")
        left_ws = sql(f"SELECT count(*) FROM workspaces WHERE id IN ('{ws}','{other_ws}')")
        erased_left = sql(f"SELECT status FROM users WHERE id='{eraser_id}'")
        print(f"  teardown: probes deleted (accounts left: {left_users}, workspaces left: {left_ws}); "
              f"the erased probe remains by design (status={erased_left})")

    failed = [name for name, passed, _ in checks if not passed]
    print(f"\nmember drill: {len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
