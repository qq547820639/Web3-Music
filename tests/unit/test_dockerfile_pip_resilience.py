"""Every service image must survive an index blip without the release chain noticing.

The authoritative run of 2026-09-26T15:48:04Z died at step 2, compose-up, before a single assertion
about this release was exercised: `pip install` inside the migrate image got `Could not find a version
that satisfies the requirement psycopg2-binary==2.9.10 (from versions: none)` while the host was
pulling at 23 kB/s, and the same message came back for `websockets` in the provider emulator. Nothing
about those pins had changed and the build has no second attempt, so one network state aborted twenty
certified steps. The retry is what turns that into a slow build: the same image, rebuilt minutes later
against the same degradation, logged `pip attempt 1 failed, retrying` and then installed.

What is checkable without a network is the shape, and two shapes matter:

  * a bounded loop, so the build cannot hang forever; and
  * an exit status that is still a failure after the last attempt. A `for ...; do cmd && break; done`
    body ends with the loop's own status, so the whole RUN succeeds having installed nothing -- which
    is worse than today's failure, because the image then boots without its dependencies.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

RETRY_LOOP = re.compile(r"for\s+\w+\s+in\s+((?:\d+\s+)+\d+)")
PIP_INSTALL = re.compile(r"pip3?\s+install")


def dockerfiles() -> list[pathlib.Path]:
    """Every service image that installs Python dependencies.

    Matched on `pip install` alone, not on `-r requirements.txt`: services/loadtest installs one pinned
    package inline, and an image that reaches the same index is exposed to the same blip.
    """
    found = []
    for path in sorted((ROOT / "services").glob("*/Dockerfile")):
        if PIP_INSTALL.search(path.read_text(encoding="utf-8")):
            found.append(path)
    return found


def run_steps(text: str) -> list[str]:
    """Each RUN instruction, joined across its backslash continuations.

    Assembled line by line rather than matched with a `.*` under DOTALL: a greedy match swallows every
    instruction after the first RUN, and the census would then report one giant step per file.
    """
    steps, lines, index = [], text.splitlines(), 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("RUN"):
            block = line
            while block.rstrip().endswith("\\") and index + 1 < len(lines):
                index += 1
                block += " " + lines[index]
            steps.append(" ".join(block.split()))
        index += 1
    return steps


def pip_run_lines(text: str) -> list[str]:
    return [block for block in run_steps(text) if PIP_INSTALL.search(block)]


def attempts(text: str) -> int:
    counts = [len(found.split()) for block in pip_run_lines(text) for found in RETRY_LOOP.findall(block)]
    return max(counts) if counts else 1


def exits_non_zero_when_exhausted(text: str) -> bool:
    for block in pip_run_lines(text):
        if re.search(r"for\b", block) and re.search(r";\s*exit\s+1\s*$", block.strip()):
            return True
        if not re.search(r"for\b", block):
            return True          # no loop at all: a plain failure, which is honest about itself
    return False


# ------------------------------------------------------------------ the guards ----

def test_every_python_image_retries_its_install():
    paths = dockerfiles()
    assert len(paths) >= 6, f"only {len(paths)} dependency-installing images were found, so this census is blind"
    offenders = [str(p.relative_to(ROOT)) for p in paths if attempts(p.read_text(encoding="utf-8")) < 3]
    assert not offenders, f"images that abort the chain on one index blip: {offenders}"


def test_the_retry_keeps_its_failure():
    paths = dockerfiles()
    offenders = [str(p.relative_to(ROOT)) for p in paths if not exits_non_zero_when_exhausted(p.read_text(encoding="utf-8"))]
    assert not offenders, (
        f"these RUN steps exit 0 after exhausting their attempts, so an image with nothing installed "
        f"would build green: {offenders}")


def test_the_retry_stays_bounded():
    for path in dockerfiles():
        found = attempts(path.read_text(encoding="utf-8"))
        assert found <= 8, f"{path} retries {found} times; a build that never ends is not resilience"


def test_the_shape_detector_fires_on_a_loop_that_reports_success_on_failure():
    silent = ("FROM python:3.12-slim\n"
              "COPY requirements.txt .\n"
              "RUN for try in 1 2 3; do pip install --no-cache-dir -r requirements.txt && break; done\n")
    assert PIP_INSTALL.search(silent)
    assert attempts(silent) == 3, "the attempt counter must see a break-style loop too"
    assert not exits_non_zero_when_exhausted(silent), \
        "this is the shape that builds an empty image and calls it a success"
    bounded = ("FROM python:3.12-slim\n"
               "COPY requirements.txt .\n"
               "RUN set -e; for attempt in 1 2 3; do pip install --no-cache-dir -r requirements.txt && exit 0; "
               "sleep 5; done; echo failed; exit 1\n")
    assert attempts(bounded) == 3
    assert exits_non_zero_when_exhausted(bounded)
    assert attempts("RUN pip install --no-cache-dir -r requirements.txt\n") == 1


def test_the_census_sees_every_installing_image():
    """The denominator is what makes the first guard mean something: an image that quietly stops
    matching the pattern would drop out of the census and pass."""
    all_dockerfiles = sorted((ROOT / "services").glob("*/Dockerfile"))
    installed = {p.parent.name for p in dockerfiles()}
    non_python = {p.parent.name for p in all_dockerfiles if p.parent.name not in installed}
    assert installed, "no image matched, so nothing here is being checked"
    for name in sorted(non_python):
        text = (ROOT / "services" / name / "Dockerfile").read_text(encoding="utf-8")
        assert not PIP_INSTALL.search(text), f"{name} installs Python packages in a way the census misses"
    assert {"api", "worker", "migrate", "loadtest"} <= installed, sorted(installed)
