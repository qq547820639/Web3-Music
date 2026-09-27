"""Real-source mutation battery for tests/unit/test_login_source_gate.py.

The guard ships in-memory controls, which prove the *predicate* shape. This proves the *installed test*
has teeth: each arm patches the file on disk, runs the file's tests against the broken tree, requires
exactly the named detectors to go red, then restores by pre-image and verifies the sha1 is back.

Two arms are must-not-fire fixtures (N1, N2): a forbidden token inside a config comment, and one inside
the gate's own prose. If either reddens anything, the detector is over-matching.

WARNING: this rewrites tracked files in the working tree for a few seconds at a time. Run it alone --
not alongside the chain, a drill, or another reader of these five files -- and only against a tree with
no uncommitted edits you care about inside them (it restores by pre-image, but a crash mid-arm leaves
the mutation in place).

Note on CTRL (the guard's own controls test) appearing in many expected sets: `mutated()` asserts its
anchor occurs exactly once, so an arm that *is* that anchor makes CTRL abort by construction. That is
the honest reading, not a second defect -- hence it is declared rather than tolerated.
"""
import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests/unit/test_login_source_gate.py"
MAIN = ROOT / "services/api/app/main.py"
AUTH = ROOT / "services/api/app/auth.py"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "services/api/Dockerfile"
NGINX = ROOT / "services/gateway/nginx.conf"
TARGETS = [MAIN, AUTH, COMPOSE, DOCKERFILE, NGINX]

ORDER = "test_the_source_gate_is_consulted_before_anything_is_read"
WRITES = "test_the_source_gate_writes_nothing_it_could_be_blamed_for"
BRANCH = "test_only_the_failed_credential_branch_costs_the_source_a_slot"
LIMIT = "test_the_limit_is_the_configured_one_rather_than_a_number_in_a_body"
REFUSAL = "test_the_refusal_says_how_long_and_where_that_number_came_from"
FIXED = "test_the_source_window_is_fixed_and_counts_accounts_not_attempts"
POLARITY = "test_the_source_window_arms_its_expiry_once_on_each_polarity"
DIGEST = "test_the_stored_address_is_keyed_and_the_limiter_key_shares_that_digest"
UNEQUAL = "test_a_keyed_digest_is_not_reachable_by_the_unkeyed_one"
TRUST = "test_the_trust_list_is_one_address_and_cannot_be_widened_by_a_stray_env"
SERVER = "test_the_server_is_told_to_use_it_and_refuses_to_guess"
PROXY = "test_the_proxy_writes_the_client_chain_on_every_line_that_sets_it"
CTRL = "test_the_gate_detectors_fire_on_the_shapes_that_would_silently_return"

FETCH = '    row=fetch_one("SELECT * FROM users WHERE lower(email)=lower(%s)",(body.email,))'
GATE_CALL = "    _source_gate(address)"
XFF = "    proxy_set_header X-Forwarded-For $remote_addr;"
# Both proxy locations carry the identical comment block and directive, so an arm that means only one
# of them has to anchor on what follows it -- `proxy_connect_timeout` exists in the /api/ block alone.
API_SETTER_BLOCK = (XFF + "\n    proxy_set_header X-Request-Id $request_id;\n"
                    "    proxy_connect_timeout 5s;")

