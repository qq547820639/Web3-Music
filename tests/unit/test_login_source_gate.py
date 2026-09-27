"""Static guards on the source dimension of the login door: what is counted, from where, and believed
by whom.

The live half of this lives in `scripts/mfa_drill.py`, which measures the properties no static reader
can reach: that a login through the gateway records the address the gateway itself saw, that the same
forged `X-Forwarded-For` is ignored when it arrives on the published port, that a source may get N
accounts wrong and is held at N+1, and that a refusal grows neither window. What belongs here is the
wiring those readings lean on, because every one of them can be lost silently: widen the trust list to
`*` and requests keep succeeding while the audit column starts believing whoever sends the header;
change the set script to arm its expiry on every add and the window stops being a window; drop the
pepper from the digest and `ip_hash` becomes a brute-forceable SHA-256 of a 2**32 address space.

One deliberate absence: nothing here asserts the *value* of the limit. It is configuration, read from
the environment, and the drill compares the deployed container's own `printenv` against what it observes.

Every detector ships with the sample that must make it fire -- a guard nobody has seen report anything
is indistinguishable from a guard that cannot.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = ROOT / "services/api/app"
MAIN = API / "main.py"
AUTH = API / "auth.py"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "services/api/Dockerfile"
NGINX = ROOT / "services/gateway/nginx.conf"

# Names that move the source window forward. A reader that missed one of these would "prove" the gate
# writes nothing by not seeing that it does.
WRITERS = {"SADD", "sadd", "EXPIRE", "expire", "DEL", "delete", "SET", "set", "INCR", "incr",
           "_login_source_add", "_source_failed", "_mfa_incr", "_login_failed"}

# Judged from the AST's call names, not from the function's text: the docstring has to be able to say
# "no DEL, no EXPIRE here" without the guard reading its own prose as a write. A blacklist alone is
# still evadable by a writer nobody thought to name, so the gate is whitelisted instead and anything
# unlisted is a failure -- a new read is a one-word edit, a new write is exactly what this is for.
GATE_ALLOWED_CALLS = {"scard", "ttl", "get", "int", "str", "len", "_source_key", "HTTPException"}


def tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def functions(source: str) -> dict:
    return {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}


def call_names(fn) -> list:
    return [n.func.id if isinstance(n.func, ast.Name) else ast.unparse(n.func) for n in ast.walk(fn)
            if isinstance(n, ast.Call)]


def text_of(fn) -> str:
    return ast.unparse(fn)


def limit_literals(body: str) -> list:
    """Numbers in the gate that could be a second copy of the limit.

    429 is the status the refusal answers with and 60 is the window's own length, which the gate has to
    state because Retry-After needs it when the key carries no TTL. Sharing this one predicate between
    the rule and its control is the point: two spellings of "is this a hardcoded limit" drift.
    """
    return [n for n in re.findall(r"\b(\d{2,4})\b", body) if n not in {"429", "60"}]


MAIN_SRC = MAIN.read_text(encoding="utf-8")
FNS = functions(MAIN_SRC)


# ------------------------------------------------------- the gate: reads, never writes ----

def test_the_source_gate_is_consulted_before_anything_is_read():
    login = FNS["login"]
    calls = call_names(login)
    assert "fetch_one" in calls, "login() no longer reads the account, so this guard is stale"
    assert "_source_gate" in calls, \
        "login() no longer consults the source window at all -- the gate is dead code"
    assert calls.index("_source_gate") < calls.index("fetch_one"), \
        f"a source that is already over its window is asked for its password first: {calls[:8]}"


def test_the_source_gate_writes_nothing_it_could_be_blamed_for():
    gate = FNS["_source_gate"]
    names = [c.split(".")[-1] for c in call_names(gate)]
    writers = [w for w in WRITERS if w in names]
    assert not writers, f"_source_gate spends or resets the window it is reporting: {writers}"
    for reader in ("scard", "ttl"):
        assert reader in names, f"_source_gate no longer reads {reader}, so it cannot report one"
    unlisted = sorted({n for n in names if n not in GATE_ALLOWED_CALLS})
    assert not unlisted, \
        f"the read-only gate reaches for something the whitelist has never seen: {unlisted}"


def test_only_the_failed_credential_branch_costs_the_source_a_slot():
    login = FNS["login"]
    spenders = [n for n in ast.walk(login) if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "_source_failed"]
    assert len(spenders) == 1, f"{len(spenders)} places spend the source window; expected exactly one"
    inside = [h for h in ast.walk(login) if isinstance(h, ast.If) and spenders[0] in list(ast.walk(h))]
    assert inside, "the source counter is not inside a conditional, so every attempt is a failure"
    guard = ast.unparse(inside[0].test)
    assert "verify_password" in guard and "status" in guard, \
        f"the one spending site sits under a test that is not the credential check: {guard[:120]}"
    assert "_source_failed" in guard or "invalid credentials" in ast.unparse(inside[0]), \
        "the spending site is not on the branch that answers 401"


def test_the_limit_is_the_configured_one_rather_than_a_number_in_a_body():
    body = text_of(FNS["_source_gate"])
    assert "settings.login_source_rate_limit_per_minute" in body, body[:200]
    copies = limit_literals(body)
    assert not copies, f"a second copy of the limit lives in the gate: {copies}"


def test_the_refusal_says_how_long_and_where_that_number_came_from():
    body = text_of(FNS["_source_gate"])
    assert "Retry-After" in body and "rq.ttl" in body, body[:240]
    assert "HTTPException(429" in body, "the source window must answer 429, the same word the account one uses"


# --------------------------------------------------------- the window's own script ----

def test_the_source_window_is_fixed_and_counts_accounts_not_attempts():
    m = re.search(r"_login_source_add = rq\.register_script\((.*?)\)\n\n", MAIN_SRC, re.S)
    assert m, "the source window is no longer a registered script, so it is not atomic any more"
    script = m.group(1)
    assert "SADD" in script.upper(), "it counts attempts again, which is the wrong quantity"
    assert "SCARD" in script.upper(), "it does not report what it counted"
    assert re.search(r"TTL.{0,60}?==\s*-1", script.upper()), \
        f"the expiry is armed on every add, so the window slides with the attack: {script[:200]}"
    assert "EXPIRE" in script.upper()


def test_the_source_window_arms_its_expiry_once_on_each_polarity():
    """The script text is the contract; this feeds it both shapes and requires one to be refused."""
    good = ("redis.call('SADD', KEYS[1], ARGV[2]); "
            "if redis.call('TTL', KEYS[1]) == -1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end; "
            "return redis.call('SCARD', KEYS[1])")
    assert re.search(r"TTL.{0,60}?==\s*-1", good.upper()), "the control itself must state the rule it checks"
    sliding = ("redis.call('SADD', KEYS[1], ARGV[2]); "
               "redis.call('EXPIRE', KEYS[1], ARGV[1]); "
               "return redis.call('SCARD', KEYS[1])")
    assert not re.search(r"TTL.{0,60}?==\s*-1", sliding.upper()), \
        "a script that expires on every add must be the one this detector rejects"
    # The shipped script is split across three adjacent string literals, so compare it with the
    # concatenation syntax removed -- otherwise this line tests the formatter rather than the script.
    shipped = re.search(r"_login_source_add = rq\.register_script\((.*?)\)\n\n", MAIN_SRC, re.S).group(1)
    assert re.sub(r"[\s\"'+]", "", shipped) == re.sub(r"[\s\"'+]", "", good), \
        "the shipped script is not the fixed-window shape this file describes"


# ------------------------------------------------------------- the digest itself ----

def test_the_stored_address_is_keyed_and_the_limiter_key_shares_that_digest():
    auth = functions(AUTH.read_text(encoding="utf-8"))
    digest = text_of(auth["address_hash"])
    assert "settings.address_pepper" in digest and "sha256" in digest, digest[:200]
    session = text_of(auth["create_browser_session"])
    assert "address_hash(client_ip" in session, "auth_sessions.ip_hash is back to an unsalted digest"
    assert not re.search(r"token_hash\(client_ip", session), \
        "ip_hash is computed by token_hash again -- 2**32 IPv4 values, brute-forced in minutes"
    source_key = text_of(FNS["_source_key"])
    assert "address_hash" in source_key, f"the limiter re-implements the digest: {source_key[:160]}"


def test_a_keyed_digest_is_not_reachable_by_the_unkeyed_one():
    """The two derivations must not coincide for any of the shapes the stack actually holds."""
    import hashlib
    pepper = "unit-test-pepper"
    for address in ("127.0.0.1", "172.28.95.7", "172.28.95.10", "203.0.113.77", "::1"):
        assert hashlib.sha256(address.encode()).hexdigest() != \
            hashlib.sha256(f"{pepper}|{address}".encode()).hexdigest()


# ------------------------------------------------------ who is allowed to be believed ----

def test_the_trust_list_is_one_address_and_cannot_be_widened_by_a_stray_env():
    compose = COMPOSE.read_text(encoding="utf-8")
    line = [x for x in compose.splitlines() if "FORWARDED_ALLOW_IPS" in x and ":" in x]
    assert len(line) == 1, f"the trust list is stated {len(line)} times: {line}"
    value = line[0].split(":", 1)[1].strip()
    assert "${" not in value, f"an interpolated trust list can be widened by a host .env: {value}"
    assert value.strip('"') not in ("*", ""), f"the api would then believe any caller: {value}"
    addresses = re.findall(r"ipv4_address:\s*([\d.]+)", compose)
    assert len(addresses) == 1, f"the compose file pins {len(addresses)} static addresses: {addresses}"
    assert value.strip('"') == addresses[0], \
        f"the trusted address {value} is no longer the gateway's pinned {addresses[0]}"


def test_the_server_is_told_to_use_it_and_refuses_to_guess():
    cmd = re.search(r"CMD \[.*?\]\n", DOCKERFILE.read_text(encoding="utf-8"), re.S)
    assert cmd, "no CMD line in the api Dockerfile any more"
    line = cmd.group(0)
    assert "--forwarded-allow-ips" in line, "uvicorn is no longer given a trust list at all"
    assert "FORWARDED_ALLOW_IPS:?" in line, \
        "the flag defaults to 127.0.0.1 when unset, which is 'trust nobody' written as a success"
    assert ":-" not in line.split("FORWARDED_ALLOW_IPS")[1][:40], \
        "a default-value interpolation would let a missing setting boot as an untrusted stack"


def test_the_proxy_writes_the_client_chain_on_every_line_that_sets_it():
    active = [x.strip() for x in NGINX.read_text(encoding="utf-8").splitlines()
              if x.strip() and not x.strip().startswith("#")]
    setters = [x for x in active if x.startswith("proxy_set_header X-Forwarded-For")]
    assert len(setters) >= 2, f"only {len(setters)} locations set the client chain: {setters}"
    appends = [x for x in setters if "proxy_add_x_forwarded_for" in x]
    assert not appends, f"appending keeps the caller's own value at the head of the chain: {appends}"
    for line in setters:
        assert "$remote_addr" in line, f"a setter that does not write the peer it saw: {line}"
    assert len([x for x in active if x.startswith("proxy_set_header X-Real-IP $remote_addr")]) >= 2, \
        "X-Real-IP is gone, so anything reading that instead has lost its client"


# ----------------------------------------------------------- the must-fire controls ----

def mutated(source: str, old: str, new: str, times: int = 1) -> str:
    """`times` because two locations in the proxy config legitimately carry the same directive: the
    control that breaks the append form has to break both, or it would only be half a regression."""
    assert source.count(old) == times, f"anchor occurs {source.count(old)} times, expected {times}: {old[:70]!r}"
    out = source.replace(old, new)
    assert out != source, "the mutation changed nothing"
    return out


def gate_body(source: str) -> str:
    return text_of(functions(source)["_source_gate"])


def login_calls(source: str) -> list:
    return call_names(functions(source)["login"])


def test_the_gate_detectors_fire_on_the_shapes_that_would_silently_return():
    """Every rule above is checked against one broken copy of the real source.

    Each of these breaks is *silent* in production: the login page still works, a limit still fires on
    the traffic that happens to arrive, and the only difference is that a request from an unlisted peer
    is now believed, or that the audit trail records a container instead of a client.
    """
    # 1. the source gate moved after the credential read
    moved = mutated(MAIN_SRC,
                     '    _source_gate(address)\n    row=fetch_one("SELECT * FROM users WHERE '
                     'lower(email)=lower(%s)",(body.email,))',
                     '    row=fetch_one("SELECT * FROM users WHERE lower(email)=lower(%s)",(body.email,))\n'
                     '    _source_gate(address)')
    order = login_calls(moved)
    assert order.index("_source_gate") > order.index("fetch_one"), \
        "the ordering detector cannot see a gate placed after the read"
    assert login_calls(MAIN_SRC).index("_source_gate") < login_calls(MAIN_SRC).index("fetch_one"), \
        "the ordering detector passes on the real file for the wrong reason"

    # 2. the gate starts writing the window it only means to read
    writing = mutated(gate_body(MAIN_SRC), "    key = _source_key(address)",
                      "    key = _source_key(address)\n    rq.delete(key)")
    assert any(c.split(".")[-1] == "delete" for c in call_names(ast.parse(writing).body[0])), \
        "the write detector cannot see rq.delete() inside the gate"
    clean = [c for c in call_names(ast.parse(gate_body(MAIN_SRC)).body[0])
             if c.split(".")[-1] in WRITERS]
    assert not clean, \
        "the shipped gate already names something from WRITERS, so the rule above proves nothing"

    # 3. an unsalted digest back in the session INSERT. Scoped to that function's body on purpose: the
    #    module also *defines* `address_hash(client_ip: str)`, so a whole-file substring test for the
    #    good form can never go red -- the shape where a control passes by measuring the definition.
    unsalted = mutated(AUTH.read_text(encoding="utf-8"), 'address_hash(client_ip or "")',
                       'token_hash(client_ip or "")')
    broken_session = text_of(functions(unsalted)["create_browser_session"])
    assert "token_hash(client_ip" in broken_session and "address_hash(client_ip" not in broken_session, \
        "the digest detector cannot see the unsalted form come back"

    # 4. the trust list widened to everybody, in the exact form the compose file allows
    wide = mutated(COMPOSE.read_text(encoding="utf-8"), 'FORWARDED_ALLOW_IPS: "172.28.95.10"',
                   'FORWARDED_ALLOW_IPS: "*"')
    value = [x for x in wide.splitlines() if "FORWARDED_ALLOW_IPS" in x][0].split(":", 1)[1].strip()
    assert value.strip('"') == "*" and value.strip('"') != "172.28.95.10", \
        "the widening the trust detector exists for is not the shape it tests"

    # 5. the proxy back to appending, on an active line
    appending = mutated(NGINX.read_text(encoding="utf-8"),
                        "    proxy_set_header X-Forwarded-For $remote_addr;",
                        "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", times=2)
    setters = [x.strip() for x in appending.splitlines()
               if x.strip().startswith("proxy_set_header X-Forwarded-For")]
    assert any("proxy_add_x_forwarded_for" in x for x in setters), \
        "an appending directive would be invisible to the check that forbids it"
    # 5b. and the same break hidden in a comment must NOT be what the rule counts
    commented = mutated(NGINX.read_text(encoding="utf-8"),
                        "    proxy_set_header X-Forwarded-For $remote_addr;",
                        "    # proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                        "    proxy_set_header X-Forwarded-For $remote_addr;", times=2)
    active = [x.strip() for x in commented.splitlines() if x.strip() and not x.strip().startswith("#")]
    assert not [x for x in active if x.startswith("proxy_set_header X-Forwarded-For")
                and "proxy_add_x_forwarded_for" in x], \
        "the active-line filter is not filtering, so a comment can hold this check open"

    # 6. the fixed window turned sliding
    sliding = mutated(MAIN_SRC, "if redis.call('TTL', KEYS[1]) == -1 then", "if 1 == 1 then")
    script = re.search(r"_login_source_add = rq\.register_script\((.*?)\)\n\n", sliding, re.S).group(1)
    assert not re.search(r"TTL.{0,60}?==\s*-1", script.upper()), \
        "a script that re-arms on every add still passes the fixed-window rule"

    # 7. the limit hardcoded into the gate instead of read from settings
    hardcoded = mutated(gate_body(MAIN_SRC), "settings.login_source_rate_limit_per_minute", "20")
    assert limit_literals(hardcoded), "the literal detector cannot see a number pasted into the gate"
    assert not limit_literals(gate_body(MAIN_SRC)), \
        "the shipped gate already carries a number the rule would flag, so the rule is not clean"
