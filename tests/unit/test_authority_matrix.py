"""Guards on the derived authority matrix: the reader, the committed copies, and the five writes
that carry no role check at the door.

docs/AUTHORITY_MATRIX.md and shared/contracts/authority-matrix.json are generated from the API
source by scripts/authority_matrix.py, so that "who approves what" (release gate G12's approval-flow
row) is a measurement rather than folklore. These tests keep three things true: the reader must see
what the app actually serves, the committed artefacts must not drift from it, and a write route that
relies on something other than a role check must name the carrier that does -- and still contain it.

Every detector here ships with the sample that must make it fire, including the two shapes the
reader originally missed: a router-mounted sub-path and an `async def` endpoint.
"""
import ast
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from authority_matrix import derive, derive_file, render_markdown  # noqa: E402

MATRIX_JSON = ROOT / "shared/contracts/authority-matrix.json"
MATRIX_DOC = ROOT / "docs/AUTHORITY_MATRIX.md"
ROLE_SOURCE = ROOT / "db/migrations/001_production_candidate.sql"

# Each entry is a write route that no Depends() restricts, and the code inside it that does the
# actual deciding. `--check` and the test below both fail if a route appears without a listing, so
# the only way to add an unauthenticated write is to say what protects it.
CARRIERS = {
    "POST /api/auth/login": ("verify_password",),
    "POST /api/auth/refresh": ("validate_refresh_session",),
    "POST /api/auth/mfa/challenge": ("_mfa_throttle",),
    "POST /api/provider-webhooks/{provider}": ("hmac.compare_digest",),
    "POST /api/payment-webhooks/{provider}": ("hmac.compare_digest",),
    # The public intake (021). Two writes anyone can attempt without a session, each named by what
    # actually decides: the arrival windows, and the receipt whose hash the database compares.
    "POST /api/reports": ("_report_gate",),
    "POST /api/reports/status": ("token_hash",),
}
EXPECTED_KINDS = {"require_roles", "platform_admin", "workspace_member", "session", "app-level-only"}


def endpoint_source(endpoint: str) -> str:
    module, _, name = endpoint.partition(":")
    path = ROOT / "services/api/app" / ({"app": "main.py"}.get(module, module.replace(".", "/") + ".py"))
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"{endpoint} is not a module-level endpoint in {path}")


def live_routes() -> set[tuple[str, str]]:
    sys.path.insert(0, str(ROOT / "services/api"))
    from app.main import app
    return {(method, route.path) for route in app.routes
            for method in (set(getattr(route, "methods", set())) - {"HEAD", "OPTIONS"})
            if getattr(route, "path", "").startswith("/api")}


def legal_roles() -> set[str]:
    line = [l for l in ROLE_SOURCE.read_text(encoding="utf-8").splitlines() if "CHECK(role IN" in l]
    assert len(line) == 1, "the role CHECK must be readable from 001"
    return set(re.findall(r"'([a-z_]+)'", line[0]))


def test_the_reader_sees_every_route_the_app_serves():
    derived = {(row["method"], row["path"]) for row in derive()}
    served = live_routes()
    assert derived, "the reader found no routes at all, so every comparison below would be vacuous"
    assert derived == served, (f"missing: {sorted(served - derived)[:6]} "
                               f"unexpected: {sorted(derived - served)[:6]}")


def test_the_committed_matrix_and_document_match_the_code():
    committed = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))
    assert committed["routes"] == derive(), "shared/contracts/authority-matrix.json is stale; run --write"
    assert MATRIX_DOC.read_text(encoding="utf-8").strip() == render_markdown(derive()).strip(), \
        "docs/AUTHORITY_MATRIX.md is stale; run --write"


def test_every_route_is_classified_and_every_writer_names_a_protector():
    rows = derive()
    unknown = sorted({row["authority"] for row in rows} - EXPECTED_KINDS)
    assert not unknown, f"the reader produced kinds it cannot explain: {unknown}"
    unguarded = sorted(f"{row['method']} {row['path']}" for row in rows
                       if row["writes"] and row["authority"] == "app-level-only")
    assert set(unguarded) == set(CARRIERS), (f"new writes with no role check: {set(unguarded) - set(CARRIERS)}; "
                                             f"stale allow-list entries: {set(CARRIERS) - set(unguarded)}")


def test_each_listed_carrier_is_still_in_the_endpoint_that_claims_it():
    for row in derive():
        key = f"{row['method']} {row['path']}"
        if not (row["writes"] and row["authority"] == "app-level-only"):
            continue
        body = endpoint_source(row["endpoint"])
        assert any(marker in body for marker in CARRIERS[key]), \
            f"{key} is protected by nothing but its own claim now: expected one of {CARRIERS[key]}"


def test_role_sets_are_drawn_from_the_schema_vocabulary():
    allowed = legal_roles()
    offenders = sorted({(role, f"{row['method']} {row['path']}")
                        for row in derive() if row["roles"]
                        for role in row["roles"].split(",") if role not in allowed})
    assert not offenders, f"require_roles() mentions names the CHECK constraint does not allow: {offenders}"
    assert allowed >= {"owner", "admin", "creator", "reviewer", "viewer", "billing", "legal", "support"}


@pytest.mark.parametrize("fixture, expected", [
    # A router mounted at /api declares only its sub-path: the old reader filtered on the decorator
    # string and dropped all 24 of those routes while still reporting a confident total.
    ('router = APIRouter(prefix="/api")\n@router.post("/things")\nasync def make():\n    return None\n',
     ("POST", "/api/things", True)),
    # An @app route with no session dependency of any kind, which is the shape the carrier list guards.
    ('@app.post("/api/open")\ndef open_way():\n    return None\n', ("POST", "/api/open", True)),
])
def test_the_reader_catches_the_shapes_it_once_missed(tmp_path, fixture, expected):
    path = tmp_path / "fixture.py"
    path.write_text(fixture, encoding="utf-8")
    rows = derive_file(path)
    assert len(rows) == 1, f"the fixture route was not read at all: {fixture[:48]!r}"
    row = rows[0]
    assert (row["method"], row["path"], row["writes"]) == expected
    assert row["roles"] == "" and row["authority"] == "app-level-only", \
        "neither fixture declares a session dependency, so both must land in the bucket the allow-list guards"
