"""Must-fire / must-not-fire tests for the browser a11y gate (release gate G11).

scripts/browser_a11y.py audits a live stack, which only proves the verdict it
printed. These tests prove the verdict functions can disagree with their input,
so "browser a11y + walkthrough passed" means something.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from browser_a11y import (contrast_ratio, csp_failures, danger_pair, gate_failures,  # noqa: E402
                       hidden_failures, mobile_fit_failures)

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


def test_filled_danger_buttons_meet_aa_contrast_in_both_apps():
    # axe only flags the pair once such a button is actually enabled and rendered, which is how
    # white-on-#ff6b6b survived every run until a panel put an always-enabled one in the roster.
    for name in ("services/web/styles.css", "services/admin/styles.css"):
        css = (ROOT / name).read_text(encoding="utf-8")
        foreground, background = danger_pair(css)
        measured = contrast_ratio(foreground, background)
        assert measured >= 4.5, f"{name}: {foreground} on {background} measures {measured:.2f}:1"


def test_the_contrast_reader_resolves_tokens_rather_than_reading_var_literals():
    css = (":root { --danger: #ff6b6b; --fg: #fff; }\n"
           "button.danger { background: var(--danger); color: var(--fg); }")
    assert danger_pair(css) == ("#fff", "#ff6b6b"), "the resolver must hand back colours, not var() text"
    # Control: the pair that shipped has to read as a violation, and the replacement as compliant.
    assert contrast_ratio("#ffffff", "#ff6b6b") < 4.5
    assert contrast_ratio("#ffffff", "#b3261e") >= 4.5


def test_the_contrast_reader_refuses_a_rule_it_cannot_evaluate():
    for broken in ("button.danger { color: #fff; }", ":root { --x: red; } button.danger { background: var(--x); color: #fff; }"):
        try:
            danger_pair(broken)
        except AssertionError:
            continue
        raise AssertionError(f"a danger rule with no readable pair slipped through: {broken[:48]!r}")


def test_an_element_marked_hidden_must_actually_not_render():
    """`label { display: grid }` outranks the UA's `[hidden]`, and that is how a verification-code
    field meant only for armed accounts rendered on the erasure panel. The gate's page-wide sweep is
    what caught it; this pins that the sweep can both see it and stay out of the way."""
    assert hidden_failures([scan()], require=False) == []
    assert hidden_failures([scan(hidden_marked=4, hidden_but_rendered=[])]) == []
    finding = hidden_failures([scan(label="account-privacy-panel", hidden_marked=4,
                                    hidden_but_rendered=["#erasureCodeWrap"])])
    assert len(finding) == 1, finding
    assert "erasureCodeWrap" in finding[0] and "account-privacy-panel" in finding[0], finding
    # A sweep over a denominator of zero reads exactly like a clean one, so it is not allowed to.
    assert hidden_failures([scan(hidden_marked=0), scan(label="v2", hidden_marked=0)])
    assert hidden_failures([scan(hidden_marked=0)], require=False) == []
    # One aggregate line, but every label:id pair counts as an offender.
    both = hidden_failures([scan(hidden_marked=2, hidden_but_rendered=["#a"]),
                            scan(label="view-2", hidden_marked=3, hidden_but_rendered=["#a", "#b"])])
    assert len(both) == 1 and "#a" in both[0] and "#b" in both[0] and "view-2" in both[0], both
