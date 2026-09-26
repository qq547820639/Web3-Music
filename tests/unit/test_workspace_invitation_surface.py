"""Static guards on the 019 invitation surface: who may write, what the SQL must hold, and where the
token may and may not go.

The live proofs are the drills, which walk the running stack. What belongs in a no-stack test is the
shape a drill cannot see from outside: that 019's six functions each carry their own REVOKE/GRANT pair
and no table-level write privilege, that the liveness and address-binding decisions sit inside the
function rather than in the endpoint that calls it, that the plaintext token never reaches an audit
row, and that it is minted from the CSPRNG and hashed before it reaches SQL.

Each guard ships with the sample that must make it fire. A detector that has never been seen to report
anything is indistinguishable from a detector that cannot.

One limit stated rather than papered over: everything here reads source text and ASTs, so a statement
assembled at runtime is invisible to it -- exactly as in tests/unit/test_workspace_membership_writes.py,
whose docstring records that the database, not grep, is what closes that hole. The function-body slices
are delimited by the migration's own CREATE/REVOKE/GRANT keywords; a privilege statement written inside
a dollar-quoted body would confuse them, and 019 has no such statement.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "services/api/app/main.py"
INVITATIONS = ROOT / "db/migrations/019_workspace_invitations.sql"
MEMBERSHIP = ROOT / "db/migrations/017_workspace_membership.sql"
APP_JS = ROOT / "services/web/app.js"

# The six functions 019 adds. Two more are declared in the file (the update guard and the e-mail-change
# trigger); the database calls those, so they are revoked from PUBLIC and never granted to the app role.
INVITATION_FUNCTIONS = ("create_workspace_invitation", "workspace_invitation_list",
                        "my_workspace_invitations", "revoke_workspace_invitation",
                        "decline_workspace_invitation", "accept_workspace_invitation")
# 017's writers that 019 must leave alone. add_workspace_member is the one it takes away.
SURVIVING_WRITERS = {"change_workspace_member_role", "remove_workspace_member",
                     "transfer_workspace_ownership"}
# The plaintext secret's name on both sides of the wire: bound in the route, and the body's field.
TOKEN_NAME = "token"
SURFACE = ("/api/workspace/members", "/api/workspace/invitations", "/api/account/invitations")

CREATE = re.compile(r"^\s*CREATE OR REPLACE FUNCTION\s+(\w+)\s*\(", re.IGNORECASE | re.MULTILINE)
# A body runs from its CREATE to the next statement that changes what anyone may call: another
# declaration, or the first REVOKE/GRANT. Never a line number.
BOUNDARY = re.compile(r"^\s*(?:CREATE OR REPLACE FUNCTION|REVOKE\b|GRANT\b)", re.IGNORECASE | re.MULTILINE)
CSPRNG_CALLS = {"secrets.token_urlsafe", "secrets.token_bytes", "secrets.token_hex"}
WEAK_SECRETS = re.compile(r"(?:^|\.)(uuid4|uuid1|random|randint|randrange|choice|md5|sha1)$")
TABLE_GRANT = re.compile(r"^\s*GRANT\s+([A-Za-z\s,]+?)\s+ON\s+(?!FUNCTION\b)([A-Za-z_][\w.]*)\s+TO\s+(\w+)",
                         re.IGNORECASE | re.MULTILINE)
APP_REVOKED = re.compile(r"REVOKE\s+[^;]*?ON\s+FUNCTION\s+(\w+)\s*\([^)]*\)\s+FROM\s+music_app\b",
                         re.IGNORECASE | re.DOTALL)
APP_GRANTED = re.compile(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+(\w+)\s*\([^)]*\)\s+TO\s+music_app\b",
                         re.IGNORECASE)
# The three predicates that decide "this invitation is still alive", the two spellings of the address
# binding, and the enumeration shape. All read inside one function's own body, not anywhere in the file.
LIVENESS = {"used": r"\bused_at\s+IS\s+NOT\s+NULL\b",
            "withdrawn": r"\brevoked_at\s+IS\s+NOT\s+NULL\b",
            "expiry": r"\bexpires_at\s*<=\s*now\(\)"}
ADDRESS_BOUND = re.compile(r"\b(?:account\.email|own)\s+(?:<>|!=|IS\s+DISTINCT\s+FROM)\s+lower\(\s*row\.email\s*\)",
                           re.IGNORECASE)
# 017:58's shape: the account table itself read by address. A join that starts from workspace_members
# and happens to carry users.email in its predicate answers "is this address already in my roster" --
# which the caller could already read off GET /api/workspace/members -- so it is deliberately not matched.
ADDRESS_RESOLVED = re.compile(r"\bFROM\s+users\b[^;]*?\blower\s*\(\s*(?:[a-z_]+\.)?email\b", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------- the readers ----

def migration_bodies(sql_text: str) -> dict:
    """name -> its source slices, each delimited by the migration's own keywords."""
    marks = list(BOUNDARY.finditer(sql_text))
    out = {}
    for index, mark in enumerate(marks):
        declared = CREATE.match(sql_text, mark.start())
        if not declared:
            continue
        end = marks[index + 1].start() if index + 1 < len(marks) else len(sql_text)
        out.setdefault(declared.group(1), []).append(sql_text[mark.start():end])
    return out


