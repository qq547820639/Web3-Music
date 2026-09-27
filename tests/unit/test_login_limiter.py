"""Static guards on the login window: what is counted, by whom, and what a refusal costs.

The live proofs are in scripts/mfa_drill.py, which reads the counter in the shared store -- it fills
the window with wrong passwords, requires that twelve successful logins left nothing behind, requires
that three further refusals did not move the stored count, and requires that the TTL *decayed* while
the key was still being knocked on. That last reading is the one no static reader can make, and it is
the defect this round closed: the window used to be "sixty seconds since the last knock" because EXPIRE
ran on every INCR, refused ones included.

What belongs in a no-stack test is the wiring those readings depend on: that the window is consulted
before anything is read, that the only place which spends it is the failed-credential branch, that the
gate itself writes nothing, and that the limit is the configured one rather than a number in a body.

Each detector ships with the sample that must make it fire. A guard never seen reporting anything is
indistinguishable from a guard that cannot.
"""
import ast
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[2] / "services/api/app/main.py"
TREE = ast.parse(MAIN.read_text(encoding="utf-8"))
REAL = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}

# Both the plain and the attribute form, because the two ways to extend a Redis key in this module are
# `_mfa_incr(...)` and `rq.expire(...)`, and a reader that only looked at Names would miss the second.
WRITERS = {"incr", "expire", "set", "delete", "_mfa_incr", "_login_failed", "_login_incr"}


def functions(source: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def actionable(fn: ast.AST) -> list[ast.stmt]:
    """Statements that do something -- so a function that merely gains a docstring cannot read as one
    that lost its throttle."""
    return [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]


def call_names(fn: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                out.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                out.append(node.func.attr)
    return out


def spending_branch(fn: ast.FunctionDef, target: str) -> ast.stmt | None:
    """The nearest If that contains a call to `target`, or None when the call is unconditional."""
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and getattr(child.func, "id", getattr(child.func, "attr", "")) == target):
                    return node
    return None


def first_write_index(fn: ast.FunctionDef, target: str) -> int:
    """The index of the first actionable statement that calls `target`, or -1."""
    for i, stmt in enumerate(actionable(fn)):
        if target in call_names(stmt):
            return i
    return -1


def test_the_window_is_consulted_before_the_account_is_read():
    """A gate behind the credential check is not a gate: it would only ever see traffic that already
    spent a database round trip and a password hash. The key is built first, which spends nothing."""
    body = actionable(REAL["login"])
    gate = first_write_index(REAL["login"], "_login_gate")
    read = first_write_index(REAL["login"], "fetch_one")
    assert 0 <= gate, "the login route never consults the window"
    assert 0 <= read, "the login route never reads the account, so this check has no ordering to judge"
    assert gate < read, f"the window is consulted at statement {gate}, the account at {read}"
    assert "verify_password" not in call_names(body[gate]), \
        "the gate shares a statement with the credential check"
    planted = functions(
        "def login(body, request, response):\n"
        "    key = _login_key(body.email)\n"
        "    row = fetch_one('SELECT * FROM users')\n"
        "    _login_gate(key)\n"
        "    return row\n")["login"]
    assert not first_write_index(planted, "_login_gate") < first_write_index(planted, "fetch_one"), \
        "the control that reads the account first was read as gated -- so this check cannot see ordering"


def test_only_a_failed_credential_spends_the_window():
    """Ten wrong passwords a minute is the bound. A correct one is not, and neither is a refusal."""
    spent = [node for node in ast.walk(REAL["login"]) if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "_login_failed"]
    assert len(spent) == 1, f"expected exactly one place that spends the window, saw {len(spent)}"
    branch = spending_branch(REAL["login"], "_login_failed")
    assert branch is not None, "the window is spent outside any conditional branch"
    assert [n for n in call_names(branch) if n == "HTTPException"], \
        "the branch that spends the window does not refuse in the same breath"
    unconditional = functions(
        "def login(body, request, response):\n"
        "    _login_gate('k')\n"
        "    row = fetch_one('x')\n"
        "    _login_failed('k')\n"
        "    raise HTTPException(401, 'invalid credentials')\n")["login"]
    assert spending_branch(unconditional, "_login_failed") is None, \
        "the control that spends the window unconditionally was read as guarded"


def test_the_gate_itself_writes_nothing():
    """Reading the counter must not extend it -- that refresh was the whole defect."""
    writes = sorted({n for n in call_names(REAL["_login_gate"]) if n in WRITERS})
    assert writes == [], f"the gate touches the window: {writes}"
    planted = functions(
        "def _login_gate(key):\n"
        "    n = int(_mfa_incr(keys=[key], args=[60]))\n"
        "    rq.expire(key, 60)\n"
        "    if n >= 10:\n"
        "        raise HTTPException(429, 'too many login attempts')\n")["_login_gate"]
    seen = {n for n in call_names(planted) if n in WRITERS}
    assert {"_mfa_incr", "expire"} <= seen, f"the control that lets the gate write was not caught: {seen}"


def test_a_refusal_names_the_wait():
    """Retry-After is what turns 'try later' into something a client can act on, and it is the one
    number the store already knows exactly."""
    gate = REAL["_login_gate"]
    exceptions = [node for node in ast.walk(gate) if isinstance(node, ast.Call)
                  and getattr(node.func, "id", "") == "HTTPException"]
    assert exceptions, "the gate raises no HTTPException"
    refused = [node for node in exceptions if node.args and getattr(node.args[0], "value", None) == 429]
    assert refused, f"the gate raises no 429: {[ast.unparse(n)[:60] for n in exceptions]}"
    headers = [kw.value for node in refused for kw in node.keywords if kw.arg == "headers"]
    assert headers, "the 429 carries no headers argument"
    assert "Retry-After" in ast.unparse(headers[0]), \
        f"the headers argument does not set Retry-After: {ast.unparse(headers[0])[:120]}"


def test_the_counter_is_keyed_on_the_account_and_on_the_configured_limit():
    """Per account is the documented decision -- and the reason "users behind one egress IP throttle
    each other" never held here. The number comes from settings, so the compose/env contract owns it."""
    text = ast.unparse(REAL["_login_key"])
    assert "login:" in text and "sha256" in text, f"the key is not the account digest: {text[:120]}"
    assert "lower()" in text, "the key is not folded the way the address is matched"
    assert "settings.login_rate_limit_per_minute" in ast.unparse(REAL["_login_gate"]), \
        "the limit is a literal in the body, so nothing would notice a profile that changed it"


def test_the_credential_windows_all_run_on_one_fixed_window_script():
    """Login, the step-up wall and the code oracle used to be three different shapes, and the loosest one
    was the door everybody walks through. One script now decides what "a minute" means on this surface."""
    for name in ("_login_failed", "_mfa_throttle", "_step_up_failed"):
        assert "_mfa_incr" in ast.unparse(REAL[name]), \
            f"{name} no longer runs on the shared fixed-window script"
    script = next(node for node in TREE.body if isinstance(node, ast.Assign)
                  and any(getattr(t, "id", "") == "_mfa_incr" for t in node.targets))
    body = ast.unparse(script)
    assert "if n == 1" in body, f"the shared script is not a fixed window: {body[:200]}"