# (name, path, old, new, times, expected-red, why-those)
ARMS = [
    ("A1 the gate moved after the credential read", MAIN,
     f"{GATE_CALL}\n{FETCH}", f"{FETCH}\n{GATE_CALL}", 1, {ORDER, CTRL},
     "order clause flips; CTRL's two-line anchor is the pair being swapped"),
    ("A2 the gate call deleted outright", MAIN,
     f"{GATE_CALL}\n", "", 1, {ORDER, CTRL},
     "the new dead-code assertion, not a ValueError; CTRL loses its anchor"),
    ("A3 gate starts writing the window", MAIN,
     "    key = _source_key(address)\n    if int(rq.scard(key) or 0)",
     "    key = _source_key(address)\n    rq.delete(key)\n    if int(rq.scard(key) or 0)", 1,
     {WRITES, CTRL}, "rq.delete is both a WRITERS hit and unlisted; CTRL #2 uses the same anchor"),
    ("A4 an unlisted writer nobody named", MAIN,
     "    key = _source_key(address)\n    if int(rq.scard(key) or 0)",
     "    key = _source_key(address)\n    rq.rename(key, key + ':moved')\n"
     "    if int(rq.scard(key) or 0)", 1,
     {WRITES}, "only the whitelist clause can see this -- the blacklist has no 'rename'"),
    ("A5 limit pasted into the gate", MAIN,
     ">= settings.login_source_rate_limit_per_minute", ">= 20", 1, {LIMIT, CTRL},
     "literal detector fires; CTRL #7 replaces the same settings reference"),
    ("A6 refusal stops stating the wait", MAIN,
     '        ttl = rq.ttl(key)\n        raise HTTPException(429, "too many accounts tried from this address",\n'
     '                            headers={"Retry-After": str(ttl) if ttl and ttl > 0 else str(LOGIN_WINDOW_SECONDS)})',
     '        raise HTTPException(429, "too many accounts tried from this address")', 1,
     {REFUSAL, WRITES}, "Retry-After/rq.ttl gone; the reads-only rule also requires rq.ttl present"),
    ("A7 window turned sliding", MAIN,
     "\"if redis.call('TTL', KEYS[1]) == -1 then redis.call('EXPIRE'",
     "\"redis.call('EXPIRE'", 1, {FIXED, POLARITY, CTRL},
     "both window rules read the script text; CTRL #6 anchors on the TTL clause"),
    ("A8 every attempt spends the source slot", MAIN,
     f"{GATE_CALL}\n{FETCH}", f"{GATE_CALL}\n    _source_failed(address, body.email)\n{FETCH}", 1,
     {BRANCH, CTRL}, "two spend sites instead of one inside the credential branch"),
    ("A9 limiter re-implements the digest inline", MAIN,
     '    return f"login:src:{address_hash(address)}"',
     '    return "login:src:" + hashlib.sha256(address.encode()).hexdigest()', 1, {DIGEST},
     "the audit column and the limiter key would silently stop agreeing"),
    ("A10 session back to an unsalted digest", AUTH,
     '            address_hash(client_ip or ""),', '            token_hash(client_ip or ""),', 1,
     {DIGEST, CTRL}, "ip_hash stops being keyed; CTRL #3 anchors on that call"),
    ("A11 pepper dropped from the digest", AUTH,
     'return hashlib.sha256(f"{settings.address_pepper}|{client_ip}".encode()).hexdigest()',
     "return hashlib.sha256(client_ip.encode()).hexdigest()", 1, {DIGEST},
     "2**32 IPv4 values, brute-forced in minutes; no CTRL anchor here"),
    ("A12 trust list widened to everybody", COMPOSE,
     '      FORWARDED_ALLOW_IPS: "172.28.95.250"', '      FORWARDED_ALLOW_IPS: "*"', 1, {TRUST, CTRL},
     "the api would believe any caller"),
    ("A13 trust list interpolated from a host env", COMPOSE,
     '      FORWARDED_ALLOW_IPS: "172.28.95.250"',
     '      FORWARDED_ALLOW_IPS: "${FORWARDED_ALLOW_IPS:-172.28.95.250}"', 1, {TRUST, CTRL},
     "${-interpolation is the widening that reads as a pinned value"),
    ("A13b the gateway pin drops back into the allocator's reach", COMPOSE,
     "172.28.95.250", "172.28.95.10", 2, {TRUST, CTRL},
     "the shape that reddened chain step 2 at acceptance-20260927T221550Z: `web` had already been "
     "handed .10 by the pool, so the gateway could not start"),
    ("A14 uvicorn flag defaults instead of refusing", DOCKERFILE,
     '${FORWARDED_ALLOW_IPS:?FORWARDED_ALLOW_IPS must name the proxy peer; see .env.example}',
     '${FORWARDED_ALLOW_IPS:-127.0.0.1}', 1, {SERVER},
     "'trust nobody' would boot as a success"),
    ("A15 proxy appends again, in both locations", NGINX,
     XFF, "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", 2, {PROXY, CTRL},
     "the caller's own value returns to the head of the chain"),
    ("A16 one location loses its setter", NGINX,
     API_SETTER_BLOCK,
     API_SETTER_BLOCK.replace(XFF + "\n", "", 1), 1, {PROXY, CTRL},
     "the /api/ door stops writing the peer it saw; two locations is the rule"),
    ("N1 forbidden token in a comment", NGINX,
     API_SETTER_BLOCK,
     "    # NOT $proxy_add_x_forwarded_for; we overwrite instead\n" + API_SETTER_BLOCK, 1, set(),
     "must stay green: the active-line filter ignores comments (a real defect this guard shipped with)"),
    ("N2 forbidden token restated in prose", MAIN,
     "    key = _source_key(address)\n    if int(rq.scard(key) or 0)",
     '    key = _source_key(address)\n    """Reads only: no SADD, no DEL, no EXPIRE happens here."""\n'
     "    if int(rq.scard(key) or 0)", 1, set(),
     "must stay green: the writer rule judges AST calls, so the gate may describe its own policy"),
]


