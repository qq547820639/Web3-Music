"""Census: no proxy in this repo may dial a hostname it cannot re-resolve.

Why this exists (measured, acceptance-20260928T054641Z): step 8's restore recreates the application tier,
and a recreated container gets a *new* address from Docker's IPAM. The gateway's
`upstream api_upstream { server api:8000; }` resolved that name once, so from then on the gateway dialled
the api's previous address -- an address the pool had meanwhile handed to the worker, which does not
listen on 8000. Every route behind that gateway answered 502, and nothing in the stack said so: the
gateway's own healthcheck probes a local path, so it stayed `healthy` while it could not reach anything.
The chain found it at step 12, five minutes later, on the one request the mfa drill makes through the
gateway (`a login through the gateway records the address the gateway itself saw -- status 502`).

The repo already contained the correct pattern and used it in exactly one of its three proxies:
`services/web/nginx.conf` carries `resolver 127.0.0.11 valid=10s` with a variable upstream, and its
comment names this same symptom. The defect resurfaced because the gateway's config was written in the
older shape. So the rule here is a census over every proxy config in the tree, not a test of one file.

nginx's own terms (https://nginx.org/en/docs/http/ngx_http_upstream_module.html, read for this change):
`resolve` "monitors changes of the IP addresses that correspond to a domain name of the server, and
automatically modifies the upstream configuration without the need of restarting nginx"; for it to work
"a resolver directive must be specified", and "the server group must reside in the shared memory" -- the
`zone` directive. Open-source nginx gained `resolve` at 1.27.3; `nginx -v` in the gateway image reports
1.27.5.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIGS = sorted((ROOT / "services").glob("*/nginx.conf"))

IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
UPSTREAM = re.compile(r"upstream\s+(\w+)\s*\{(.*?)\}", re.S)
SERVER = re.compile(r"server\s+([^\s;]+)([^;]*);")
PROXY_TARGET = re.compile(r"proxy_pass\s+https?://([^/\s;]+)")
DIRECTIVES = re.compile(r"^\s*resolver\s", re.M)


def active_text(text: str) -> str:
    """Directive lines only.

    Without this, a comment that merely *names* the bad shape -- the sentence in services/web/nginx.conf
    that explains why the old form was wrong -- would keep the census red forever, and would also let a
    config that commented out its own violation read as clean.
    """
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def proxy_problems(name: str, text: str) -> list[str]:
    """Every place this nginx config dials a service by name through a mapping it cannot refresh."""
    body = active_text(text)
    problems: list[str] = []
    has_resolver = bool(DIRECTIVES.search(body))
    names = {block[0] for block in UPSTREAM.findall(body)}
    needs_resolver = False

    for block, inner in UPSTREAM.findall(body):
        servers = SERVER.findall(inner)
        if not servers:
            problems.append(f"{name}: upstream {block} declares no server, so the census reads nothing")
        uses_resolve = False
        for target, params in servers:
            host = target.split(":")[0]
            if IP.match(host):
                continue
            if "resolve" not in params:
                problems.append(
                    f"{name}: upstream {block} dials {host!r} once at config load "
                    f"(`server {target};` has no resolve), so any recreate of {host} leaves this proxy "
                    f"hitting a dead address")
                continue
            uses_resolve = True
        if uses_resolve and "zone" not in inner:
            problems.append(f"{name}: upstream {block} uses resolve but has no `zone`, and nginx only "
                            f"tracks the name for a group that lives in shared memory")
        if uses_resolve:
            needs_resolver = True

    for target in PROXY_TARGET.findall(body):
        host = target.split(":")[0]
        if host.startswith("$"):
            needs_resolver = True
            continue
        if host in names or IP.match(host):
            continue
        problems.append(f"{name}: proxy_pass to {host!r} is neither a declared upstream nor a variable, "
                        f"so nginx resolves it once through the system resolver")

    if needs_resolver and not has_resolver:
        problems.append(f"{name}: dials names that must be re-resolved but declares no `resolver` "
                        f"directive, so nothing refreshes the mapping")
    return problems


def census() -> tuple[list[str], dict[str, int]]:
    problems: list[str] = []
    seen: dict[str, int] = {}
    for path in CONFIGS:
        text = path.read_text(encoding="utf-8")
        seen[str(path.relative_to(ROOT))] = len(UPSTREAM.findall(active_text(text)))
        problems += proxy_problems(str(path.relative_to(ROOT)), text)
    return problems, seen


# ------------------------------------------------------------------ the census ----

def test_no_proxy_dials_a_hostname_it_cannot_re_resolve():
    problems, seen = census()
    assert problems == [], "a proxy in the tree can go stale when a container is recreated: " + \
                           " | ".join(problems)


def test_the_census_actually_reads_the_proxy_configs():
    """Denominator. An empty parse would satisfy the rule above as loudly as a real green."""
    assert len(CONFIGS) >= 3, f"expected a proxy config per edge service, found {[str(p) for p in CONFIGS]}"
    _, seen = census()
    gateway = "services/gateway/nginx.conf"
    assert gateway in seen, f"{gateway} was not read; census saw {sorted(seen)}"
    assert seen[gateway] == 3, (
        f"{gateway} should carry three upstream groups (api, web, admin); the census read "
        f"{seen[gateway]} -- if a block was merged or renamed, the rule above is checking less than it did"
    )


# ------------------------------------------------------------------ must-fire ----

def test_a_server_without_resolve_is_reported_and_named():
    text = (ROOT / "services/gateway/nginx.conf").read_text(encoding="utf-8")
    assert "server api:8000 resolve;" in text, "the gateway's api upstream no longer looks like the shape " \
        "this control mutates -- update the control, do not delete it"
    regressed = text.replace("server api:8000 resolve;", "server api:8000;", 1)
    found = proxy_problems("gateway", regressed)
    assert len(found) == 1, f"removing `resolve` from the api upstream must report exactly one problem, " \
                            f"got {found}"
    assert "api" in found[0] and "resolve" in found[0], found[0]
    assert proxy_problems("gateway", text) == [], "the real gateway config must be clean before the " \
        "regression control means anything"


def test_a_resolve_without_a_zone_is_reported():
    text = (ROOT / "services/gateway/nginx.conf").read_text(encoding="utf-8")
    assert "zone api_upstream 64k;" in text, "the api upstream no longer declares a shared-memory zone"
    no_zone = text.replace("zone api_upstream 64k;", "", 1)
    found = proxy_problems("gateway", no_zone)
    assert len(found) == 1 and "shared memory" in found[0], \
        f"nginx only tracks a name for a group in shared memory, and dropping the zone must say so: {found}"


def test_a_re_resolving_proxy_with_no_resolver_is_reported():
    """The web container's shape: a variable upstream. It is only dynamic because of its `resolver`."""
    text = (ROOT / "services/web/nginx.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in text and "$api_host" in text, \
        "services/web/nginx.conf no longer uses the variable-upstream shape this control checks"
    no_resolver = "\n".join(line for line in text.splitlines() if "resolver 127.0.0.11" not in line)
    found = proxy_problems("web", no_resolver)
    assert len(found) == 1 and "resolver" in found[0], \
        f"deleting the resolver must leave the config red, not silently green: {found}"
    assert proxy_problems("web", text) == [], f"the real web config should be clean: {found}"


def test_a_proxy_pass_to_an_undeclared_host_is_reported():
    planted = ("server {\n  listen 80;\n  resolver 127.0.0.11 valid=10s;\n"
               "  location /x/ { proxy_pass http://typo-ed-name:9000/; }\n}\n")
    found = proxy_problems("planted", planted)
    assert len(found) == 1 and "typo-ed-name" in found[0], \
        f"a proxy_pass that names no upstream must be reported, got {found}"


def test_comments_do_not_count_as_directives():
    """The violation this file exists to catch is also written down as prose; prose must not read as config."""
    commented = ("# upstream api_upstream { server api:8000; } -- this is the shape that broke, do not use\n"
                 "server {\n  listen 80;\n  resolver 127.0.0.11 valid=10s;\n"
                 "  upstream api_upstream { zone api_upstream 64k; server api:8000 resolve; }\n"
                 "  location /api/ { proxy_pass http://api_upstream; }\n}\n")
    assert proxy_problems("commented", commented) == [], \
        "a commented-out old shape must not make the census red"
    real = ("server {\n  listen 80;\n"
            "  upstream api_upstream { server api:8000; }\n"
            "  location /api/ { proxy_pass http://api_upstream; }\n}\n")
    assert len(proxy_problems("real", real)) == 1, \
        "the same line, uncommented, must be red -- otherwise the comment exemption is just a blind spot"
