"""Static guards on the step-up wall in front of the destructive writes.

The live proofs are in the drills: scripts/erasure_drill.py, scripts/member_drill.py and
scripts/mfa_drill.py each refuse-without-a-credential and refuse-with-a-wrong-one against a running
stack, and scripts/browser_a11y.py proves the fields are load-bearing in the page rather than
decoration. What belongs in a no-stack test is the shape a drill cannot see from outside: that the
set of guarded routes is the set someone decided on, that every one of them takes the credential from
the request body rather than from a session stamp, and that a refusal is recorded against the account.

Each detector ships with the sample that must make it fire, in this repo's convention that a guard
never seen to report anything is indistinguishable from a guard that cannot.

One limit stated rather than papered over: the route-level guard reads the endpoint body for the
helper call. An endpoint that reached the same refusal through a wrapper it called itself would not
be seen, exactly as any static reader misses indirection. The drills close that by issuing the real
request and checking the row afterwards.
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from authority_matrix import calls_step_up, derive  # noqa: E402

MAIN = ROOT / "services/api/app/main.py"
APP_JS = ROOT / "services/web/app.js"
INDEX = ROOT / "services/web/index.html"

# method+path -> the endpoint function that serves it. The list is the decision: an irreversible or
# privilege-removing write is confirmed against the credential, an additive one (inviting a member)
# is not, and a route added to one side has to be argued for on the other.
STEP_UP_ROUTES = {
    "POST /api/account/erasure": "account_erasure",
    "POST /api/auth/mfa/disable": "mfa_disable",
    "POST /api/workspace/members/transfer": "transfer_workspace_ownership",
    "PATCH /api/workspace/members/{member_id}": "change_workspace_member_role",
    "DELETE /api/workspace/members/{member_id}": "remove_workspace_member",
}
STEP_UP_MODELS = ("ErasureBody", "MfaDisableBody", "MemberRoleBody", "MemberRemoveBody", "MemberTransferBody")


def tree():
    return ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))


def function(name, source=None):
    body = tree().body if source is None else ast.parse(source).body
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a module-level function" + (" in main.py" if source is None else ""))


def classes():
    return {node.name: node for node in tree().body if isinstance(node, ast.ClassDef)}


def call_names(node):
    return {call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}


def refusals(node):
    """(status, counted) for every _step_up_refused call in a function, in source order."""
    out = []
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "_step_up_refused"):
            continue
        counted = next((bool(kw.value.value) for kw in call.keywords if kw.arg == "counted"), True)
        status = call.args[3].value if len(call.args) > 3 and isinstance(call.args[3], ast.Constant) else None
        out.append((status, counted))
    return sorted(out)


def js_function(name):
    """The text of a top-level `function name(` / `async function name(` in app.js."""
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"\n(?:async )?function " + re.escape(name) + r"\(.*?(?=\n\})", source, re.DOTALL)
    assert match, f"{name} is not a top-level function in app.js"
    return match.group(0)


# ------------------------------------------------------------------ routes ----

def test_the_matrix_flags_exactly_the_destructive_writes():
    flagged = {f"{row['method']} {row['path']}" for row in derive() if row["step_up"]}
    assert flagged == set(STEP_UP_ROUTES), (
        f"guarded routes drifted: extra={sorted(flagged - set(STEP_UP_ROUTES))} "
        f"missing={sorted(set(STEP_UP_ROUTES) - flagged)}")
    assert not [row for row in derive() if row["method"] == "GET" and row["step_up"]], \
        "a read should never ask for a credential"


def test_the_step_up_reader_fires_on_a_call_and_stays_out_of_everything_else():
    guarded = ast.parse("def endpoint():\n    step_up_factors(request, 'u', 'k', None, None)\n    return None\n")
    assert [calls_step_up(node) for node in guarded.body] == [True]
    plain = ast.parse("def endpoint():\n    return Depends(get_user)\n")
    assert [calls_step_up(node) for node in plain.body] == [False]
    # A dependency named the same way as the helper must not count: the wall is in the body or it
    # is not there.
    lookalike = ast.parse("def endpoint(user=Depends(step_up_factors)):\n    return None\n")
    assert calls_step_up(lookalike.body[0]) is False


def test_every_guarded_route_takes_the_credential_from_its_body():
    for route, name in STEP_UP_ROUTES.items():
        node = function(name)
        assert "step_up_factors" in call_names(node), route
        dumped = ast.dump(node)
        assert "password" in dumped, f"{route} calls the helper without a body password"


def test_the_guarded_models_inherit_the_credential_fields():
    declared = classes()
    for model in STEP_UP_MODELS:
        assert model in declared, f"{model} is no longer a module-level model"
        bases = [base.id for base in declared[model].bases if isinstance(base, ast.Name)]
        assert "StepUp" in bases, f"{model} stopped carrying the step-up fields: {bases}"


def test_the_model_reader_fires_on_a_body_that_dropped_the_fields():
    planted = "class ErasureBody(BaseModel):\n    confirmation: str\n"
    bases = [base.id for base in ast.parse(planted).body[0].bases if isinstance(base, ast.Name)]
    assert "StepUp" not in bases, "the reader cannot see a model that reverted to BaseModel"


# ------------------------------------------------------------------ verdicts ----

def test_a_missing_credential_is_a_challenge_and_a_wrong_one_is_a_failure():
    """401 means "collect it and send it again", 403 means "what you sent was wrong".

    Collapsing the two would put Pydantic's 422 or a bare 401 in charge of a security decision, and
    a client could not tell a first attempt from a brute force.
    """
    assert refusals(function("step_up_factors")) == [(401, False), (403, True), (403, True)]


def test_the_verdict_reader_fires_on_a_wrong_password_answered_as_a_challenge():
    planted = ("def step_up_factors(request, user_id, kind, password, code):\n"
               "    if not verify_password(password, 'x'):\n"
               "        raise _step_up_refused(request, user_id, kind, 401, 'needs your password')\n"
               "    return ['password']\n")
    assert refusals(ast.parse(planted).body[0]) == [(401, True)]


def test_only_wrong_credentials_count_against_the_window():
    """Six guesses a minute, and a manager who confirms seven actions is not one of them.

    The challenge path passes counted=False; if that ever flips, the roster becomes unusable for a
    minute after a few honest clicks, and the drill that measures it is scripts/member_drill.py.
    """
    gate = function("step_up_factors")
    # Skip the docstring: the guard is about the first statement that does something, and a function
    # that gains a comment must not read as one that lost its throttle.
    actionable = [stmt for stmt in gate.body
                  if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
    first = actionable[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call) \
        and getattr(first.value.func, "id", "") == "_step_up_gate", \
        "the window is not consulted before anything is read"
    counted = [status for status, is_counted in refusals(gate) if not is_counted]
    assert counted == [401], f"exactly one refusal may be free of the counter: {counted}"
    assert any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
               and call.func.attr == "delete" for call in ast.walk(gate)), \
        "a correct credential never clears the window, so successes would accumulate"


def test_denials_are_written_against_the_account_and_not_as_system():
    """audit(cur, None, ...) would file the event as actor_id='system'.

    The caller did hold a valid session; the row's whole purpose is to say that whoever held it could
    not re-produce its credential, so it has to name the account.
    """
    node = function("_step_up_refused")
    assert "Actor" in call_names(node), "the denial audit row carries no actor"
    assert "auth.step_up.denied" in MAIN.read_text(encoding="utf-8")


def test_the_actor_reader_fires_on_an_anonymous_denial():
    planted = ('def _step_up_refused(request, user_id, kind, status, reason, counted=True):\n'
               '    audit(cur, None, "auth.step_up.denied", "user", user_id, {}, None)\n')
    assert "Actor" not in call_names(ast.parse(planted).body[0])


# ------------------------------------------------------------------ the surface ----

def test_the_pages_send_the_credential_from_every_destructive_handler():
    for name in ("memberChange", "memberRemove", "ownershipTransfer"):
        body = js_function(name)
        assert "stepUpFields()" in body, f"{name} writes without asking for a credential"
        assert "stepUpBody(answer)" in body, f"{name} collects a credential it never sends"
    erasure = re.search(r"\$\('#erasureButton'\)\.onclick = async \(\) => \{.*?(?=\n\};)",
                        APP_JS.read_text(encoding="utf-8"), re.DOTALL)
    assert erasure, "the erasure button has no handler to read"
    assert "password: $('#erasurePassword').value" in erasure.group(0), \
        "the privacy panel collects a password it never sends"
    disable = re.search(r"\$\('#mfaDisable'\)\.onclick = async \(\) => \{.*?(?=\n\};)",
                        APP_JS.read_text(encoding="utf-8"), re.DOTALL)
    assert disable and "password, code" in disable.group(0), \
        "dropping the second factor can be done with a code alone"


def test_the_erasure_and_disable_panels_ask_for_the_password():
    index = INDEX.read_text(encoding="utf-8")
    for field in ("erasurePassword", "mfaDisablePassword"):
        label = re.search(r'<label>[^<]*<input id="' + field + r'"[^>]*>', index)
        assert label, f"#{field} is missing from index.html"
        markup = label.group(0)
        assert 'type="password"' in markup, f"#{field} would echo the credential on screen"
        assert 'autocomplete="current-password"' in markup, f"#{field} is not a password managers can fill"
    app = APP_JS.read_text(encoding="utf-8")
    for field in ("erasurePassword", "mfaDisablePassword"):
        assert f"$('#{field}')" in app, f"#{field} is markup nothing reads"


def test_the_second_factor_field_appears_only_for_an_account_that_has_one():
    """An unarmed account must not be asked to produce a code it cannot produce."""
    index = INDEX.read_text(encoding="utf-8")
    assert re.search(r'<label id="erasureCodeWrap" hidden>', index), "the code field is visible by default"
    render = js_function("renderMfa")
    assert "state.mfaArmed = armed" in render, "the panel never learns whether a code is owed"
    assert "$('#erasureCodeWrap').hidden = !armed" in render, "the code field is not driven by that flag"
    assert "if (state.mfaArmed)" in js_function("stepUpFields")
