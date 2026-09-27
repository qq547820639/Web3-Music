"""Every payload the acceptance chain dispatches must also be dispatched by CI, or be exempt on purpose.

The reason this census exists: migration 020 made "clean" a claim that needs a scanner behind it, and
the round that shipped it added `scripts/media_scan_drill.py` as chain step 14 -- while
`.github/workflows/ci.yml` went on dispatching the erasure, mfa and member drills and never once
invoked the new one. Nothing compared the two dispatch lists, so the boundary with the most teeth was
enforced on one host and not in the shared gate, and both looked green.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHAIN = ROOT / "scripts/acceptance-all.sh"
CI = ROOT / ".github/workflows/ci.yml"

PAYLOAD = re.compile(r"(?:python3?\s+|\./|bash\s+|sh\s+)?scripts/([A-Za-z0-9._-]+\.(?:py|sh))")

# Each exemption states the reading that earns it, and `test_the_exemptions_are_backed_by_a_reading`
# re-checks that reading, so an exemption cannot outlive the fact that justified it.
EXEMPT = {
    "test.sh": "CI runs the payload that script exists to run -- `docker compose --profile test run "
               "--rm acceptance` -- and builds the image through its own `up --build` step, so the "
               "wrapper adds nothing there that the workflow does not already do.",
}
ACCEPTANCE_RUN = re.compile(r"docker compose --profile test run --rm acceptance")


def code_lines(path):
    """The file without comment lines -- prose naming a script is not the same as dispatching it."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.strip().startswith("#"))


def dispatched(path):
    return set(PAYLOAD.findall(code_lines(path)))


def dispatched_text(text):
    return set(PAYLOAD.findall("\n".join(line for line in text.splitlines()
                                          if not line.strip().startswith("#"))))


def offenders(chain_text=None, ci_text=None):
    chain = dispatched(CHAIN) if chain_text is None else dispatched_text(chain_text)
    ci = dispatched(CI) if ci_text is None else dispatched_text(ci_text)
    return sorted(chain - ci - set(EXEMPT))


# ---------------------------------------------------------------------- the guard ----

def test_every_chain_payload_is_dispatched_by_ci_or_exempt():
    missing = offenders()
    assert not missing, "the acceptance chain dispatches scripts CI never runs: " + ", ".join(missing)


def test_the_chain_actually_dispatches_the_drills_this_census_is_about():
    """Without this, an empty chain list would make the subset assertion pass for the wrong reason."""
    chain = dispatched(CHAIN)
    assert {"media_scan_drill.py", "member_drill.py", "mfa_drill.py", "erasure_drill.py",
            "restore_fidelity.py", "lease-contention.sh", "chaos-worker-recovery.sh"} <= chain, sorted(chain)


# ---------------------------------------------------------------------- the controls ----

def test_the_census_fires_on_a_drill_that_ci_stops_running():
    """The exact shape of the defect: the chain gained a step, CI never did."""
    stripped = code_lines(CI).replace("python scripts/media_scan_drill.py", "python scripts/member_drill.py")
    assert "media_scan_drill.py" not in stripped
    assert offenders(ci_text=stripped) == ["media_scan_drill.py"], offenders(ci_text=stripped)


def test_the_census_fires_on_a_new_chain_step_at_all():
    """A chain step with no CI counterpart must be named, not silently dropped from the denominator."""
    invented = CHAIN.read_text(encoding="utf-8") + "\n  python scripts/zzz_new_gate_drill.py\n"
    assert offenders(chain_text=invented) == ["zzz_new_gate_drill.py"], offenders(chain_text=invented)
    assert offenders() == []


def test_the_exemptions_are_backed_by_a_reading():
    for name, why in EXEMPT.items():
        assert len(why) > 40, f"{name}: an exemption must say why, not just name itself"
        assert name in dispatched(CHAIN), f"{name}: exempt but the chain never dispatches it"
        assert name not in dispatched(CI), f"{name}: exempt but CI runs it anyway"
    assert ACCEPTANCE_RUN.search(code_lines(CI)), \
        "test.sh is exempted because CI runs its payload directly; that line is gone"


def test_ci_leans_on_the_same_build_identity_guard_the_chain_gained():
    """Why CI needs no profile-wide build of its own: it runs the step that checks the builds agree.

    On a throwaway runner `worker-b` has no cached image, so `lease-contention.sh`'s `up` builds it from
    the same checkout -- the stale-image shape this host fell into came from a long-lived dev VM. The
    reading that keeps CI honest is the preflight inside that script, so what this census asserts is
    that CI dispatches the script at all (see the guard above), not that it rebuilds the contender.
    """
    assert "lease-contention.sh" in dispatched(CI)
    assert "contention preflight" in (ROOT / "scripts/lease-contention.sh").read_text(encoding="utf-8")
