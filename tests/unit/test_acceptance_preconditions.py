"""The acceptance chain's environment preconditions: what must be true before step 1 runs.

Why this is a gate at all: a red that comes from the machine reads like a red that comes from the code.
Two shapes bit this repository on 2026-09-27, both after the precheck existed in some form --
`static-verify` reported `tracked=0 listed=229 stale=229` on a PATH without `/sbin` (a missing host
binary, presented as a broken manifest), and mfa-drill reported "an unarmed account still logs in with a
password alone -- 500" while the api traceback said `psycopg2.errors.DiskFull: could not extend file
"base/18221/18380"` with 718780 KB free on the Docker data disk. Both wrote an evidence directory whose
only true content was "this ran in the wrong place".
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHAIN = ROOT / "scripts/acceptance-all.sh"
TEXT = CHAIN.read_text(encoding="utf-8")

# Everything before the evidence directory is created: a failure there must exit 2 and leave no run
# behind, because a run directory implies a verdict about the code.
PRECHECK = TEXT[:TEXT.index('EVIDENCE_DIR="release-evidence/acceptance-${STAMP}"')]


def executable(text):
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def test_the_precheck_covers_every_host_binary_a_step_shells_out_to():
    """Interpreters are not the whole dependency list: steps also call `sha256sum` and `xargs`."""
    checked = set(re.findall(r"for tool in ([^;]+); do", PRECHECK))
    names = set().union(*(c.split() for c in checked)) if checked else set()
    assert {"python", "node", "docker", "sha256sum", "xargs"} <= names, sorted(names)
    used = {tool for tool in ("sha256sum", "xargs")
            if re.search(rf"\b{tool}\b", "\n".join(
                line for line in (ROOT / "scripts/acceptance-all.sh").read_text().splitlines()
                if not line.strip().startswith("#")))}
    assert used <= names, f"host binaries used by the chain but not pre-checked: {sorted(used - names)}"


def test_nothing_in_the_precheck_writes_an_evidence_directory():
    assert "mkdir -p" not in PRECHECK
    assert PRECHECK.count("exit 2") >= 3


def test_the_disk_floor_is_an_integer_and_the_unknown_case_never_compares():
    """`[ unknown -lt 4194304 ]` under `set -e` aborts the precheck with a shell error, not a verdict."""
    code = executable(PRECHECK)
    comparison = re.search(r'if \[ "\$disk_free_kb" != "unknown" \] && \[ "\$disk_free_kb" -lt "\$MIN_FREE_KB" \]',
                           code)
    assert comparison, "the disk comparison must be guarded against the non-numeric case"
    assert re.search(r"MIN_FREE_KB=\$\{MIN_FREE_KB:-\d+\}", code), "the floor needs a numeric default"
    assert code.index('disk_free_kb=unknown') < comparison.start(), "unknown must be the initial state"


def test_the_reading_the_precheck_prints_is_the_one_the_run_records():
    """A precondition that is checked but not written down cannot be audited after the fact."""
    header = re.search(r"\{\n(?:.*\n)*?\} > \"\$RESULTS_FILE\"", TEXT)
    assert header, "the SUMMARY header block could not be located to read it"
    recorded = header.group(0)
    assert 'echo "disk_free_kb=$disk_free_kb"' in recorded, "SUMMARY must record the free-space reading"
    assert 'echo "fresh_database=${FRESH:-0}"' in recorded, "SUMMARY must record whether it cleared volumes"
    assert re.search(r'echo "git_commit=\$\(git rev-parse HEAD', recorded), "the tree anchor stays required"


def test_the_failure_message_carries_the_measurement_that_unblocks_the_reader():
    """Refusing to run is only useful if it says what to do and how much, from a real reading."""
    refusal = PRECHECK[PRECHECK.index('if [ "$disk_free_kb" != "unknown" ]'):]
    refusal = refusal[:refusal.index("fi")]
    assert "KB" in refusal and "MIN_FREE_KB" in (refusal + PRECHECK)
    assert re.search(r"\d{6,}", refusal), "the message must quote an actual measured number"
