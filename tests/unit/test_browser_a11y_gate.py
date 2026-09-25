"""Must-fire / must-not-fire tests for the browser a11y gate (release gate G11).

scripts/browser_a11y.py audits a live stack, which only proves the verdict it
printed. These tests prove the verdict functions can disagree with their input,
so "browser a11y + walkthrough passed" means something.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from browser_a11y import csp_failures, gate_failures, mobile_fit_failures  # noqa: E402

CSP_OK = "default-src 'self'; script-src 'self'; base-uri 'none'"
ORIGINS = ["http://localhost:4173", "http://localhost:4174"]


def scan(**overrides):
    base = {"label": "view", "viewport": "desktop", "axe": True, "audited": True, "settle": "stable",
            "violations": [], "scroll_width": 1440, "client_width": 1440}
    base.update(overrides)
    return base


def violation(impact):
    return {"id": f"rule-{impact}", "impact": impact, "node_count": 1, "targets": ["#x"], "why": "measured"}


def test_clean_audit_raises_nothing():
    assert gate_failures([scan(), scan(label="other", viewport="mobile")]) == []


def test_critical_and_serious_are_blocking():
    assert gate_failures([scan(violations=[violation("critical")])])
    assert gate_failures([scan(violations=[violation("serious")])])


def test_moderate_is_tolerated_per_the_documented_criterion():
    assert gate_failures([scan(violations=[violation("moderate")])]) == []


def test_scan_that_returned_nothing_is_not_a_pass():
    assert gate_failures([scan(audited=False)])


def test_scan_during_paint_is_not_a_pass():
    assert gate_failures([scan(settle="timeout")])


def test_as_deployed_entries_are_not_judged_by_axe():
    assert gate_failures([scan(axe=False, audited=False, settle="timeout")]) == []


def test_mobile_overflow_only_measured_on_mobile():
    wide_desktop = scan(viewport="desktop", scroll_width=9999, client_width=1440)
    fitted_phone = scan(viewport="mobile", scroll_width=390, client_width=390)
    assert mobile_fit_failures([wide_desktop, fitted_phone]) == []
    assert mobile_fit_failures([scan(viewport="mobile", scroll_width=1200, client_width=390)])
    assert mobile_fit_failures([scan(viewport="mobile", scroll_width=391, client_width=390)]) == []


def test_mobile_check_refuses_to_pass_on_an_unmeasured_denominator():
    assert mobile_fit_failures([scan(viewport="desktop")])


def test_mobile_check_is_only_waived_when_the_run_says_so():
    desktop_only = [scan(viewport="desktop")]
    assert mobile_fit_failures(desktop_only, require=False) == []
    assert mobile_fit_failures(desktop_only, require=True)


def test_csp_accepted_only_when_script_src_is_self():
    assert csp_failures({o: CSP_OK for o in ORIGINS}, ORIGINS) == []


def test_csp_rejects_unsafe_inline_and_missing_directive():
    assert csp_failures({o: "script-src 'self' 'unsafe-inline'" for o in ORIGINS}, ORIGINS)
    assert csp_failures({o: "default-src 'self'" for o in ORIGINS}, ORIGINS)


def test_csp_rejects_an_origin_that_was_never_observed():
    assert csp_failures({ORIGINS[0]: CSP_OK}, ORIGINS)
    assert csp_failures({}, [])
