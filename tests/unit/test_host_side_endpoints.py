"""Host-side entry points must address the stack by literal address, not by hostname.

On 2026-09-26 another project on this machine started `vite preview` on port 4173 while a release run
was in flight. `localhost` resolves to ::1 first, so the browser gate drove that other app and went
red on a missing #loginForm -- and the same collision could equally have produced a green against
somebody else's site. The API-side drills read API_BASE_URL, so they were one step away from the same
aliasing. `scripts/` is the surface that runs on the host; in-container URLs use service names and are
out of scope, and docker-compose's CORS_ORIGINS legitimately lists browser origins rather than
destinations.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEEDLE = "http://localhost:"


def scripts_using_hostname() -> list[str]:
    files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "scripts").glob("*.sh"))
    return [str(path.relative_to(ROOT)) for path in files if NEEDLE in path.read_text(encoding="utf-8")]


def first_literal_address_endpoint() -> str:
    files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "scripts").glob("*.sh"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "http://127.0.0.1:" in text:
            return str(path.relative_to(ROOT))
    return ""


def test_no_host_side_script_addresses_the_stack_by_hostname():
    found = scripts_using_hostname()
    assert not found, f"these host-side scripts still resolve a port by hostname: {found}"


def test_the_hostname_sweep_has_a_denominator():
    # Without this, an empty glob or a renamed directory would make the check above pass vacuously.
    assert first_literal_address_endpoint(), "no script under scripts/ contains an http://127.0.0.1: endpoint"


def test_the_predicate_fires_on_the_shape_that_broke_the_run():
    planted = 'BASE = os.getenv("API_BASE_URL", "http://localhost:8000") + "/api"'
    assert NEEDLE in planted, "the planted sample no longer carries the needle, so the check cannot fire"
    assert NEEDLE not in planted.replace("http://localhost:", "http://127.0.0.1:")