def one_body(sql_text: str, name: str) -> str:
    """The single body slice of `name`, refusing to guess when there is not exactly one."""
    slices = migration_bodies(sql_text).get(name, [])
    assert len(slices) == 1, f"{name} should be declared exactly once, found {len(slices)} times"
    return slices[0]


def without(text: str, pattern: str, label: str) -> str:
    """Drop one construct from a body, proving the construct was in the sample before it was mutated."""
    mutated, count = re.subn(pattern, "", text, count=1, flags=re.DOTALL)
    assert count == 1 and mutated != text, f"the {label} control found nothing to remove for {pattern!r}"
    return mutated


def privilege_problems(sql_text: str) -> list:
    """Every way 019's six functions could be shipped without the privileges that fence them."""
    problems = []
    for name in INVITATION_FUNCTIONS:
        slices = migration_bodies(sql_text).get(name, [])
        if len(slices) != 1:
            problems.append(f"{name} is declared {len(slices)} times, expected exactly one "
                            f"CREATE OR REPLACE FUNCTION")
            continue
        if "SECURITY DEFINER" not in slices[0].upper():
            problems.append(f"{name} is no longer SECURITY DEFINER, so it runs as whoever called it")
        if not re.search(r"REVOKE\s+(?:ALL|EXECUTE)\s+ON\s+FUNCTION\s+" + re.escape(name)
                         + r"\s*\([^)]*\)\s+FROM\s+PUBLIC\b", sql_text, re.IGNORECASE):
            problems.append(f"{name} has no REVOKE ... FROM PUBLIC line")
        if not re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+" + re.escape(name)
                         + r"\s*\([^)]*\)\s+TO\s+music_app\b", sql_text, re.IGNORECASE):
            problems.append(f"{name} has no GRANT EXECUTE ... TO music_app line")
    for privileges, table, grantee in TABLE_GRANT.findall(sql_text):
        words = {word.strip().upper() for word in privileges.split(",")}
        if not words <= {"SELECT"} or table != "workspace_invitations" or grantee != "music_app":
            problems.append("table-level privilege widened beyond the one SELECT 019 issues: "
                            f"GRANT {privileges.strip()} ON {table} TO {grantee}")
    return problems


def missing_liveness(body: str) -> list:
    """The liveness predicates a membership-writing body has to carry, by name, when absent."""
    return [label for label, pattern in LIVENESS.items() if not re.search(pattern, body, re.IGNORECASE)]


def dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def calls_named(node, names) -> list:
    return [call for call in ast.walk(node)
            if isinstance(call, ast.Call) and dotted(call.func) in names]


def module_functions(source=None) -> dict:
    body = (ast.parse(MAIN.read_text(encoding="utf-8")).body if source is None else ast.parse(source).body)
    return {node.name: node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def route(name: str, source=None):
    node = module_functions(source).get(name)
    assert node is not None, f"{name} is no longer a module-level function"
    return node


def token_bindings(node) -> set:
    """Names this route binds to a freshly minted CSPRNG value."""
    minted = set()
    for stmt in ast.walk(node):
        value = stmt.value if isinstance(stmt, ast.Assign) else getattr(stmt, "value", None)
        if not (isinstance(value, ast.Call) and dotted(value.func) in CSPRNG_CALLS):
            continue
        for target in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]):
            if isinstance(target, ast.Name):
                minted.add(target.id)
    return minted


