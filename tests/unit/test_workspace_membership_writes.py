"""Static guards on the workspace-membership write path.

The live proofs are scripts/member_drill.py: 39 checks against the running stack, including that
music_app is refused when it writes `workspace_members` itself and that 017's functions refuse a
forged actor. What belongs in a no-stack test is the shape a drill cannot see from outside: that no
code path in the API writes the table, that every membership endpoint resolves an actor, and that
every function 017 declares carries its REVOKE/GRANT pair.

Each guard ships with the sample that must make it fire. A detector that has never been seen to
report anything is indistinguishable from a detector that cannot.

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
MEMBERSHIP_WRITE = re.compile(r"\b(add|change|remove|transfer)_workspace_\w+\b")
MEMBERSHIP_FUNCTIONS = {"add_workspace_member", "change_workspace_member_role",
                        "remove_workspace_member", "transfer_workspace_ownership"}


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
    """The paths a function serves, if it serves any under /api/workspace/members."""
    if not isinstance(node, ast.FunctionDef):
        return []
    routes = [d for d in node.decorator_list
              if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
              and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"]
    paths = [d.args[0].value for d in routes if d.args and isinstance(d.args[0], ast.Constant)]
    return [path for path in paths if "/workspace/members" in path]


def unguarded(node) -> bool:
    """True when a membership endpoint resolves nobody: no get_actor, no require_roles."""
    if not membership_routes(node):
        return False
    dumped = ast.dump(node.args)
    return not any(name in dumped for name in ("get_actor", "require_roles"))


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
    assert len(calls) == 4, f"expected exactly one SQL string per 017 write function: {calls}"
    for sql in calls:
        assert sql.startswith("SELECT * FROM "), sql
    assert {MEMBERSHIP_WRITE.search(sql).group(0) for sql in calls} == MEMBERSHIP_FUNCTIONS


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


def routes_declared() -> set[str]:
    paths = set()
    for node in statements(MAIN):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app"
                        and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                    value = decorator.args[0].value
                    if "/workspace/members" in value:
                        paths.add(value)
    return paths


def test_the_panel_drives_the_routes_the_api_declares():
    declared = routes_declared()
    assert declared == {"/api/workspace/members", "/api/workspace/members/{member_id}",
                        "/api/workspace/members/transfer"}, sorted(declared)
    app = APP_JS.read_text(encoding="utf-8")
    for route in ("/api/workspace/members", "/api/workspace/members/transfer"):
        assert route in app, f"the roster no longer calls {route}"
    assert "/api/workspace/members/${member.user_id}" in app, (
        "the panel must address a member by id, and the id it uses is the user_id the roster lists")


def test_the_panel_selects_only_elements_the_document_defines():
    app = APP_JS.read_text(encoding="utf-8")
    document = INDEX_HTML.read_text(encoding="utf-8")
    declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', document))
    team_ids = {"teamState", "teamList", "teamManage", "teamEmail", "teamAddRole", "teamAdd", "teamError"}
    missing = sorted(team_ids - declared)
    assert not missing, f"index.html lost roster elements: {missing}"
    dangling = sorted({found for found in re.findall(r"\$\('#(team[A-Za-z0-9]*)'\)", app)} - declared)
    assert not dangling, f"app.js selects roster elements that no document defines: {dangling}"


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