def sha(p):
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def apply(path, old, new, times):
    """Return the pre-image so the caller can restore by content, not by reverse-replace (a deletion
    arm's `new` is the empty string, and replace('', old, 1) would prepend instead of undo)."""
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == times, f"{path.name}: anchor occurs {n} times, expected {times}: {old[:60]!r}"
    out = src.replace(old, new)
    assert out != src, f"{path.name}: mutation changed nothing"
    path.write_text(out, encoding="utf-8")
    return src


def run():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf", str(TESTS)],
                       cwd=ROOT, capture_output=True, text=True)
    # ERROR is its own red: an arm that makes a test crash (KeyError/ValueError on a lookup) would
    # otherwise read as "no detector fired", which is exactly the false green to avoid.
    red = sorted({m.group(1) for m in re.finditer(r"^(?:FAILED|ERROR) \S+::(\w+)", r.stdout, re.M)})
    passed = re.search(r"(\d+) passed", r.stdout)
    return red, (int(passed.group(1)) if passed else 0), r.stdout


if __name__ == "__main__":
    base = {p: sha(p) for p in TARGETS}
    red, npass, out = run()
    print(f"baseline: {npass} passed, {len(red)} red\n  " +
          "  ".join(f"{p.name}={base[p]}" for p in TARGETS))
    assert npass == 13 and not red, f"baseline is not clean -- stop, the battery measures nothing: {red}"

    bad = []
    for name, path, old, new, times, expect, why in ARMS:
        pre = apply(path, old, new, times)
        assert sha(path) != base[path], f"{name}: sha unchanged, the arm never landed"
        red, npass, out = run()
        path.write_text(pre, encoding="utf-8")
        assert sha(path) == base[path], f"{name}: restore did not return to baseline sha"
        got = set(red)
        ok = got == expect
        if not ok:
            bad.append(name)
        print(f"{'OK ' if ok else 'MISMATCH':9s} {name}\n          red: "
              f"{', '.join(red) if red else '(none)'}   [{why}]")

    print("\n--- summary " + "-" * 58)
    for name, path, old, new, times, expect, why in ARMS:
        if name in bad:
            print(f"MISMATCH {name}: expected {sorted(expect)}")
    final = {p: sha(p) for p in TARGETS}
    assert final == base, f"residue after the battery: {[p.name for p in TARGETS if final[p] != base[p]]}"
    red, npass, out = run()
    print(f"restored tree: {npass} passed, {len(red)} red; all five sha1 back to baseline")
    print(f"arms: {len(ARMS)} ({sum(1 for a in ARMS if not a[5])} must-not-fire), "
          f"all as declared: {not bad}" + (f"  -> {bad}" if bad else ""))
    sys.exit(1 if bad else 0)