def mentions_token(node) -> bool:
    """Does this route hold the plaintext at all, in a variable or as the body's field?"""
    return any((isinstance(node_, ast.Name) and node_.id == TOKEN_NAME)
               or (isinstance(node_, ast.Attribute) and node_.attr == TOKEN_NAME) for node_ in ast.walk(node))


def audits_reachable(name: str, source=None, depth: int = 3) -> list:
    """Every audit(...) call reachable from a route by following module-level calls."""
    functions = module_functions(source)
    assert name in functions, f"{name} is not a module-level function"
    found, seen, frontier = [], {name}, [functions[name]]
    for _ in range(depth):
        following = []
        for node in frontier:
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                callee = dotted(call.func)
                if callee == "audit":
                    found.append(call)
                elif callee in functions and callee not in seen:
                    seen.add(callee)
                    following.append(functions[callee])
        frontier = following
    return found


def token_leaks(name: str, source=None) -> list:
    """Plaintext-token names handed to an audit(...) call this route can reach."""
    watched = token_bindings(route(name, source)) | {TOKEN_NAME}
    leaks = []
    for call in audits_reachable(name, source):
        for sub in ast.walk(call):
            hit = (sub.id if isinstance(sub, ast.Name) else
                   sub.attr if isinstance(sub, ast.Attribute) else None)
            message = f"{name} hands the plaintext token {hit!r} to an audit(...) call"
            if hit in watched and message not in leaks:
                leaks.append(message)
    return leaks


def write_statements(node) -> list:
    """(function called, params tuple) per literal `SELECT ... FROM func(%s)` this route issues."""
    found = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        statement = next((arg.value for arg in call.args
                          if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                          and re.match(r"^\s*SELECT\b", arg.value, re.IGNORECASE)), None)
        if not statement:
            continue
        target = re.search(r"\bFROM\s+(\w+)\s*\(", statement, re.IGNORECASE)
        if not target:
            continue
        found += [(target.group(1), arg) for arg in call.args if isinstance(arg, ast.Tuple)]
    return found


def unwrapped_token(params) -> list:
    """Token references that reach the statement without passing through token_hash()."""
    found = []

    def walk(node):
        if isinstance(node, ast.Call) and dotted(node.func) == "token_hash":
            return                                     # digested on the way in: this is the shape we want
        if isinstance(node, ast.IfExp):
            # `token_hash(x.token) if x.token else None` -- the test asks whether there is one, it does
            # not carry its value into SQL.
            walk(node.body)
            walk(node.orelse)
            return
        if isinstance(node, ast.Name) and node.id == TOKEN_NAME:
            found.append(node.id)
            return
        if isinstance(node, ast.Attribute) and node.attr == TOKEN_NAME:
            found.append("." + node.attr)
            return
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(params)
    return found


def minting_problems(name: str, source=None) -> list:
    """Was the secret minted by the CSPRNG? Only ask this of a route that issues one."""
    node = route(name, source)
    problems = []
    if any(isinstance(call, ast.Call) and WEAK_SECRETS.search(dotted(call.func)) for call in ast.walk(node)):
        problems.append(f"{name} builds a secret from uuid4/random rather than the CSPRNG")
    if not calls_named(node, CSPRNG_CALLS):
        problems.append(f"{name} never calls secrets.token_urlsafe, so its token has no measured source")
    return problems


def hashing_problems(name: str, source=None) -> list:
    """A route that holds the plaintext has to digest it on every path into a 019 write statement."""
    node = route(name, source)
    statements = write_statements(node)
    if not statements:
        return [f"{name} issues no literal SELECT ... FROM func(%s) statement, so this guard read nothing"]
    if not mentions_token(node):
        return []
    problems = []
    for target, params in statements:
        if target not in INVITATION_FUNCTIONS:
            continue
        if not calls_named(params, {"token_hash"}):
            problems.append(f"{name} passes {target} a token that never went through token_hash()")
        problems += [f"{name} passes the plaintext token {reference!r} into {target}"
                     for reference in unwrapped_token(params)]
    return problems


def key(path: str) -> str:
    """One spelling for a path parameter, so the panel's `${...}` and the decorator's `{...}` compare."""
    return re.sub(r"(?:\$\{[^}]*\}|\{[^}]*\})", "{}", path)


