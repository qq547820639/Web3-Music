"""Static guards on the workspace-membership write path.

The live proofs are scripts/member_drill.py: it walks the running stack, including that music_app is
refused when it writes `workspace_members` itself and that 017's functions refuse a forged actor. What
belongs in a no-stack test is the shape a drill cannot see from outside: that no code path in the API
writes the table, that every membership endpoint resolves an actor, and that every function 017 declares
carries its REVOKE/GRANT pair.

019 moved one of those four writes out of the surface entirely: adding a member by e-mail address. This
file keeps the census honest about the remaining three and refuses the fourth's return; the invitation
half -- 019's own functions, the token, and the address binding -- is guarded next door, in
tests/unit/test_workspace_invitation_surface.py.

Each guard ships with the sample that must make it fire. A detector that has never been seen to report
anything is indistinguishable from a detector that cannot.

One limit stated rather than papered over: the write guard matches literal SQL. A statement whose
table name is interpolated at runtime -- f"DELETE FROM {table}" -- is invisible to any static
detector, including this one. That hole is closed by the database, not by grep: member_drill asserts
that an INSERT and a self-promoting UPDATE issued as music_app are both refused with permission
denied, so a dynamic write reaches the same wall the literal ones are kept away from.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "services/api/app/main.py"
MIGRATION = ROOT / "db/migrations/017_workspace_membership.sql"

FUNCTION = re.compile(r"^\s*CREATE OR REPLACE FUNCTION (\w+)\(", re.IGNORECASE | re.MULTILINE)
MEMBERSHIP_WRITE = re.compile(r"\b(change|remove|transfer)_workspace_\w+\b")
MEMBERSHIP_FUNCTIONS = {"change_workspace_member_role", "remove_workspace_member",
                        "transfer_workspace_ownership"}
# The route set 019 leaves behind: three membership writes, the roster read, and the four invitation
# routes that replaced adding a member by address. Path strings as declared on the decorators.
MEMBERSHIP_SURFACE = ("/api/workspace/members", "/api/workspace/invitations", "/api/account/invitations")
DECLARED_ROUTES = {"/api/workspace/members", "/api/workspace/members/{member_id}",
                   "/api/workspace/members/transfer",
                   "/api/workspace/invitations", "/api/workspace/invitations/{invitation_id}",
                   "/api/account/invitations", "/api/account/invitations/accept",
                   "/api/account/invitations/decline"}
# 019 deleted the by-address write; the name stays here so the deletion cannot rot back into a route.
REMOVED_WRITE = "add_workspace_member"


def on_surface(path: str) -> bool:
    """Does this route path belong to the membership/invitation surface this file guards?"""
    return path.startswith(MEMBERSHIP_SURFACE)


def tree_of(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def statements(path) -> list[ast.stmt]:
    return tree_of(path).body


def sql_literals(tree) -> list[str]:
    """Every plain (non-f) string literal in the module that starts like SQL."""
    return [" ".join(node.value.split()) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", node.value, re.IGNORECASE)]


def writes_to(text: str, table: str) -> list[str]:
    """Every literal statement in the source that writes `table`, wherever it is written."""
    pattern = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:[a-z_]+\.)?" + re.escape(table) + r"\b",
                         re.IGNORECASE)
    return [match.group(0) for match in pattern.finditer(text)]


def membership_routes(node) -> list[str]:
    """The paths a function serves, if it serves any on the membership/invitation surface."""
    if not isinstance(node, ast.FunctionDef):
        return []
    routes = [d for d in node.decorator_list
              if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
              and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"]
    paths = [d.args[0].value for d in routes if d.args and isinstance(d.args[0], ast.Constant)]
    return [path for path in paths if on_surface(path)]


def unguarded(node) -> bool:
    """True when an endpoint on the surface resolves nobody: no get_actor, require_roles or get_user.

    The account-side routes (/api/account/invitations*) resolve a session rather than a workspace actor
    on purpose -- their caller is not a member yet -- so a resolved identity of either shape is enough.
    """
    if not membership_routes(node):
        return False
    dumped = ast.dump(node.args)
    return not any(name in dumped for name in ("get_actor", "require_roles", "get_user"))


def function_names(sql_text: str) -> list[str]:
    return FUNCTION.findall(sql_text)


def grants_declared(sql_text: str) -> tuple[set[str], set[str]]:
    revoked = {m.group(1) for m in re.finditer(r"REVOKE ALL ON FUNCTION (\w+)\(", sql_text, re.IGNORECASE)}
    granted = {m.group(1) for m in re.finditer(r"GRANT EXECUTE ON FUNCTION (\w+)\(", sql_text, re.IGNORECASE)}
    return revoked, granted


# ------------------------------------------------------------------ guards ----

def test_api_never_writes_workspace_members_itself():
    offenders = writes_to(MAIN.read_text(encoding="utf-8"), "workspace_members")
    assert not offenders, (
        "music_app holds SELECT only on workspace_members, so an application-side write matches "
        f"zero rows and raises nothing rather than failing: {offenders}")


def test_the_write_detector_fires_on_every_literal_shape_it_claims_to_see():
    samples = {
        "bare insert": "INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (%s,%s,%s)",
        "update": "UPDATE workspace_members SET role=%s WHERE user_id=%s",
        "inside a call": "cur.execute('DELETE FROM workspace_members WHERE workspace_id=%s')",
        "schema qualified": "delete from public.workspace_members where 1=1",
    }
    for label, sample in samples.items():
        assert writes_to(sample, "workspace_members"), (label, sample)
    assert not writes_to("SELECT m.role FROM workspace_members m WHERE m.workspace_id=%s", "workspace_members")
    assert not writes_to("UPDATE asset_offers SET status='sold'", "workspace_members")


def test_membership_writes_go_through_the_017_functions():
    calls = [sql for sql in sql_literals(tree_of(MAIN)) if MEMBERSHIP_WRITE.search(sql)]
    assert len(calls) == 3, f"expected exactly one SQL string per surviving 017 write function: {calls}"
    for sql in calls:
        assert sql.startswith("SELECT * FROM "), sql
    assert {MEMBERSHIP_WRITE.search(sql).group(0) for sql in calls} == MEMBERSHIP_FUNCTIONS


def test_the_by_address_write_stays_off_the_surface():
    """019 revoked music_app's EXECUTE on add_workspace_member.

    A route that called it again would not be a feature restored, it would be a 500: the application
    role can no longer run the function at all. The deletion is pinned on both sides -- the SQL string
    and the POST route -- because the reason it went away (an address probe plus a membership nobody
    agreed to) is still standing in 017's body, which the checksum freeze keeps unedited.
    """
    main = MAIN.read_text(encoding="utf-8")
    callers = [sql for sql in sql_literals(tree_of(MAIN)) if REMOVED_WRITE in sql]
    assert not callers, f"{REMOVED_WRITE} is called from the API again: {callers}"
    sources = sorted(p.relative_to(ROOT / "services/api").as_posix()
                     for p in (ROOT / "services/api").rglob("*.py") if REMOVED_WRITE in p.read_text(encoding="utf-8"))
    assert not sources, f"{REMOVED_WRITE} reappears in services/api/ at {sources}"
    restored = [f"{verb} {path}" for verb, path in route_verbs()
                if path == "/api/workspace/members" and verb == "POST"]
    assert not restored, (
        f"POST /api/workspace/members is back, and 019:373 leaves it with nothing to call: {restored}")
    removed_ids = {"teamEmail", "teamAddRole", "teamAdd"} & element_ids(
        (ROOT / "services/web/index.html").read_text(encoding="utf-8"))
    assert not removed_ids, f"the add-by-address box came back with the route: {sorted(removed_ids)}"
    assert main.count("/api/workspace/members") >= 1, "the roster read went away, so the checks above are empty"


def test_the_removed_write_detector_fires_on_a_route_and_a_statement():
    """The guard above reports an absence, so the planted copy is the only proof it can see one."""
    planted = ('@app.post("/api/workspace/members")\n'
               'def add_a_member(body, actor=Depends(get_actor)):\n'
               '    return fetch_all("SELECT * FROM add_workspace_member(%s,%s,%s,%s)", '
               '(actor.workspace_id, actor.user_id, body.email, body.role))\n')
    restored = [(verb, path) for verb, path in route_verbs(planted)
                if path == "/api/workspace/members" and verb == "POST"]
    assert restored == [("POST", "/api/workspace/members")], restored
    assert [sql for sql in sql_literals(ast.parse(planted)) if REMOVED_WRITE in sql], (
        "the statement reader cannot see the call the deleted route used to make")
    assert sorted({"teamAdd"} & element_ids('<div id="teamAdd"></div>')) == ["teamAdd"], (
        "the markup reader cannot see a restored add box either")
    # And the surviving three must not read as a restoration.
    assert not [sql for sql in sql_literals(tree_of(MAIN)) if REMOVED_WRITE in sql]


def test_every_membership_endpoint_declares_an_actor_dependency():
    offenders = [node.name for node in statements(MAIN) if unguarded(node)]
    assert not offenders, f"membership endpoints that resolve no actor: {offenders}"


def test_the_actor_detector_fires_on_an_unguarded_endpoint():
    """The guard above has to be able to see an absence, not merely report a presence."""
    open_route = ast.parse('@app.get("/api/workspace/members")\ndef open_route(request):\n    return None\n',
                           filename="<synthetic>")
    assert [node.name for node in open_route.body if unguarded(node)] == ["open_route"]
    guarded = ast.parse('@app.get("/api/workspace/members")\n'
                        'def closed_route(request, actor=Depends(get_actor)):\n    return None\n',
                        filename="<synthetic>")
    assert [node.name for node in guarded.body if unguarded(node)] == []
    # 019 put the same requirement on the invitation surface, on both sides of it: the manager's routes
    # resolve a workspace actor, the invitee's resolve a session, and neither may resolve nothing.
    for path, dependency in (("/api/workspace/invitations", "require_roles"),
                             ("/api/account/invitations/accept", "get_user")):
        left_open = ast.parse(f'@app.post("{path}")\ndef open_route(request):\n    return None\n',
                              filename="<synthetic>")
        assert [node.name for node in left_open.body if unguarded(node)] == ["open_route"], path
        covered = ast.parse(f'@app.post("{path}")\ndef closed_route(request, actor=Depends({dependency})):\n'
                            '    return None\n', filename="<synthetic>")
        assert [node.name for node in covered.body if unguarded(node)] == [], path
    elsewhere = ast.parse('@app.get("/api/projects")\ndef unrelated(request):\n    return None\n',
                          filename="<synthetic>")
    assert [node.name for node in elsewhere.body if unguarded(node)] == [], "the detector must stay in its lane"


def test_every_017_function_revokes_public_and_grants_the_app_role():
    sql_text = MIGRATION.read_text(encoding="utf-8")
    declared = set(function_names(sql_text))
    revoked, granted = grants_declared(sql_text)
    assert declared, "the parser found no functions, so this assertion would be vacuous"
    assert declared == granted, f"missing GRANT EXECUTE: {sorted(declared - granted)}"
    assert declared == revoked, f"missing REVOKE FROM PUBLIC: {sorted(declared - revoked)}"
    assert MEMBERSHIP_FUNCTIONS <= declared


def test_the_grant_census_fires_on_a_function_shipped_without_grants():
    sample = ("CREATE OR REPLACE FUNCTION sneaky_workspace_member(uuid) RETURNS void AS $$ SELECT 1 $$;\n"
              "REVOKE ALL ON FUNCTION workspace_role_error(text) FROM PUBLIC;\n"
              "GRANT EXECUTE ON FUNCTION workspace_role_error(text) TO music_app;\n")
    declared = set(function_names(sample))
    revoked, granted = grants_declared(sample)
    # only the sneaky one is *declared* here; the other name appears solely in a grant, which is
    # exactly the shape that must be reported as "declared, never granted"
    assert declared == {"sneaky_workspace_member"}
    assert declared - granted == {"sneaky_workspace_member"}
    assert declared - revoked == {"sneaky_workspace_member"}


# ------------------------------------------------------------------ the panel ----
# services/web/app.js renders the roster, so three things about it are checkable without a browser:
# it drives the routes the API actually declares, it selects only elements the document defines, and
# the role vocabulary it offers is read from the schema rather than typed out again in JavaScript.

APP_JS = ROOT / "services/web/app.js"
INDEX_HTML = ROOT / "services/web/index.html"
ROLE_SOURCE = ROOT / "db/migrations/001_production_candidate.sql"

# The method each surviving path answers with. POST on the bare /api/workspace/members is the entry
# that used to be in this table and is not any more: 019 moved it to POST /api/workspace/invitations.
DECLARED_ENDPOINTS = {("GET", "/api/workspace/members"),
                      ("PATCH", "/api/workspace/members/{member_id}"),
                      ("DELETE", "/api/workspace/members/{member_id}"),
                      ("POST", "/api/workspace/members/transfer"),
                      ("POST", "/api/workspace/invitations"),
                      ("GET", "/api/workspace/invitations"),
                      ("DELETE", "/api/workspace/invitations/{invitation_id}"),
                      ("GET", "/api/account/invitations"),
                      ("POST", "/api/account/invitations/accept"),
                      ("POST", "/api/account/invitations/decline")}
# The elements the roster and the invitation panels are built out of. The three whose names said "add a
# member" (teamEmail, teamAddRole, teamAdd) are gone with the route; the teamInvite* / invite* / inbox*
# names are what replaced them, and the panel cannot work without any of the rest.
PANEL_IDS = {"teamState", "teamList", "teamManage", "teamError",
             "teamInviteEmail", "teamInviteRole", "teamInvite", "teamInviteToken", "teamInviteExpiry",
             "invitesBlock", "inviteState", "inviteList",
             "inboxState", "inboxList", "inboxToken", "inboxTokenAccept", "inboxError"}
SELECTED = re.compile(r"\$\('#((?:team|invite|inbox)[A-Za-z0-9]*)'\)")


def element_ids(document: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', document))


def dangling_selectors(app: str, document: str) -> list[str]:
    """Roster or invitation elements the panel reaches for and no document defines."""
    return sorted({found for found in SELECTED.findall(app)} - element_ids(document))


def role_names_in_schema() -> set[str]:
    text = ROLE_SOURCE.read_text(encoding="utf-8")
    line = [l for l in text.splitlines() if "CHECK(role IN" in l]
    assert len(line) == 1, f"the role CHECK must be readable from 001, found {len(line)}"
    return set(re.findall(r"'([a-z_]+)'", line[0]))


def literal_role_names(source: str, function: str) -> set[str]:
    """Role names spelled out as string constants inside one function body (its docstring excluded)."""
    tree = ast.parse(source, filename="<synthetic>" if "<" in function else str(MAIN))
    target = next((node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == function), None)
    assert target is not None, f"{function} is not in the source given"
    body = list(target.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]                      # the docstring may name roles in prose
    found = {node.value for node in ast.walk(ast.Module(body=body, type_ignores=[]))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    return found & role_names_in_schema()


def route_verbs(source=None) -> list[tuple[str, str]]:
    """(METHOD, path) for every route on the membership/invitation surface that the source declares.

    `source` lets a control feed the reader a route set of its own, which is the only way to show that a
    guard against a restored POST can see one. The verb is part of the fact 019 changed: the path
    /api/workspace/members still exists, only as a read.
    """
    body = ast.parse(MAIN.read_text(encoding="utf-8")).body if source is None else ast.parse(source).body
    found = []
    for node in body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app"
                    and decorator.args and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)):
                path = decorator.args[0].value
                if on_surface(path):
                    found.append((decorator.func.attr.upper(), path))
    return found


def routes_declared() -> set[str]:
    return {path for _, path in route_verbs()}


def test_the_panel_drives_the_routes_the_api_declares():
    declared = routes_declared()
    assert declared == DECLARED_ROUTES, sorted(declared)
    assert {(verb, path) for verb, path in route_verbs()} == DECLARED_ENDPOINTS, sorted(route_verbs())
    app = APP_JS.read_text(encoding="utf-8")
    for route in ("/api/workspace/members", "/api/workspace/members/transfer",
                  "/api/workspace/invitations", "/api/account/invitations",
                  "/api/account/invitations/accept", "/api/account/invitations/decline"):
        assert route in app, f"the roster no longer calls {route}"
    assert "/api/workspace/members/${member.user_id}" in app, (
        "the panel must address a member by id, and the id it uses is the user_id the roster lists")


def test_the_panel_selects_only_elements_the_document_defines():
    app = APP_JS.read_text(encoding="utf-8")
    document = INDEX_HTML.read_text(encoding="utf-8")
    missing = sorted(PANEL_IDS - set(re.findall(r'id="([A-Za-z0-9_-]+)"', document)))
    assert not missing, f"index.html lost roster elements: {missing}"
    dangling = dangling_selectors(app, document)
    assert not dangling, f"app.js selects roster elements that no document defines: {dangling}"


def test_the_element_reader_fires_on_a_selector_nothing_defines():
    """Both halves of the guard above read two files and subtract; an empty subtraction needs proving."""
    document = INDEX_HTML.read_text(encoding="utf-8")
    declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', document))
    assert sorted(PANEL_IDS - declared) == [], "the panel markup went missing, so the set above is not the decision"
    assert dangling_selectors("$('#teamList'); $('#inboxState'); $('#teamGhost');",
                              '<b id="teamList"></b><b id="inboxState"></b>') == ["teamGhost"]
    assert dangling_selectors("$('#teamList'); $('#inboxState');",
                              '<b id="teamList"></b><b id="inboxState"></b>') == []
    assert dangling_selectors("$('#erasureButton');", '<b id="teamList"></b>') == [], (
        "the reader reached outside the roster and the invitation panels")


def test_the_role_vocabulary_is_read_from_the_schema_not_retyped():
    main = MAIN.read_text(encoding="utf-8")
    assert literal_role_names(main, "allowed_workspace_roles") == set(), (
        "the panel's role list must come from pg_constraint; spelling the names out in Python is the "
        "second source of truth 017 warns about")
    assert "pg_get_constraintdef" in main, "allowed_workspace_roles no longer reads the constraint"
    # Control: a hand-copied list is reported, and the schema reader really does know the names.
    copied = ("def allowed_workspace_roles():\n"
              "    return ['owner', 'admin', 'creator']\n")
    assert literal_role_names(copied, "allowed_workspace_roles") == {"owner", "admin", "creator"}
    assert role_names_in_schema() >= {"owner", "admin", "creator", "reviewer", "viewer",
                                      "billing", "legal", "support"}
