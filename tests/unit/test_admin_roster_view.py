"""Static guards on the platform-admin workspace roster (018 + /api/admin/v12/workspaces).

The live proofs are scripts/member_drill.py (18 checks in that section, against the running stack) and
the browser gate's roster walk. What belongs in a no-stack test is what neither can see from outside:

  * that the two read functions in 018 arrive with their REVOKE/GRANT pair, their SECURITY DEFINER and
    a pinned search_path -- a DEFINER function without the last one is a hijack surface, not a guard;
  * that the guard inside them names `users.is_platform_admin` rather than the bare column, which
    plpgsql refuses outright in this shape (018 records the error it hit);
  * that the endpoints take the actor from the resolved session and never from the request;
  * and that the columns the console interpolates are columns 018 actually returns. Three languages
    spell the same fact -- plpgsql OUT list, the JSON the endpoint emits, the template string in
    admin.js -- and a rename on one side otherwise reaches an operator as a blank cell.

Every guard here ships with the sample that must make it fire.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/018_admin_roster.sql"
ROUTER = ROOT / "services/api/app/routers/admin_v12.py"
ADMIN_JS = ROOT / "services/admin/admin.js"
ADMIN_HTML = ROOT / "services/admin/index.html"
GATE = ROOT / "scripts/browser_a11y.py"

ROSTER_FUNCTIONS = {"platform_workspaces", "platform_workspace_members"}
ROSTER_ENDPOINTS = {"workspace_directory", "workspace_roster"}

FUNCTION = re.compile(r"^\s*CREATE OR REPLACE FUNCTION (\w+)\(", re.IGNORECASE | re.MULTILINE)
DECLARATION = re.compile(r"CREATE OR REPLACE FUNCTION \w+\([^)]*\).*?AS \$\$", re.IGNORECASE | re.DOTALL)


def sql_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def declared_functions(text: str) -> set[str]:
    return set(FUNCTION.findall(text))


def grants_declared(text: str) -> tuple[set[str], set[str]]:
    revoked = {m.group(1) for m in re.finditer(r"REVOKE ALL ON FUNCTION (\w+)\(", text, re.IGNORECASE)}
    granted = {m.group(1) for m in re.finditer(r"GRANT EXECUTE ON FUNCTION (\w+)\(", text, re.IGNORECASE)}
    return revoked, granted


def function_declarations(text: str) -> list[str]:
    """Each CREATE ... AS $$ header, so attributes can be read per function rather than per file."""
    return DECLARATION.findall(text)


def returns_columns(text: str, function: str) -> set[str]:
    header = re.search(r"CREATE OR REPLACE FUNCTION " + re.escape(function) + r"\([^)]*\)\s*"
                       r"RETURNS TABLE \(([^)]*)\)", text, re.IGNORECASE)
    assert header, f"{function}'s RETURNS TABLE list is unreadable in 018"
    return {name.strip().split()[0] for name in header.group(1).split(",") if name.strip()}


def admin_check_statements(text: str) -> list[str]:
    """Any line that reads the administrator flag into the guard variable, qualified or not.

    A `SELECT ... INTO` rather than a permissive match on the column name, because 018 also declares an
    OUT column called is_platform_admin and joins `u.is_platform_admin` out of users. Flagging those is
    the shape of detector that gets switched off by the next person who needs the noise gone.
    """
    return [line.strip() for line in text.splitlines()
            if re.search(r"\bINTO\s+admin\b", line, re.IGNORECASE)
            and not line.lstrip().startswith("--")]


def ambiguous_admin_checks(text: str) -> list[str]:
    """The guard reads above that do not name their table.

    `is_platform_admin` is both a column of `users` and an OUT column of the function guarding on it, so
    an unqualified use is a hard error from plpgsql today -- and the half-qualified form is the one that
    silently reads back its own NULL if the out-parameter is ever renamed.
    """
    return [line for line in admin_check_statements(text)
            if not re.search(r"\w+\.is_platform_admin\s+INTO\s+admin\b", line, re.IGNORECASE)]


def router_source() -> str:
    return ROUTER.read_text(encoding="utf-8")


def endpoint_nodes(source: str) -> list[ast.FunctionDef]:
    return [node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name in ROSTER_ENDPOINTS]


def reads_actor_user_id(node) -> bool:
    """Whether the node mentions `actor.user_id` as a node, not as a substring of a dump."""
    return any(isinstance(call, ast.Attribute) and call.attr == "user_id"
               and isinstance(call.value, ast.Name) and call.value.id == "actor"
               for call in ast.walk(node))


def actor_is_forged(node) -> bool:
    """True when a roster endpoint could be told who to answer as, from outside the session.

    Three shapes count as forgery: a parameter that reads like an actor id, no reference at all to the
    resolved session, and a call to one of 018's functions whose first argument is not that session.
    """
    params = {a.arg for a in node.args.args}
    if params & {"actor_user", "actor_id", "user_id", "as_user"}:
        return True
    if not reads_actor_user_id(node):
        return True
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and call.args
                and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
                and re.search(r"\bplatform_workspace", call.args[0].value)):
            arguments = call.args[1].elts if len(call.args) > 1 and isinstance(call.args[1], ast.Tuple) else []
            if not arguments or not reads_actor_user_id(arguments[0]):
                return True
    return False


def requires_platform_admin(node) -> bool:
    """Whether the endpoint resolves its actor through require_platform_admin.

    Searched over the whole node, not the decorators: FastAPI dependencies arrive as parameter defaults
    (`actor: Actor = Depends(require_platform_admin)`), which a decorator walk never sees -- the first
    version of this helper reported the real endpoints as unguarded for exactly that reason.
    """
    return any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
               and call.func.id == "Depends" and call.args
               and isinstance(call.args[0], ast.Name) and call.args[0].id == "require_platform_admin"
               for call in ast.walk(node))


def interpolations(source: str, function: str, aliases=("w", "m")) -> dict[str, set[str]]:
    """The `<alias>.<key>` reads inside one JS function body, keyed by alias.

    loadWorkspaces() renders two different shapes in one function -- `w` for a directory row, `m` for
    a member row -- so keeping the aliases apart is what makes the comparison against 018 mean
    something. Flattening them lets a roster column satisfy a directory column. Only the aliases that
    carry a template row are collected; `e.message` and `b.dataset` are error and event plumbing.
    """
    start = source.find(f"async function {function}(")
    if start < 0:
        return {}
    end = source.find("\nasync function ", start + 1)
    body = source[start:end if end > 0 else len(source)]
    found: dict[str, set[str]] = {}
    for alias, key in re.findall(r"\b(" + "|".join(aliases) + r")\.([a-z_]+)\b", body):
        found.setdefault(alias, set()).add(key)
    return found


def views_the_gate_opens(source: str) -> set[tuple[str, str]]:
    """Every (label, marker) the browser gate navigates to, from both shapes it uses."""
    opened = {tuple(pair) for pair in
              re.findall(r'goto_view\(page,\s*"([^"]+)",\s*"(view-[a-z-]+)"\)', source)}
    admin_block = re.search(r'"adminNav":\s*\((.*?)\)\}', source, re.DOTALL)
    assert admin_block, "walk_views no longer spells out an adminNav list, so the console has no denominator"
    opened |= {tuple(pair) for pair in
               re.findall(r'\("([^"]+)",\s*"(view-[a-z-]+)"\)', admin_block.group(1))}
    return opened


def nav_buttons(document: str) -> dict[str, str]:
    """data-view -> aria-label, for one console document."""
    return dict(re.findall(r'<button[^>]*data-view="([a-z-]+)"[^>]*aria-label="([^"]+)"', document))


def panels_refusal_targets(source: str) -> list[str]:
    """Every container the refusal path in refreshAll() writes into, in declaration order."""
    block = re.search(r"const PANELS = \[(.*?)\n\];", source, re.DOTALL)
    assert block, "refreshAll() no longer declares which containers its loaders own"
    return re.findall(r"'(#[A-Za-z0-9_-]+)'", block.group(1))


# ------------------------------------------------------------------ 018 ----

def test_018_ships_both_read_functions_with_their_grants():
    declared = declared_functions(sql_text())
    revoked, granted = grants_declared(sql_text())
    assert declared, "the parser found no functions, so this assertion would be vacuous"
    assert ROSTER_FUNCTIONS <= declared, sorted(ROSTER_FUNCTIONS - declared)
    missing_grant = sorted((ROSTER_FUNCTIONS & declared) - granted)
    missing_revoke = sorted((ROSTER_FUNCTIONS & declared) - revoked)
    assert not missing_grant, f"declared but never granted to music_app: {missing_grant}"
    assert not missing_revoke, f"declared but left open to PUBLIC: {missing_revoke}"


def test_the_grant_census_fires_on_a_read_function_shipped_without_grants():
    sample = ("CREATE OR REPLACE FUNCTION platform_sneaky(uuid) RETURNS void AS $$ SELECT 1 $$;\n"
              "REVOKE ALL ON FUNCTION platform_workspaces(uuid) FROM PUBLIC;\n"
              "GRANT EXECUTE ON FUNCTION platform_workspaces(uuid) TO music_app;\n")
    declared = declared_functions(sample)
    revoked, granted = grants_declared(sample)
    assert declared == {"platform_sneaky"}
    assert declared - granted == {"platform_sneaky"}
    assert declared - revoked == {"platform_sneaky"}


def test_every_018_function_is_definer_with_a_pinned_search_path():
    declarations = function_declarations(sql_text())
    assert len(declarations) == 2, f"018 should declare two functions, read {len(declarations)}"
    for header in declarations:
        assert "SECURITY DEFINER" in header, header
        assert re.search(r"SET search_path=public", header), header


def test_the_definition_guard_fires_on_a_function_that_could_be_hijacked():
    loose = ("CREATE OR REPLACE FUNCTION platform_loose(uuid) RETURNS void "
             "LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$ SELECT 1 $$;")
    assert "SECURITY DEFINER" in function_declarations(loose)[0]
    assert not re.search(r"SET search_path=public", function_declarations(loose)[0])


def test_the_administrator_check_names_its_table():
    guards = admin_check_statements(sql_text())
    assert len(guards) == 2, f"018 should guard in two functions, read {guards}"
    offenders = ambiguous_admin_checks(sql_text())
    assert not offenders, f"unqualified is_platform_admin guard in 018: {offenders}"


def test_the_qualification_detector_fires_on_the_ambiguous_shape():
    bare = ("DECLARE admin boolean;\n"
            "BEGIN\n"
            "  SELECT is_platform_admin INTO admin FROM users WHERE id = actor_user;\n"
            "  SELECT u.is_platform_admin, member_role FROM users u;\n")
    assert admin_check_statements(bare) == ["SELECT is_platform_admin INTO admin FROM users WHERE id = actor_user;"]
    assert ambiguous_admin_checks(bare) == admin_check_statements(bare)
    assert ambiguous_admin_checks(sql_text()) == []


# ------------------------------------------------------------------ the endpoints ----

def test_the_roster_endpoints_answer_as_the_session_and_nobody_else():
    nodes = endpoint_nodes(router_source())
    assert {node.name for node in nodes} == ROSTER_ENDPOINTS, "both roster endpoints must still exist"
    offenders = sorted(node.name for node in nodes if actor_is_forged(node))
    assert not offenders, f"roster endpoints that take an actor from outside the session: {offenders}"


def test_the_forgery_detector_fires_on_an_endpoint_that_trusts_the_caller():
    trusting = '''
@router.get("/workspaces/{workspace_id}/members")
def workspace_roster(workspace_id: str, actor_user: str):
    return fetch_all("SELECT * FROM platform_workspace_members(%s,%s::uuid)", (actor_user, workspace_id))
'''
    node = endpoint_nodes(trusting)[0]
    assert actor_is_forged(node), "an endpoint with an actor_user parameter must read as forgeable"
    anonymous = '''
@router.get("/workspaces")
def workspace_directory(request):
    return fetch_all("SELECT * FROM platform_workspaces(NULL)")
'''
    assert actor_is_forged(endpoint_nodes(anonymous)[0])
    assert actor_is_forged(endpoint_nodes(router_source())[0]) is False


def test_both_roster_routes_declare_the_platform_administrator_dependency():
    missing = sorted(node.name for node in endpoint_nodes(router_source()) if not requires_platform_admin(node))
    assert not missing, f"roster endpoints with no require_platform_admin: {missing}"
    open_route = '@router.get("/workspaces")\ndef workspace_directory(actor):\n    return None\n'
    assert requires_platform_admin(endpoint_nodes(open_route)[0]) is False, \
        "the detector never fires, so the guard above proves nothing"
    tenant_only = ('@router.get("/workspaces")\n'
                   'def workspace_directory(actor: Actor = Depends(get_actor)):\n    return None\n')
    assert requires_platform_admin(endpoint_nodes(tenant_only)[0]) is False, (
        "an endpoint that resolves the workspace role but not the platform flag reads as guarded")


# ------------------------------------------------------------------ the console ----

def test_the_console_reads_columns_018_actually_returns():
    """Two half-copies of one column list, three languages apart."""
    read = interpolations(ADMIN_JS.read_text(encoding="utf-8"), "loadWorkspaces")
    directory = returns_columns(sql_text(), "platform_workspaces")
    roster = returns_columns(sql_text(), "platform_workspace_members")
    assert read.get("w") and read.get("m"), f"the console reads no template columns: {read}"
    assert read["w"] <= directory, f"the directory template reads {sorted(read['w'] - directory)}"
    assert read["m"] <= roster, f"the roster template reads {sorted(read['m'] - roster)}"
    # The other direction is the one a reviewer cares about: 018 is the wider statement, and a column
    # it returns that nobody renders is a promise the console quietly broke. `account_status` and
    # `is_platform_admin` are the two the operator is meant to be able to see.
    for column in ("account_status", "is_platform_admin", "member_role"):
        assert column in read["m"], f"018 still returns {column}, but the console stopped rendering it"


def test_the_column_census_fires_on_a_renamed_column():
    js = ("async function loadWorkspaces() {\n"
          "  return w.workspace_name + m.member_role + w.name_that_does_not_exist;\n"
          "}\n")
    read = interpolations(js, "loadWorkspaces")
    assert read["w"] == {"workspace_name", "name_that_does_not_exist"}
    assert read["w"] - returns_columns(sql_text(), "platform_workspaces") == {"name_that_does_not_exist"}
    assert not read["m"] - returns_columns(sql_text(), "platform_workspace_members")


def test_the_workspaces_view_the_gate_opens_is_a_view_the_document_defines():
    """Coverage in both directions: a control-plane view the console renders but the gate never opens
    is unaudited, and a view the gate opens that the console no longer defines is a walk that dies at
    the click."""
    document = ADMIN_HTML.read_text(encoding="utf-8")
    buttons = nav_buttons(document)
    assert buttons, "no nav button with an aria-label could be read from the control plane"
    gate = views_the_gate_opens(GATE.read_text(encoding="utf-8"))
    assert ("工作区与成员", "view-workspaces") in gate, sorted(gate)
    opened = {marker[5:] for _, marker in gate if marker[5:] in buttons}
    never = sorted(set(buttons) - opened - {"overview"})
    assert not never, (f"control-plane views the gate never opens: {never} "
                       "(overview is the landing view, reached by logging in)")
    for label, marker in gate:
        if marker[5:] not in buttons:
            continue                       # a Studio view; it belongs to services/web/index.html
        assert f'id="{marker}"' in document, f"the gate opens {marker}, which index.html does not define"
        assert buttons[marker[5:]] == label + "视图", (
            f"{marker} is announced as {buttons[marker[5:]]!r} while the gate hunts for {label!r} + 视图, "
            "so get_by_role(name=...) is looking for text the console never renders")


def test_the_view_census_fires_on_a_nav_entry_that_only_exists_in_the_gate():
    synthetic = '<nav><button data-view="ghost" aria-label="幽灵页">幽灵</button></nav>'
    buttons = nav_buttons(synthetic)
    assert buttons == {"ghost": "幽灵页"}, buttons
    assert 'id="view-ghost"' not in synthetic          # the pane the gate would wait for is absent
    assert buttons["ghost"] != "幽灵" + "视图"           # and so is the accessible name it hunts
    real = ADMIN_HTML.read_text(encoding="utf-8")
    assert nav_buttons(real).get("workspaces") == "工作区与成员视图"


def test_the_refusal_path_only_writes_into_containers_that_exist():
    """A typo in one of these selectors does not fail loudly: querySelector returns null, the refusal
    handler then throws inside the failure path, and the operator is left staring at the numbers that
    were just invalidated."""
    js = ADMIN_JS.read_text(encoding="utf-8")
    document = ADMIN_HTML.read_text(encoding="utf-8")
    targets = panels_refusal_targets(js)
    assert len(targets) >= 9, f"the refusal path covers {len(targets)} containers, expected one per panel: {targets}"
    declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', document))
    missing = sorted({name[1:] for name in targets} - declared)
    assert not missing, f"admin.js would write refusals into ids the document never defines: {missing}"
    written = {f"#{found}" for found in re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js)}
    unowned = sorted(set(targets) - written)
    assert not unowned, f"{unowned} are named as loader containers but no loader writes to them"


def test_the_container_census_fires_on_a_selector_nobody_owns():
    js = ("const PANELS = [\n"
          "  [loadOverview, ['#stats', '#no_such_grid']],\n"
          "];\n"
          "function other() { return $('#stats').innerHTML; }\n")
    targets = panels_refusal_targets(js)
    written = {f"#{found}" for found in re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js)}
    assert "#no_such_grid" in targets and "#no_such_grid" not in written
    assert "#stats" in targets and "#stats" in written


def test_a_refused_panel_is_labelled_instead_of_left_showing_stale_numbers():
    js = ADMIN_JS.read_text(encoding="utf-8")
    assert "allSettled" in js, \
        "refreshAll is back to Promise.all: one failed load rejects the group, the boot handler hides " \
        "#app, and a session that is still valid is bounced to the login form"
    assert "未能载入" in js, "the refusal path no longer labels the panels it empties"
    assert re.search(r"\$\(selector\)\.innerHTML = note", js), \
        "nothing replaces the panel content on refusal, so the previous render stays on screen"
