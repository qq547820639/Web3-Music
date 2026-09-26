#!/usr/bin/env python3
"""Resident drill for workspace membership, the roles on it, and the invitations that now gate them
(release gate G11).

Everything here runs against the live stack through the HTTP surface, with a handful of deliberate
excursions into the database, because the claim being tested is not "the endpoint checks the role"
but "the write cannot happen without the authority":

  * a statement issued as music_app directly is required to be refused, which is the premise that
    makes the SECURITY DEFINER functions in db/migrations/017 and db/migrations/019 the only carrier
    for these writes (measured, not assumed: music_app holds SELECT only on workspace_members, and
    019 gives it no more than SELECT on workspace_invitations); and
  * the functions are called as music_app with a forged actor id, so a future caller that skips the
    endpoint's role check cannot pass this drill.
  * two inputs the HTTP surface cannot produce are written by hand: an invitation row dated past its
    own expiry (a deadline nobody has ever reached is not a tested deadline), and a rename of an
    account's address -- no endpoint does that, 019's trigger does.

What the invitation half is for: 017's add-by-email resolved the address against `users`, answered an
unknown one with words that made the endpoint a platform-membership oracle, and wrote the member row
for a known one in the same call, with no step in which the person named could say no (017:58-71).
019 splits the offer from the membership and takes music_app's EXECUTE on that function. So the order
of the checks below is itself the claim: an offer that attached nobody, then an acceptance that
attached exactly one, then the refusals that keep an offer from being spent by the wrong account, or
twice, or after its deadline.

Fixtures are provisioned by SQL (no registration endpoint) and torn down at the end, except the erased
probe, which stays as designed residue -- the same asymmetry scripts/erasure_drill.py documents.
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

BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000") + "/api"
STAMP = time.strftime("%H%M%S", time.gmtime()) + uuid.uuid4().hex[:6]
OWNER = f"member-owner-{STAMP}@example.local"
JOINER = f"member-joiner-{STAMP}@example.local"
OUTSIDER = f"member-outsider-{STAMP}@example.local"
ERASER = f"member-eraser-{STAMP}@example.local"
TRANSFEREE = f"member-transferee-{STAMP}@example.local"
INVITEE = f"member-invitee-{STAMP}@example.local"
DECLINER = f"member-decliner-{STAMP}@example.local"
STALE = f"member-stale-{STAMP}@example.local"
RENAMED = f"member-renamed-{STAMP}@example.local"
ABSENT = f"nobody-{STAMP}@example.local"
GHOST = f"nobody-{STAMP}-ghost@example.local"
PASSWORD = "demo-owner"
WORKSPACE = f"Member Drill Workspace {STAMP}"
OTHER_WORKSPACE = f"Member Drill Foreign {STAMP}"
checks: list[tuple[str, bool, str]] = []
# Every plaintext token the surface handed this drill, kept for the audit census at the end: "the
# secret never reaches the trail" is only worth testing over all of them, not over one sample.
issued: list[str] = []


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


def bearer(token: str, workspace: str = "") -> dict:
    """Headers for the account-level routes; pass a workspace for the ones that resolve the actor in it."""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
            **({"X-Workspace-Id": workspace} if workspace else {})}


def invite(headers: dict, email: str, role: str):
    return httpx.post(BASE + "/workspace/invitations", json={"email": email, "role": role},
                      headers=headers, timeout=40)


def offer_body(response) -> dict:
    """A 200 from the invitation route, with its token kept on the way past so the audit census at the
    end can ask where every one of them went."""
    body = response.json() if response.status_code == 200 else {}
    if body.get("token"):
        issued.append(body["token"])
    return body


def offer_report(body: dict) -> str:
    """An offer rendered for a failure detail, without the token: this drill prints its log into the
    release evidence, and the claim at the end is that the plaintext lives in exactly one place."""
    return json.dumps({key: value for key, value in body.items() if key != "token"}, default=str)[:200]


def claim(headers: dict, token: str | None = None, invitation_id: str | None = None):
    """Spend an offer by exactly one handle: 019:300 refuses both and neither, so a claim that sent both
    would be passing a rule the product never agreed to have."""
    return httpx.post(BASE + "/account/invitations/accept",
                      json={"token": token} if token else {"invitation_id": invitation_id},
                      headers=headers, timeout=40)


def decline(headers: dict, invitation_id: str | None):
    return httpx.post(BASE + "/account/invitations/decline", json={"invitation_id": invitation_id},
                      headers=headers, timeout=40)


def inbox(token: str) -> dict:
    """The invitee's own surface: no workspace header, no membership, just a session and its address."""
    response = httpx.get(BASE + "/account/invitations", headers=bearer(token), timeout=40)
    response.raise_for_status()
    return response.json()


def offers(workspace: str, token: str) -> dict:
    """The inviter's list, with the role vocabulary the offer panel is handed. Owner/admin only."""
    response = httpx.get(BASE + "/workspace/invitations", headers=bearer(token, workspace), timeout=40)
    response.raise_for_status()
    return response.json()


def offer_row(workspace: str, token: str, invitation_id: str | None) -> dict:
    """One offer as the inviter's list sees it. A row that is not there reads as {} so the check that
    wanted it can fail with a detail instead of taking the rest of the drill down with a KeyError."""
    return next((row for row in offers(workspace, token)["invitations"]
                 if row["invitation_id"] == invitation_id), {})


def live_offers(workspace: str, email: str) -> str:
    """Rows in neither the used nor the revoked state -- 019:58's partial index predicate -- for one
    address. Note it counts an expired row: settling is a write, and the clock is not one."""
    return sql(f"SELECT count(*) FROM workspace_invitations WHERE workspace_id='{workspace}' "
               f"AND lower(email)=lower('{email}') AND used_at IS NULL AND revoked_at IS NULL")


def constraint_names(definition: str) -> list[str]:
    """Role names as the CHECK constraint spells them, from a constraint definition string."""
    return re.findall(r"'([a-z_]+)'::\w+", definition)