def routes_with_methods() -> dict:
    """{normalised path: {methods}} for every membership/invitation route main.py declares."""
    declared = {}
    for node in module_functions().values():
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app"):
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            path = decorator.args[0].value
            if path.startswith(SURFACE):
                declared.setdefault(key(path), set()).add(decorator.func.attr.upper())
    return declared


def api_calls(source: str) -> list:
    """(path, method, call site) for every api(...) call whose target path is a literal.

    Two shapes a single path regex would miss, both of them in the real panel: the options object nests
    JSON.stringify({ email, role }) inside it, so the call is closed by walking to the matching paren
    rather than with a `[^}]*` class; and one handler picks its path with a ternary, so every /api
    literal inside the call is taken as a target it may address.
    """
    calls = []
    for match in re.finditer(r"\bapi\(", source):
        tail, depth, quote, index = [], 1, None, match.end()
        while index < len(source) and depth:
            char = source[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            tail.append(char)
            index += 1
        block = "".join(tail)
        verb = re.search(r"method:\s*['\"](\w+)['\"]", block)
        site = source[match.start():index].strip()
        for _, path in re.findall(r"(['\"`])(/api/[^'\"`]*)\1", block):
            calls.append((path, verb.group(1).upper() if verb else "GET", site))
    return calls


def undeclared_calls(calls, declared: dict) -> list:
    """Panel calls on the membership surface whose method the API does not serve at that path."""
    return [f"{method} {path} <- {site.splitlines()[0]}"
            for path, method, site in calls
            if path.startswith(SURFACE) and (key(path) not in declared or method not in declared[key(path)])]


# ------------------------------------------------------------------ privileges ----

def test_019_ships_its_six_functions_fully_fenced():
    problems = privilege_problems(INVITATIONS.read_text(encoding="utf-8"))
    assert not problems, "; ".join(problems)


def test_the_privilege_census_fires_on_a_function_that_lost_its_grant():
    sql_text = INVITATIONS.read_text(encoding="utf-8")
    stripped, count = re.subn(r"(?m)^GRANT EXECUTE ON FUNCTION accept_workspace_invitation\([^)]*\) TO music_app;\n",
                              "", sql_text, count=1)
    assert count == 1, "the control could not find the GRANT line it means to remove"
    assert privilege_problems(stripped) == [
        "accept_workspace_invitation has no GRANT EXECUTE ... TO music_app line"]
    assert privilege_problems(sql_text) == [], "the census must be quiet on the real file before its word on a mutant counts"


def test_the_privilege_census_fires_on_a_table_level_write_grant():
    sql_text = INVITATIONS.read_text(encoding="utf-8")
    widened = sql_text + "\nGRANT UPDATE ON workspace_invitations TO music_app;\n"
    assert privilege_problems(widened) == [
        "table-level privilege widened beyond the one SELECT 019 issues: "
        "GRANT UPDATE ON workspace_invitations TO music_app"], privilege_problems(widened)
    # The one table grant 019 does issue, and the vocabulary it never issues at all.
    assert TABLE_GRANT.findall(sql_text) == [("SELECT", "workspace_invitations", "music_app")]
    upper = sql_text.upper()
    assert "GRANT INSERT" not in upper and "GRANT UPDATE" not in upper and "GRANT DELETE" not in upper
    assert "GRANT ALL ON WORKSPACE_INVITATIONS" not in upper


# ------------------------------------------------------------------ liveness ----

def test_the_accept_function_holds_every_liveness_predicate():
    body = one_body(INVITATIONS.read_text(encoding="utf-8"), "accept_workspace_invitation")
    missing = missing_liveness(body)
    assert not missing, (
        "019 puts expiry and settlement in SQL on purpose, so the server clock is the only judge; a "
        f"predicate missing here is the endpoint's opinion replacing the database's: {missing}")


def test_the_liveness_reader_fires_on_a_body_that_dropped_the_expiry_check():
    body = one_body(INVITATIONS.read_text(encoding="utf-8"), "accept_workspace_invitation")
    assert missing_liveness(without(body, r"IF row\.expires_at <= now\(\) THEN.*?END IF;", "expiry")) == ["expiry"]
    assert missing_liveness(without(body, r"IF row\.used_at IS NOT NULL THEN.*?END IF;", "used")) == ["used"]
    assert missing_liveness(without(body, r"IF row\.revoked_at IS NOT NULL THEN.*?END IF;", "withdrawn")) == ["withdrawn"]
    # And the slice really is the function's: words that appear elsewhere in the file do not count.
    assert missing_liveness(one_body(INVITATIONS.read_text(encoding="utf-8"), "my_workspace_invitations")) == \
        ["used", "withdrawn", "expiry"]


# ------------------------------------------------------------------ the binding ----

def test_the_address_binding_is_in_the_functions_not_the_endpoint():
    sql_text = INVITATIONS.read_text(encoding="utf-8")
    assert ADDRESS_BOUND.search(one_body(sql_text, "accept_workspace_invitation")), (
        "accept no longer compares the session's own address with the invited one, which puts membership "
        "back on whoever holds the link -- the shape 019 says it is refusing to copy")
    assert ADDRESS_BOUND.search(one_body(sql_text, "decline_workspace_invitation")), (
        "a decline is a statement about someone's own invitation; without the comparison it is not")
    create = one_body(sql_text, "create_workspace_invitation")
    resolved = ADDRESS_RESOLVED.search(create)
    assert not resolved, (
        "creating an invitation looked an address up in `users` again, which is the membership probe "
        f"019 exists to remove: {resolved.group(0)}")


def test_the_binding_reader_fires_on_bodies_that_dropped_or_readded_the_comparisons():
    sql_text = INVITATIONS.read_text(encoding="utf-8")
    accept = one_body(sql_text, "accept_workspace_invitation")
    assert ADDRESS_BOUND.search(accept), "the reader must see the real comparison before its absence means anything"
    assert not ADDRESS_BOUND.search(without(accept, r"IF account\.email <> lower\(row\.email\) THEN.*?END IF;",
                                            "address binding"))
    decline = one_body(sql_text, "decline_workspace_invitation")
    assert not ADDRESS_BOUND.search(without(decline, r"IF own IS DISTINCT FROM lower\(row\.email\) THEN.*?END IF;",
                                            "decline binding"))
    resolving = ("CREATE OR REPLACE FUNCTION create_workspace_invitation(uuid, uuid, text, text, text)\n"
                 "LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$\n"
                 "DECLARE target record;\n"
                 "BEGIN\n"
                 "  SELECT id, status INTO target FROM users WHERE lower(email)=lower(coalesce(member_email,''));\n"
                 "  IF NOT FOUND THEN RAISE EXCEPTION 'no account with that email on this platform'; END IF;\n"
                 "END $$;\n")
    assert ADDRESS_RESOLVED.search(resolving), "the reader cannot see the 017:58 shape it claims to forbid"
    assert not ADDRESS_RESOLVED.search(one_body(sql_text, "create_workspace_invitation"))


# ------------------------------------------------------------------ the token ----

def test_the_api_never_audits_the_plaintext_token():
    main = MAIN.read_text(encoding="utf-8")
    assert audits_reachable("create_workspace_invitation_route", main), (
        "no audit(...) call is reachable from the create route, so this guard read nothing")
    assert token_bindings(route("create_workspace_invitation_route", main)) == {"token"}, (
        "the create route no longer binds a token variable, so the leak check below has no subject")
    for name in ("create_workspace_invitation_route", "accept_workspace_invitation_route",
                 "decline_workspace_invitation_route"):
        leaks = token_leaks(name, main)
        assert not leaks, "; ".join(leaks)


def test_the_audit_reader_fires_on_a_route_that_files_the_token():
    planted = '''
import secrets
def create_workspace_invitation_route(body, request, actor=Depends(get_actor)):
    token = secrets.token_urlsafe(32)
    return write_it(actor, token, "SELECT * FROM create_workspace_invitation(%s,%s)", (actor.user_id, token))

def write_it(actor, token, statement, params):
    with transaction() as conn, conn.cursor() as cur:
        audit(cur, actor, "workspace.invitation.create", "workspace_invitation", "x", {"token": token}, None)
    return None
'''
    assert token_leaks("create_workspace_invitation_route", planted) == [
        "create_workspace_invitation_route hands the plaintext token 'token' to an audit(...) call"]
    assert audits_reachable("create_workspace_invitation_route", planted), "the reader found no audit call to check"
    assert not token_leaks("create_workspace_invitation_route", MAIN.read_text(encoding="utf-8")), (
        "the real route has to be quiet on the same reader, or the planted report proves nothing")
    # The token may appear in the response-building extra= lambda and in the hashed argument. That is
    # the whole design: one copy out to the person who will hand it over, none into audit_events.
    real = ast.unparse(route("create_workspace_invitation_route"))
    assert "token" in real and "token_hash(token)" in real, real


def test_tokens_are_minted_by_the_csprng_and_hashed_before_sql():
    main = MAIN.read_text(encoding="utf-8")
    assert not minting_problems("create_workspace_invitation_route", main)
    for name in ("create_workspace_invitation_route", "accept_workspace_invitation_route"):
        problems = hashing_problems(name, main)
        assert not problems, f"{name}: " + "; ".join(problems)
    assert [target for target, _ in write_statements(route("create_workspace_invitation_route", main))] == \
        ["create_workspace_invitation"], "the create route no longer issues the 019 write statement"
    assert [target for target, _ in write_statements(route("accept_workspace_invitation_route", main))] == \
        ["accept_workspace_invitation"]


def test_the_token_reader_fires_on_a_raw_token_and_on_a_weak_secret():
    raw = '''
import secrets
def create_workspace_invitation_route(body, request, actor=Depends(get_actor)):
    token = secrets.token_urlsafe(32)
    return write_it(actor, "SELECT * FROM create_workspace_invitation(%s,%s)", (actor.user_id, token))

def write_it(actor, statement, params):
    return None
'''
    assert hashing_problems("create_workspace_invitation_route", raw) == [
        "create_workspace_invitation_route passes create_workspace_invitation a token that never went "
        "through token_hash()",
        "create_workspace_invitation_route passes the plaintext token 'token' into create_workspace_invitation"]
    assert not hashing_problems("create_workspace_invitation_route",
                                raw.replace("(actor.user_id, token)", "(actor.user_id, token_hash(token))"))
    # Wrapping the secret in something that is not the sanctioned digest still reaches SQL undigested:
    # both clauses fire, and the second one is what says the value on the wire is the stored one.
    assert hashing_problems("create_workspace_invitation_route",
                            raw.replace("(actor.user_id, token)", "(actor.user_id, token.encode())")) == [
        "create_workspace_invitation_route passes create_workspace_invitation a token that never went "
        "through token_hash()",
        "create_workspace_invitation_route passes the plaintext token 'token' into create_workspace_invitation"]
    # A hand-rolled digest: nothing plaintext reaches SQL, but the route stopped using token_hash().
    hand_rolled = raw.replace("(actor.user_id, token)", "(actor.user_id, hashlib.sha256(secret).hexdigest())") \
                     .replace("token = secrets.token_urlsafe(32)", "token = secrets.token_urlsafe(32)\n    secret = token")
    assert hashing_problems("create_workspace_invitation_route", hand_rolled) == [
        "create_workspace_invitation_route passes create_workspace_invitation a token that never went "
        "through token_hash()"]
    weak = raw.replace("token = secrets.token_urlsafe(32)", "token = str(uuid.uuid4())")
    assert minting_problems("create_workspace_invitation_route", weak) == [
        "create_workspace_invitation_route builds a secret from uuid4/random rather than the CSPRNG",
        "create_workspace_invitation_route never calls secrets.token_urlsafe, so its token has no measured source"]
    assert not minting_problems("create_workspace_invitation_route", raw)
    dice = raw.replace("token = secrets.token_urlsafe(32)", "token = random.choice(string.ascii_letters)")
    assert minting_problems("create_workspace_invitation_route", dice)[:1] == [
        "create_workspace_invitation_route builds a secret from uuid4/random rather than the CSPRNG"]


def test_the_token_reader_stays_in_its_lane():
    """A guard that flagged every route would be indistinguishable from one that cannot read."""
    no_token = '''
def decline_workspace_invitation_route(body, request, user=Depends(get_user)):
    return claim(request, user, "SELECT * FROM decline_workspace_invitation(%s,%s::uuid)",
                 (user.user_id, body.invitation_id))
'''
    assert hashing_problems("decline_workspace_invitation_route", no_token) == [], (
        "a write with no token in it must not be asked to hash one")
    no_statement = '''
def my_invitations(user=Depends(get_user)):
    return serialize({})
'''
    assert hashing_problems("my_invitations", no_statement) == [
        "my_invitations issues no literal SELECT ... FROM func(%s) statement, so this guard read nothing"]
    assert unwrapped_token(ast.parse("(a, b.c)").body[0].value) == []


# ------------------------------------------------------------------ the panel ----

def test_the_panel_sends_only_methods_the_api_declares():
    """No add-by-address POST is left in the browser: /api/workspace/members is a read now.

    The route 019 replaces it with is POST /api/workspace/invitations, and this is the guard that says
    the panel moved rather than that the API did.
    """
    calls = api_calls(APP_JS.read_text(encoding="utf-8"))
    problems = undeclared_calls(calls, routes_with_methods())
    assert not problems, "; ".join(problems)
    # Non-vacuity: the assertion above is quiet because the panel calls the invitation surface, not
    # because it stopped calling anything at all. Each of these is a call the deleted route used to be.
    on_surface = {(method, key(path)) for path, method, _ in calls if path.startswith(SURFACE)}
    assert on_surface >= {("GET", "/api/workspace/members"), ("POST", "/api/workspace/invitations"),
                          ("DELETE", "/api/workspace/invitations/{}"), ("GET", "/api/account/invitations"),
                          ("POST", "/api/account/invitations/accept"),
                          ("POST", "/api/account/invitations/decline")}, sorted(on_surface)
    assert not [site for path, method, site in calls
                if path == "/api/workspace/members" and method == "POST"], (
        "the roster is POSTing to the route 019 deleted; the reader reported no problem above, so it "
        "cannot see this one and both guards are lying")


def test_the_panel_reader_fires_on_the_string_the_deleted_code_used():
    deleted = "const result = await api('/api/workspace/members', { method: 'POST', body: JSON.stringify({ email, role }) });"
    reported = undeclared_calls(api_calls(deleted), routes_with_methods())
    assert reported == ["POST /api/workspace/members <- "
                        "api('/api/workspace/members', { method: 'POST', "
                        "body: JSON.stringify({ email, role }) })"], reported
    # The shapes that must stay quiet: the same path on a served method, the route that replaced the
    # deleted call, and an interpolated id -- which is how the panel addresses one member.
    for compliant in ("await api('/api/workspace/members');",
                      "await api('/api/workspace/invitations', { method: 'POST', body: '{}' });",
                      "await api(`/api/workspace/members/${member.user_id}`, { method: 'PATCH', body: '{}' });"):
        assert not undeclared_calls(api_calls(compliant), routes_with_methods()), compliant
    assert api_calls("await api('/api/projects', { method: 'POST' });") == [
        ("/api/projects", "POST", "api('/api/projects', { method: 'POST' })")], (
        "the reader stopped seeing calls outside its lane, so the assertions above would be vacuous")


# ------------------------------------------------------------------ 017's writers ----

def test_019_takes_exactly_one_function_from_the_application_role():
    revoked = set(APP_REVOKED.findall(INVITATIONS.read_text(encoding="utf-8")))
    assert revoked == {"add_workspace_member"}, (
        "019 was meant to close one door: adding a member by address without their agreement. The "
        f"revocations of music_app's EXECUTE are now {sorted(revoked)}")
    granted = set(APP_GRANTED.findall(MEMBERSHIP.read_text(encoding="utf-8")))
    assert SURVIVING_WRITERS <= granted, f"017 no longer grants the admin console's writers: {sorted(granted)}"
    assert not (SURVIVING_WRITERS & revoked), (
        "change/remove/transfer are what the roster and the ownership button call; revoking one of them "
        "from music_app turns the admin console into a 500 without deleting a line of Python")
    assert "add_workspace_member" in granted, (
        "017's GRANT is what 019's REVOKE acts on; if that pairing went away the census below would be "
        "keeping an empty set")


def test_the_revocation_census_fires_on_a_silently_broken_admin_console():
    widened = INVITATIONS.read_text(encoding="utf-8") + (
        "\nREVOKE EXECUTE ON FUNCTION transfer_workspace_ownership(uuid, uuid, uuid) FROM music_app;\n")
    revoked = set(APP_REVOKED.findall(widened))
    assert revoked - {"add_workspace_member"} == {"transfer_workspace_ownership"}, revoked
    assert SURVIVING_WRITERS & revoked == {"transfer_workspace_ownership"}, (
        "the reader cannot see a second revocation, so the guard above cannot fire either")