def validator_names(body: str) -> set[str]:
    """Role names as workspace_role_error() spells them. Prose literals are filtered by the space."""
    return {name for name in re.findall(r"'([a-z_ ]+)'", body) if " " not in name}


def live_constraint_names() -> list[str]:
    return constraint_names(sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                 "WHERE conrelid='workspace_members'::regclass AND contype='c' "
                                 "AND pg_get_constraintdef(oid) ~ 'role'"))


def live_validator_names() -> set[str]:
    # sql() reads a single line, so the function body is flattened before it is parsed -- a multi-line
    # definition otherwise reaches the extractor as its first line only, and a reader that cannot see
    # the IN list silently returns an empty set.
    return validator_names(sql("SELECT regexp_replace(pg_get_functiondef('workspace_role_error(text)'::regprocedure),"
                               " '\\s+', ' ', 'g')"))


def main() -> int:
    owner_id = make_user(OWNER, "Member Owner")
    joiner_id = make_user(JOINER, "Member Joiner")
    outsider_id = make_user(OUTSIDER, "Member Outsider")
    eraser_id = make_user(ERASER, "Member Eraser")
    transferee_id = make_user(TRANSFEREE, "Member Transferee")
    invitee_id = make_user(INVITEE, "Member Invitee")
    decliner_id = make_user(DECLINER, "Member Decliner")
    stale_id = make_user(STALE, "Member Stale")
    renamed_id = make_user(RENAMED, "Member Renamed")
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
        # 019:391 gives the application role SELECT on its own new table and not one privilege more,
        # which is what makes the six functions the only way an offer can be written -- and what the
        # subject access export depends on, so both polarities are read from the live catalogue.
        error = refused_as_app(f"INSERT INTO workspace_invitations(workspace_id,email,role,token_hash,created_by,expires_at) "
                               f"VALUES ('{ws}','{JOINER}','viewer','{uuid.uuid4().hex}{uuid.uuid4().hex}','{owner_id}',"
                               f"now() + interval '7 days')")
        check("the application role cannot write an offer either, so an invitation cannot come from code",
              "permission denied" in error, f"insert was accepted: {error}")
        error = refused_as_app(f"DELETE FROM workspace_invitations WHERE workspace_id='{ws}'")
        check("and it cannot delete one, so a settled offer cannot be made to have never existed",
              "permission denied" in error, f"delete was accepted: {error}")
        error = refused_as_app("SELECT count(*) FROM workspace_invitations")
        check("while reading that table is exactly the privilege it was given", error == "", f"read refused: {error}")

        # ---- invite, then accept -------------------------------------------------------
        # 017's write attached the account named in the very call that named it. 019 splits the two
        # halves, so the order of these checks is the claim: an offer that had already written a member
        # row would still leave the end state this section ends on, and only the middle check sees it.
        offer = offer_body(invite(owner_h, JOINER, "creator"))
        joiner_invitation, joiner_token = offer.get("invitation_id"), offer.get("token")
        check("an owner can offer an address a role, and the offer comes back as a claimable invitation",
              offer.get("invited") is True and offer.get("already_member") is False and offer.get("role") == "creator"
              and isinstance(joiner_token, str) and len(joiner_token) == 43, offer_report(offer))
        check("while it attaches nobody: the person it names has not agreed to anything yet",
              role_of(ws, joiner_id) == "-" and live_offers(ws, JOINER) == "1",
              f"role={role_of(ws, joiner_id)} live offers={live_offers(ws, JOINER)}")
        accepted = claim(bearer(tokens["joiner"]), token=joiner_token)
        check("the membership appears when the addressed account spends the token it was given",
              accepted.status_code == 200 and accepted.json().get("accepted") is True
              and accepted.json().get("added") is True and role_of(ws, joiner_id) == "creator",
              f"{accepted.status_code} {accepted.text[:200]} role={role_of(ws, joiner_id)}")
        check("and the spent offer leaves that account's own inbox with the membership",
              joiner_invitation not in [row["invitation_id"] for row in inbox(tokens["joiner"])["invitations"]]
              and live_offers(ws, JOINER) == "0",
              json.dumps(inbox(tokens["joiner"]), default=str)[:200])

        # ---- the same call twice, in both of its new shapes -----------------------------
        # Under 017 a second add was an idempotent no-op because the member row was already there. An
        # offer has two different doubles to answer for: the address that is already in cannot be offered
        # again -- and gets no token, because a token would be a claim on a row that was never written --
        # and the address still outside gets its earlier offer retired rather than a second live one
        # stacked on the same pair (019:58's partial unique index is what makes that the only shape).
        member_offer = offer_body(invite(owner_h, JOINER, "admin"))
        check("an address that is already a member is not offered a second time, and no token is handed "
              "out for an offer that was never written",
              member_offer.get("invited") is False and member_offer.get("already_member") is True
              and "token" not in member_offer and live_offers(ws, JOINER) == "0"
              and role_of(ws, joiner_id) == "creator", offer_report(member_offer))
        first = offer_body(invite(owner_h, ABSENT, "viewer"))
        second = offer_body(invite(owner_h, ABSENT, "creator"))
        check("re-inviting an address retires the earlier offer instead of stacking a second one",
              first.get("invited") is True and second.get("superseded") == 1 and live_offers(ws, ABSENT) == "1"
              and offer_row(ws, tokens["owner"], first.get("invitation_id")).get("invitation_status") == "revoked"
              and offer_row(ws, tokens["owner"], second.get("invitation_id")).get("invitation_status") == "pending",
              offer_report(second))
        bad_role = invite(owner_h, OUTSIDER, "superuser")
        check("a role the CHECK constraint does not allow is refused",
              bad_role.status_code in (400, 409) and "role" in bad_role.text.lower(),
              f"{bad_role.status_code} {bad_role.text[:160]}")
        check("and the refused offer wrote neither a membership nor an invitation",
              role_of(ws, outsider_id) == "-" and live_offers(ws, OUTSIDER) == "0",
              f"role={role_of(ws, outsider_id)} live offers={live_offers(ws, OUTSIDER)}")
        ownership = invite(owner_h, OUTSIDER, "owner")
        check("ownership is not on offer, because an invitation that could write it would be a second "
              "door onto the one decision transfer exists to make",
              ownership.status_code == 409 and "transfer" in ownership.text
              and sql(f"SELECT count(*) FROM workspace_invitations WHERE workspace_id='{ws}' AND role='owner'") == "0"
              and role_of(ws, outsider_id) == "-", f"{ownership.status_code} {ownership.text[:160]}")

        # ---- the role vocabulary the panel is handed ----------------------------------
        # 017 says the legal role names are not restated in the migration, yet workspace_role_error()
        # spells all eight of them out, and the API now hands the browser a third copy derived from
        # pg_constraint. Three representations can only be trusted if something compares them, so this
        # is the drift detector: the endpoint's list must equal the constraint's array, and the
        # validator's set must equal it too.
        view = httpx.get(BASE + "/workspace/members", headers=owner_h, timeout=40).json()
        api_roles = view.get("roles") or []
        offer_roles = offers(ws, tokens["owner"]).get("roles") or []
        constraint_roles = live_constraint_names()
        validator_roles = live_validator_names()
        check("the membership view hands the panel a role vocabulary", len(api_roles) >= 8, str(api_roles))
        check("the panel's list is the CHECK constraint's own array, in order",
              api_roles == constraint_roles, f"api {api_roles} vs constraint {constraint_roles}")
        check("and 017's validator agrees with that constraint, name for name",
              set(api_roles) == validator_roles, f"validator {sorted(validator_roles)} vs constraint {constraint_roles}")
        check("the offer panel gets the same vocabulary with the owner seat taken out of it",
              offer_roles == [role for role in api_roles if role != "owner"],
              f"invitations {offer_roles} vs membership {api_roles}")
        taken = []
        for role in api_roles:
            probe = invite(owner_h, f"nobody-{role}-{STAMP}@example.local", role)
            # `owner` is the one name 019 refuses on purpose (019:141): ownership moves when the current
            # owner says so, and an offer that could write it would be a way around that judgement.
            if role == "owner":
                if not (probe.status_code == 409 and "transfer" in probe.text):
                    taken.append(f"owner:{probe.status_code} {probe.text[:60]}")
            elif not (probe.status_code == 200 and offer_body(probe).get("invited") is True):
                taken.append(f"{role}:{probe.status_code} {probe.text[:60]}")
        check("every name in that vocabulary is a role the invitation path actually takes, and only "
              "ownership is withheld from it", not taken, "; ".join(taken))
        check("the constraint reader is not blind to an array it has not seen",
              constraint_names("CHECK ((role = ANY (ARRAY['zeta'::text, 'eta'::text])))") == ["zeta", "eta"],
              constraint_names("CHECK ((role = ANY (ARRAY['zeta'::text, 'eta'::text])))"))
        check("and the validator reader keeps names while dropping the prose",
              validator_names("WHEN role IN ('alpha','beta') THEN NULL ELSE 'unknown workspace role' END") == {"alpha", "beta"},
              sorted(validator_names("WHEN role IN ('alpha','beta') THEN NULL ELSE 'unknown workspace role' END")))

        # ---- who may act -------------------------------------------------------------
        joiner_h = {"Authorization": f"Bearer {tokens['joiner']}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        refused = invite(joiner_h, OUTSIDER, "viewer")
        check("a creator cannot invite", refused.status_code == 403, f"{refused.status_code} {refused.text[:160]}")
        check("the refusal left nothing behind", role_of(ws, outsider_id) == "-" and live_offers(ws, OUTSIDER) == "0",
              f"role={role_of(ws, outsider_id)} live offers={live_offers(ws, OUTSIDER)}")
        # 019:373 does not wrap the address-based write in something that looks safer; it takes the
        # application role's EXECUTE away, because that function was itself the oracle and the
        # non-consensual attach. "workspace owner or admin required" would be a failure here: it is the
        # answer of a door that is still standing, with a guard bolted onto it.
        forged = refused_as_app(f"SELECT add_workspace_member('{ws}','{joiner_id}','{OUTSIDER}','owner')")
        check("and the address-based write is out of the application role's reach, not just out of its routes",
              "permission denied" in forged, f"it answered: {forged[:200] or 'nothing -- the call went through'}")
        check("which is why no membership appeared", role_of(ws, outsider_id) == "-")
        cross = invite(bearer(tokens["owner"], other_ws), OUTSIDER, "viewer")
        check("being the owner of one workspace buys nothing in another",
              cross.status_code == 403 and role_of(other_ws, outsider_id) == "-" and live_offers(other_ws, OUTSIDER) == "0",
              f"{cross.status_code} {cross.text[:160]}")

        # ---- the token is a route to an offer, not the authority to take it ------------
        # 019:332 puts the acceptee's own address in the authorization condition, inside SQL. That is the
        # fork between this and the shape most invitation systems ship: Gitea's TeamInvitePost
        # (routers/web/org/teams.go) adds whoever presents the token and never compares the session's
        # address to the invite's, so a link that leaks is a membership. One offer, two sessions -- and
        # the refusal has to leave the offer spendable, or the wrong click would have settled it.
        invitation = offer_body(invite(owner_h, INVITEE, "viewer"))
        invitee_session = sign_in(INVITEE)
        stranger = claim(bearer(tokens["joiner"]), token=invitation.get("token"))
        check("a session that is not the address an offer names cannot spend the token in its hands",
              stranger.status_code == 409 and "addressed to a different account" in stranger.text
              and role_of(ws, invitee_id) == "-",
              f"{stranger.status_code} {stranger.text[:160]} role={role_of(ws, invitee_id)}")
        addressee = claim(bearer(invitee_session), token=invitation.get("token"))
        check("and the account it does name can spend that very same token",
              addressee.status_code == 200 and addressee.json().get("added") is True
              and role_of(ws, invitee_id) == "viewer",
              f"{addressee.status_code} {addressee.text[:160]} role={role_of(ws, invitee_id)}")
        twice = claim(bearer(invitee_session), invitation_id=invitation.get("invitation_id"))
        check("one offer, one membership: the second claim is refused and spent nothing",
              twice.status_code == 409 and "already been used" in twice.text
              and role_of(ws, invitee_id) == "viewer" and live_offers(ws, INVITEE) == "0",
              f"{twice.status_code} {twice.text[:160]}")

        # ---- whose inbox, and who may take an offer back --------------------------------
        # Two more addresses in the same workspace, both still outside it, are the smallest fixture that
        # separates "filtered by my address" (019:218) from "everything anybody was ever offered" -- the
        # route takes no workspace and no membership, so an unguarded read shows up here as a neighbour's
        # row rather than as a design note.
        outsider_session, decliner_session = sign_in(OUTSIDER), sign_in(DECLINER)
        outside_offer = offer_body(invite(owner_h, OUTSIDER, "viewer"))
        declined_offer = offer_body(invite(owner_h, DECLINER, "viewer"))
        outside_box, declined_box = inbox(outsider_session), inbox(decliner_session)
        check("each account's inbox is the offers addressed to it, in the workspace it was offered",
              [row["invitation_id"] for row in outside_box["invitations"]] == [outside_offer.get("invitation_id")]
              and [row["invitation_id"] for row in declined_box["invitations"]] == [declined_offer.get("invitation_id")]
              and all(row["invitation_workspace"] == ws for row in outside_box["invitations"] + declined_box["invitations"])
              and outside_box["email"] == OUTSIDER,
              json.dumps({"outsider": outside_box, "decliner": declined_box}, default=str)[:240])
        withdrawn = httpx.delete(BASE + f"/workspace/invitations/{outside_offer.get('invitation_id')}",
                                 headers=owner_h, timeout=40)
        check("an owner can take an offer back before anybody accepts it",
              withdrawn.status_code == 200 and withdrawn.json().get("revoked") is True
              and live_offers(ws, OUTSIDER) == "0", f"{withdrawn.status_code} {withdrawn.text[:160]}")
        late = claim(bearer(outsider_session), invitation_id=outside_offer.get("invitation_id"))
        check("and the account it was written for cannot accept it afterwards",
              late.status_code == 409 and "withdrawn" in late.text and role_of(ws, outsider_id) == "-",
              f"{late.status_code} {late.text[:160]}")
        declined_id = declined_offer.get("invitation_id")
        nosy = decline(bearer(tokens["joiner"]), declined_id)
        check("turning an offer down is bound to the same address as taking it up",
              nosy.status_code == 409 and "addressed to a different account" in nosy.text
              and live_offers(ws, DECLINER) == "1", f"{nosy.status_code} {nosy.text[:160]}")
        turned_down = decline(bearer(decliner_session), declined_id)
        check("the address an offer names can turn it down, and it stops being anyone's pending offer",
              turned_down.status_code == 200 and turned_down.json().get("declined") is True
              and declined_id not in [row["invitation_id"] for row in inbox(decliner_session)["invitations"]]
              and live_offers(ws, DECLINER) == "0" and role_of(ws, decliner_id) == "-"
              and offer_row(ws, tokens["owner"], declined_id).get("invitation_status") == "revoked",
              f"{turned_down.status_code} {turned_down.text[:160]}")

        # ---- a deadline nobody has ever reached is not a tested deadline -----------------
        # Seven days is not a window this drill waits out and the surface has no way to shorten it, so the
        # row is written with the two stamps the clock cannot produce together. Both are supplied because
        # invitation_expiry_after_creation (019:51) forbids the reverse: an offer created now and expired
        # an hour ago is not insertable, which is exactly why a probe has to date its creation back too.
        # The hash is two uuid4 hexes -- 64 lowercase characters, and all it has to be is unused.
        stale_hash = uuid.uuid4().hex + uuid.uuid4().hex
        expired_id = sql(f"INSERT INTO workspace_invitations(workspace_id,email,role,token_hash,created_by,created_at,expires_at)"
                         f" VALUES ('{ws}','{STALE}','viewer','{stale_hash}','{owner_id}',now() - interval '8 days',"
                         f"now() - interval '1 hour') RETURNING id")
        stale_session = sign_in(STALE)
        expired = claim(bearer(stale_session), invitation_id=expired_id)
        check("an offer past its own deadline cannot be accepted, even by the account it was written for",
              expired.status_code == 409 and "expired" in expired.text and role_of(ws, stale_id) == "-",
              f"{expired.status_code} {expired.text[:160]} role={role_of(ws, stale_id)}")
        stale_box = inbox(stale_session)
        check("and the expired offer is out of that address's inbox although the row is in the table",
              sql(f"SELECT count(*) FROM workspace_invitations WHERE lower(email)=lower('{STALE}')") == "1"
              and expired_id not in [row["invitation_id"] for row in stale_box["invitations"]],
              json.dumps(stale_box, default=str)[:200])
        check("while the inviter's list says which of its rows that one is",
              offer_row(ws, tokens["owner"], expired_id).get("invitation_status") == "expired",
              json.dumps(offer_row(ws, tokens["owner"], expired_id), default=str)[:200])

        # ---- an address that moves gives up the offers waiting on the old one ------------
        # 019:96's trigger exists for erasure -- erase_user_identity rewrites users.email (013:94), and
        # without it a real mailbox would sit invited somewhere until its deadline while the account it
        # belonged to was already gone. The same rule reads more widely: carrying an offer across a
        # rename would hand whoever now answers to the new address a membership the old one was offered.
        # No endpoint renames an address, so this is written the plainest way there is.
        renamed_offer = offer_body(invite(owner_h, RENAMED, "viewer"))
        sql(f"UPDATE users SET email='moved-{STAMP}@example.local' WHERE id='{renamed_id}'")
        moved_row = offer_row(ws, tokens["owner"], renamed_offer.get("invitation_id"))
        check("a rename settles the offers still waiting on the address that moved",
              moved_row.get("invitation_status") == "revoked" and live_offers(ws, RENAMED) == "0"
              and role_of(ws, renamed_id) == "-", json.dumps(moved_row, default=str)[:200])

        # ---- a subject access response has to account for the offers --------------------
        # An invitation row is about a person whether or not they ever accepted it: it carries their
        # address and keys three more by id (019:44-50, who offered it, who used it, who settled it). The
        # export declares its coverage instead of implying it (main.py:740), so the declaration and the
        # row get their own checks -- a coverage list nobody selected against is a claim, not a fact.
        subject = httpx.get(BASE + "/account/export", headers=bearer(tokens["joiner"]), timeout=40)
        bundle = subject.json() if subject.status_code == 200 else {}
        records = (bundle.get("records") or {}).get(ws, {})
        settled = [key for key in records if key.startswith("workspace_invitations.")]
        check("the export declares the invitation columns it reads",
              {"workspace_invitations.created_by", "workspace_invitations.used_by",
               "workspace_invitations.revoked_by"} <= set(bundle.get("coverage") or []),
              f"{subject.status_code} {json.dumps(bundle.get('coverage', []), default=str)[:200]}")
        check("and the acceptee's own response carries the offer they spent",
              subject.status_code == 200 and bool(settled)
              and any(str(row.get("used_by")) == joiner_id for key in settled for row in records[key]),
              f"{subject.status_code} keys={list(records)}")
        inviter = httpx.get(BASE + "/account/export", headers=bearer(tokens["owner"]), timeout=40)
        inviter_bundle = inviter.json() if inviter.status_code == 200 else {}
        inviter_records = (inviter_bundle.get("records") or {}).get(ws, {})
        check("while the inviter's response carries the offers it wrote, accepted or not",
              len(inviter_records.get("workspace_invitations.created_by") or []) > 0,
              f"{inviter.status_code} keys={list(inviter_records)}")

        # ---- change, and the last-owner guard ----------------------------------------
        # The roster is writable from inside a session, so the credential behind that session is
        # asked for again at the moment of the write. Both refusals below have to leave the role
        # exactly as it was, which is the only way to tell "refused" from "refused after writing".
        no_cred = httpx.patch(BASE + f"/workspace/members/{joiner_id}", json={"role": "viewer"}, headers=owner_h, timeout=40)
        check("a role change is refused until the password is re-presented",
              no_cred.status_code == 401 and role_of(ws, joiner_id) == "creator",
              f"{no_cred.status_code} {no_cred.text[:160]} role={role_of(ws, joiner_id)}")
        bad_cred = httpx.patch(BASE + f"/workspace/members/{joiner_id}", json={"role": "viewer", "password": "not-the-password"},
                               headers=owner_h, timeout=40)
        check("and by a password that does not match the account",
              bad_cred.status_code == 403 and role_of(ws, joiner_id) == "creator",
              f"{bad_cred.status_code} {bad_cred.text[:160]} role={role_of(ws, joiner_id)}")
        check("the credential is spent before the workspace rules are consulted",
              "transfer ownership" not in bad_cred.text, bad_cred.text[:160])
        changed = httpx.patch(BASE + f"/workspace/members/{joiner_id}", json={"role": "viewer", "password": PASSWORD},
                              headers=owner_h, timeout=40)
        check("an owner can change a role", changed.status_code == 200 and role_of(ws, joiner_id) == "viewer",
              f"{changed.status_code} {changed.text[:160]}")
        demote_self = httpx.patch(BASE + f"/workspace/members/{owner_id}", json={"role": "admin", "password": PASSWORD},
                                  headers=owner_h, timeout=40)
        check("the only owner cannot demote themselves",
              demote_self.status_code == 409 and "transfer ownership" in demote_self.text, f"{demote_self.status_code} {demote_self.text[:160]}")
        check("and that refusal changed nothing", role_of(ws, owner_id) == "owner")
        remove_self = httpx.request("DELETE", BASE + f"/workspace/members/{owner_id}", json={"password": PASSWORD},
                                    headers=owner_h, timeout=40)
        check("the only owner cannot remove themselves either",
              remove_self.status_code == 409 and "transfer ownership" in remove_self.text, f"{remove_self.status_code} {remove_self.text[:160]}")
        check("the workspace still has exactly its one owner",
              sql(f"SELECT count(*) FROM workspace_members WHERE workspace_id='{ws}' AND role='owner'") == "1")

        # ---- transfer, which is the way out of both guards ---------------------------
        bare_transfer = httpx.post(BASE + "/workspace/members/transfer", json={"user_id": joiner_id}, headers=owner_h, timeout=40)
        check("transferring ownership asks for the password too",
              bare_transfer.status_code == 401 and role_of(ws, joiner_id) == "viewer",
              f"{bare_transfer.status_code} {bare_transfer.text[:160]}")
        transfer = httpx.post(BASE + "/workspace/members/transfer",
                              json={"user_id": joiner_id, "password": PASSWORD}, headers=owner_h, timeout=40)
        check("ownership moves to an existing member",
              transfer.status_code == 200 and role_of(ws, joiner_id) == "owner" and role_of(ws, owner_id) == "admin",
              f"{transfer.status_code} {transfer.text[:160]} owner={role_of(ws, joiner_id)} previous={role_of(ws, owner_id)}")
        check("and never for a moment was the workspace without one",
              sql(f"SELECT count(*) FROM workspace_members WHERE workspace_id='{ws}' AND role='owner'") == "1")
        outsider_h = {"Authorization": f"Bearer {tokens['joiner']}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        tokens["joiner_owner"] = outsider_h
        back = httpx.post(BASE + "/workspace/members/transfer",
                          json={"user_id": owner_id, "password": PASSWORD}, headers=owner_h, timeout=40)
        check("the account that gave up ownership cannot take it back by itself",
              back.status_code == 409 and "only the current owner" in back.text, f"{back.status_code} {back.text[:160]}")
        check("the new owner is still the new owner", role_of(ws, joiner_id) == "owner")
        evict = httpx.post(BASE + "/workspace/members/transfer",
                           json={"user_id": outsider_id, "password": PASSWORD}, headers=outsider_h, timeout=40)
        check("ownership cannot move to someone who is not a member",
              evict.status_code == 409 and "must already be a member" in evict.text, f"{evict.status_code} {evict.text[:160]}")
        # the ex-owner is now an admin, and an admin must not be able to evict an owner
        removed_by_admin = httpx.request("DELETE", BASE + f"/workspace/members/{joiner_id}", json={"password": PASSWORD},
                                         headers=owner_h, timeout=40)
        check("an admin who is not an owner cannot remove the owner",
              removed_by_admin.status_code == 409 and "only an owner" in removed_by_admin.text,
              f"{removed_by_admin.status_code} {removed_by_admin.text[:160]}")
        check("the owner is still there", role_of(ws, joiner_id) == "owner")

        # ---- the wall is on a clock, not on luck -------------------------------------
        # Wrong passwords are counted per account and the window is fixed, so guessing is bounded even
        # though a manager moving through the roster seven times a minute is not. This runs here
        # because filling that window is precisely what it does, and nothing after it needs the
        # owner's credential again.
        statuses = [httpx.patch(BASE + f"/workspace/members/{joiner_id}", json={"role": "creator", "password": f"wrong-{i}"},
                                headers=owner_h, timeout=40).status_code for i in range(8)]
        limit = statuses.index(429) + 1 if 429 in statuses else None
        check("repeated wrong passwords are throttled", limit is not None, f"statuses: {statuses}")
        check("the throttle does not fire on the first attempt", bool(limit) and limit > 1, f"429 at attempt {limit}")
        check("while it is full even the right password is refused",
              limit is not None and httpx.patch(BASE + f"/workspace/members/{joiner_id}",
                                                json={"role": "creator", "password": PASSWORD},
                                                headers=owner_h, timeout=40).status_code == 429)
        check("and none of it wrote a role", role_of(ws, joiner_id) == "owner")

        # ---- the loop 014's error message promised -----------------------------------
        # erase_user_identity refuses a sole owner and says "transfer ownership first". Before 017
        # there was no way to do that from the API, so the privacy path dead-ended at a superuser.
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{ws}','{eraser_id}','creator')")
        eraser_token = sign_in(ERASER)
        eraser_h = {"Authorization": f"Bearer {eraser_token}", "X-Workspace-Id": ws, "Content-Type": "application/json"}
        sql(f"INSERT INTO workspace_members(workspace_id,user_id,role) VALUES ('{other_ws}','{eraser_id}','owner')")
        blocked = httpx.post(BASE + "/account/erasure", json={"confirmation": ERASER, "password": PASSWORD},
                             headers=eraser_h, timeout=40)
        check("erasure is refused while the account is the only owner of a workspace",
              blocked.status_code == 409 and "only owner" in blocked.text, f"{blocked.status_code} {blocked.text[:160]}")
        # that account owns other_ws, so the transfer must be made there with that membership
        other_h = {"Authorization": f"Bearer {eraser_token}", "X-Workspace-Id": other_ws, "Content-Type": "application/json"}
        # The privacy loop still needs somebody to hand the workspace to, and consent is the only way
        # anyone gets in now: eraser offers, transferee claims it from its own inbox by the id -- the
        # handle that route reads, not the link -- and only then is there a member to transfer to.
        transferee_offer = offer_body(invite(other_h, TRANSFEREE, "creator"))
        tokens["transferee"] = sign_in(TRANSFEREE)
        joined = claim(bearer(tokens["transferee"]), invitation_id=transferee_offer.get("invitation_id"))
        check("the workspace that blocks an erasure can still gain a member, the only way left",
              joined.status_code == 200 and joined.json().get("added") is True
              and role_of(other_ws, transferee_id) == "creator",
              f"{joined.status_code} {joined.text[:160]} role={role_of(other_ws, transferee_id)}")
        moved = httpx.post(BASE + "/workspace/members/transfer",
                           json={"user_id": transferee_id, "password": PASSWORD}, headers=other_h, timeout=40)
        check("the same account can move ownership of the workspace that blocks it",
              moved.status_code == 200 and role_of(other_ws, transferee_id) == "owner",
              f"{moved.status_code} {moved.text[:160]} now={role_of(other_ws, transferee_id)}")
        erased = httpx.post(BASE + "/account/erasure", json={"confirmation": ERASER, "password": PASSWORD},
                            headers=eraser_h, timeout=40)
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

        # ---- the platform-admin roster view (018 + GET /api/admin/v12/workspaces) ------
        # This is the one read on the release that crosses a tenant boundary on purpose, so the checks
        # are arranged around the carrier rather than the endpoint: the identity trio has no row level
        # security at all (018's header records the measured relrowsecurity=f), which means "is this
        # caller a platform administrator" has to be answered inside the statement that selects the
        # rows. A HTTP-only test would keep passing if someone deleted the guard and moved the answer
        # back into Python, so the same call is also issued as music_app with a forged actor id.
        #
        # get_actor still demands X-Workspace-Id for these routes -- the header resolves who is asking,
        # the path says who is being looked at. So the header below names the administrator's own
        # workspace while the path names a different tenant's, and one of the checks is that the path
        # wins; a version that answered from the header would list its own roster and still be green.
        admin_id = sql("SELECT id FROM users WHERE email='owner@example.local'")
        admin_ws = sql("SELECT m.workspace_id FROM workspace_members m JOIN users u ON u.id=m.user_id "
                       "WHERE u.email='owner@example.local' ORDER BY m.created_at LIMIT 1")
        check("the seed account this section reads with is the platform's administrator",
              sql(f"SELECT is_platform_admin FROM users WHERE id='{admin_id}'") == "t", admin_id)
        check("and it holds no membership in the tenant it is about to read",
              role_of(ws, admin_id) == "-" and admin_ws and admin_ws != ws, f"{admin_ws} vs {ws}")

        admin_h = {"Authorization": f"Bearer {sign_in('owner@example.local')}", "X-Workspace-Id": admin_ws}
        directory = httpx.get(BASE + "/admin/v12/workspaces", headers=admin_h, timeout=40)
        check("a platform admin gets the workspace directory",
              directory.status_code == 200, f"{directory.status_code} {directory.text[:160]}")
        listed_ws = {w["workspace_id"]: w for w in directory.json().get("workspaces", [])}
        check("which includes workspaces it is not a member of",
              {ws, other_ws} <= set(listed_ws), f"directory has {len(listed_ws)} rows, {ws} present={ws in listed_ws}")
        check("and says who owns each one and how large it is",
              bool(listed_ws) and all({"owner_email", "member_count"} <= set(w) for w in listed_ws.values()),
              json.dumps(listed_ws.get(ws, {}), default=str)[:200])
        counted = sql(f"SELECT count(*) FROM workspace_members WHERE workspace_id='{ws}'")
        check("the member count it prints is the table's own count",
              str(listed_ws.get(ws, {}).get("member_count")) == counted,
              f"directory={listed_ws.get(ws, {}).get('member_count')} table={counted}")

        roster = httpx.get(BASE + f"/admin/v12/workspaces/{ws}/members", headers=admin_h, timeout=40)
        check("a platform admin reads another tenant's roster without joining it",
              roster.status_code == 200, f"{roster.status_code} {roster.text[:160]}")
        admin_rows = roster.json().get("members", [])
        admin_view = {m["user_id"] for m in admin_rows}
        tenant_view = {m["user_id"] for m in members(ws, tokens["owner"])}
        table_view = set(sql(f"SELECT string_agg(user_id::text, ',') FROM workspace_members "
                             f"WHERE workspace_id='{ws}'").split(","))
        check("the platform roster, the tenant's own list, and the table agree",
              admin_view == tenant_view == table_view,
              f"platform={len(admin_view)} tenant={len(tenant_view)} table={len(table_view)}")
        own_roster = httpx.get(BASE + f"/admin/v12/workspaces/{admin_ws}/members", headers=admin_h, timeout=40)
        check("the roster is the workspace named in the path, not the one carried in the header",
              own_roster.status_code == 200 and admin_rows
              and {m["user_id"] for m in own_roster.json().get("members", [])} != admin_view,
              f"header ws={admin_ws} rows={json.dumps(own_roster.json().get('members', []), default=str)[:120]}")
        # 018 returns the membership role as `member_role` because `user_id` and `email` come from the
        # users join; the tenant endpoint calls the same fact `role`. Both names are pinned here so a
        # rename on either side reddens this drill instead of silently emptying the console column.
        check("and the roles match name for name, not just the crowd",
              admin_rows and {m["member_role"] for m in admin_rows}
              == {m["role"] for m in members(ws, tokens["owner"])},
              json.dumps(admin_rows[:2], default=str)[:200])
        check("each platform row carries the account state alongside the membership",
              bool(admin_rows) and all({"account_status", "is_platform_admin", "display_name"} <= set(m) for m in admin_rows),
              json.dumps(admin_rows[:1], default=str)[:200])

        refused_dir = httpx.get(BASE + "/admin/v12/workspaces", headers=owner_h, timeout=40)
        refused_roster = httpx.get(BASE + f"/admin/v12/workspaces/{other_ws}/members", headers=owner_h, timeout=40)
        check("a workspace owner who is not a platform admin gets neither view",
              refused_dir.status_code == 403 and refused_roster.status_code == 403,
              f"directory {refused_dir.status_code}, roster {refused_roster.status_code}")
        guard = refused_as_app(f"SELECT * FROM platform_workspace_members('{joiner_id}','{ws}')")
        check("the function refuses the same call when the endpoint is bypassed",
              "platform administrator required" in guard, f"accepted, or refused for another reason: {guard}")
        as_app_ok = refused_as_app(f"SELECT count(*) FROM platform_workspaces('{admin_id}')")
        check("music_app may execute it for a real admin, so the refusal above is the guard "
              "and not a missing GRANT", as_app_ok == "", f"as music_app: {as_app_ok}")

        missing = httpx.get(BASE + f"/admin/v12/workspaces/{uuid.uuid4()}/members", headers=admin_h, timeout=40)
        check("an unknown workspace id answers 404 rather than an empty roster",
              missing.status_code == 404, f"{missing.status_code} {missing.text[:160]}")
        malformed = httpx.get(BASE + "/admin/v12/workspaces/not-a-uuid/members", headers=admin_h, timeout=40)
        check("a malformed workspace id is the caller's problem to fix, not a 500",
              malformed.status_code == 422, f"{malformed.status_code} {malformed.text[:160]}")

        check("the directory read is recorded as an audit event",
              sql(f"SELECT count(*) FROM audit_events WHERE action='admin.workspace.directory.read' "
                  f"AND actor_id='{admin_id}'") != "0")
        check("and so is which roster was read",
              sql(f"SELECT count(*) FROM audit_events WHERE action='admin.workspace.roster.read' "
                  f"AND actor_id='{admin_id}' AND subject_id='{ws}'") != "0")

        # ---- what is actually holding these three tables apart (registered, not assumed) ----------
        # Every other business table in the schema is protected by row level security, forced, with a
        # policy per table. users/workspaces/workspace_members have no policy and no relrowsecurity: the
        # only wall there is that music_app was never granted anything but SELECT, so a write matches
        # zero rows and raises nothing -- which is why 017/018 exist. Read from the catalogue rather than
        # from the migrations, because 001 turns RLS on through EXECUTE format over an array of names and
        # a static reader cannot see any of it.
        covered = sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                      "WHERE n.nspname='public' AND c.relrowsecurity")
        trio_covered = sql("SELECT count(*) FROM pg_class WHERE relname IN "
                           "('users','workspaces','workspace_members') AND relrowsecurity")
        trio_policies = sql("SELECT count(*) FROM pg_policies WHERE tablename IN "
                            "('users','workspaces','workspace_members')")
        check("row level security is on for part of the schema, so the next two readings mean something",
            int(covered or 0) > 0, f"relrowsecurity relations: {covered}")
        check("and the three identity tables are not part of it",
              trio_covered == "0" and trio_policies == "0",
              f"enabled={trio_covered} policies={trio_policies} while {covered} relations are covered")
        privileges = {table: sql(f"SELECT string_agg(DISTINCT privilege_type, '+' ORDER BY privilege_type) "
                                 f"FROM information_schema.role_table_grants WHERE grantee='music_app' "
                                 f"AND table_name='{table}'")
                      for table in ("users", "workspaces", "workspace_members")}
        check("what keeps them apart is a privilege that was never granted",
              all(value == "SELECT" for value in privileges.values()), str(privileges))

        # ---- what the offer surface no longer answers ---------------------------------
        # This section used to pin the opposite fact, registered in docs/RELEASE_CHECKLIST.md as the
        # owner's call: 017:58-61 resolved the address against `users` and gave an unknown one its own
        # words, so the endpoint answered "does this mailbox have an account on this platform?" to any
        # owner on it -- and for a known one it wrote the member row in the same breath. 019 is the
        # ruling, and it closes the probe by not resolving the address at all: there is no branch for an
        # answer to leak out of. So the two calls below are that oracle, run against the live surface,
        # and the claim is that nothing in either answer tells the caller which one it got.
        known_response, ghost_response = invite(owner_h, OUTSIDER, "viewer"), invite(owner_h, GHOST, "viewer")
        known, ghost = offer_body(known_response), offer_body(ghost_response)
        check("an invitation cannot be used to ask whether an address has an account on the platform",
              known.get("invited") is True and ghost.get("invited") is True and set(known) == set(ghost)
              and "no account" not in (known_response.text + ghost_response.text).lower(),
              f"known={offer_report(known)} absent={offer_report(ghost)}")
        # Only the fields that name *this* offer may differ. Anything else that moves between the two
        # branches -- a superseded count, a role, an already_member flag -- is the shape of the answer
        # carrying the fact the wording no longer does.
        differs = {key for key in known if key in ghost and known[key] != ghost[key]}
        check("and the two answers differ only in what identifies the offer, not in what it means",
              differs <= {"email", "invitation_id", "expires_at", "token"}
              and known.get("email") == OUTSIDER and ghost.get("email") == GHOST,
              f"differing keys: {sorted(differs)}")
        check("while neither of them attached anybody",
              role_of(ws, outsider_id) == "-" and live_offers(ws, OUTSIDER) == "1" and live_offers(ws, GHOST) == "1",
              f"outsider role={role_of(ws, outsider_id)}")

        # ---- what the trail and the list are allowed to know ---------------------------
        # A spendable invitation is a bearer credential, which is why the plaintext exists exactly once
        # in the whole system: in the response that hands it over. The table keeps only its SHA-256
        # (auth.py:49), the audit row is written from the result before the token is folded into the
        # response (main.py:534), and the digest is not the credential either -- so whoever reads the
        # log, or the inviter's list, has a record of an offer and nothing they can spend.
        leaked = [token[:8] for token in issued
                  if sql("SELECT count(*) FROM audit_events WHERE action LIKE 'workspace.invitation.%' "
                         f"AND position('{token}' in payload::text) > 0") != "0"]
        check("no invitation token this drill was handed appears anywhere in the audit trail",
              bool(issued) and not leaked, f"visible in payloads: {leaked} of {len(issued)} tokens")
        trail = sql("SELECT string_agg(action, ',' ORDER BY action) FROM (SELECT DISTINCT action FROM audit_events "
                    "WHERE action LIKE 'workspace.invitation.%') a")
        check("and the trail really did record every kind of invitation action this drill performed, "
              "so the silence above is evidence rather than an empty table",
              set(trail.split(",")) == {"workspace.invitation.accept", "workspace.invitation.create",
                                        "workspace.invitation.decline", "workspace.invitation.revoke"},
              trail)
        listing = httpx.get(BASE + "/workspace/invitations", headers=bearer(tokens["owner"], ws), timeout=40)
        digests = [row for row in sql(f"SELECT string_agg(token_hash, ',') FROM workspace_invitations "
                                      f"WHERE workspace_id='{ws}'").split(",") if row]
        shown = [digest[:8] for digest in digests if digest in listing.text]
        check("the inviter's list hands back no stored digest, so reading it spends nothing",
              bool(digests) and not shown, f"{len(digests)} digests, visible: {shown}")
        spilled = [token[:8] for token in issued if token in listing.text]
        check("and no plaintext token comes back out of the list the offer was created for",
              not spilled, f"visible: {spilled}")
    finally:
        # Offers go before the accounts they name: created_by, used_by and revoked_by are foreign keys
        # into users (019:44-50), so an account cannot be deleted while an offer still points at it --
        # and a run that left them behind would make the next one's "exactly one pending offer" false.
        for workspace in (ws, other_ws):
            sql(f"DELETE FROM workspace_invitations WHERE workspace_id='{workspace}'")
        for user_id in (owner_id, joiner_id, outsider_id, transferee_id, invitee_id, decliner_id,
                        stale_id, renamed_id):
            sql(f"DELETE FROM auth_sessions WHERE user_id='{user_id}'")
            sql(f"DELETE FROM workspace_members WHERE user_id='{user_id}'")
            sql(f"DELETE FROM users WHERE id='{user_id}'")
        for workspace in (ws, other_ws):
            sql(f"DELETE FROM workspaces WHERE id='{workspace}'")
        left_users = sql(f"SELECT count(*) FROM users WHERE id IN ('{owner_id}','{joiner_id}','{outsider_id}',"
                         f"'{transferee_id}','{invitee_id}','{decliner_id}','{stale_id}','{renamed_id}')")
        left_ws = sql(f"SELECT count(*) FROM workspaces WHERE id IN ('{ws}','{other_ws}')")
        left_offers = sql(f"SELECT count(*) FROM workspace_invitations WHERE workspace_id IN ('{ws}','{other_ws}') "
                          f"OR lower(email) LIKE '%{STAMP}%'")
        erased_left = sql(f"SELECT status FROM users WHERE id='{eraser_id}'")
        print(f"  teardown: probes and their offers deleted (accounts left: {left_users}, workspaces left: "
              f"{left_ws}, offers left: {left_offers}); the erased probe remains by design (status={erased_left})")

    failed = [name for name, passed, _ in checks if not passed]
    print(f"\nmember drill: {len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
