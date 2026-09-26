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


# --------------------------------------------------------- the chain's own build step ----
# Retrying inside one image is only half of it: the release chain used to build every image at once,
# and on the 4 vCPU / 5.8 GiB Colima VM that is what killed an unrelated apt-get with SIGKILL while two
# other images were pulling wheels.

def compose_up_body(script: str) -> str:
    """The body of step_stack_up(), the chain's build-and-start step."""
    found = re.search(r"step_stack_up\(\)\s*\{(.*?)\n\}", script, re.DOTALL)
    assert found, "acceptance-all.sh no longer defines step_stack_up"
    return found.group(1)


def shell_commands(body: str) -> str:
    """Only the executable lines.

    The step's own comment explains why `up --build` was dropped, so a text-face search for that flag
    over the whole body finds it in the prose and reports the shape as still present -- the same trap as
    an absence assertion blocked by a "do not write this back" note. Comments are stripped first.
    """
    return "\n".join(line.strip() for line in body.splitlines()
                     if line.strip() and not line.strip().startswith("#"))


def test_the_chain_builds_one_image_at_a_time():
    body = shell_commands(compose_up_body((ROOT / "scripts/acceptance-all.sh").read_text(encoding="utf-8")))
    assert "--build" not in body, \
        "`up --build` builds every service concurrently; measured on the release VM that killed " \
        "worker's apt-get with SIGKILL (rc 137) while other images were downloading"
    assert re.search(r"for\s+\w+\s+in\s+\$\(docker compose config --services\)", body), \
        "the build loop must enumerate the compose services, not a hand-kept list that goes stale"
    assert re.search(r"docker compose build \"?\$\{?\w+\}?\"?\s*\|\|\s*return 1", body), \
        "a failed image build has to fail the step rather than fall through to `up`"


def test_the_build_shape_detector_fires_on_the_concurrent_form():
    old = ("step_stack_up() {\n"
           "  docker compose up --build -d\n"
           "  docker compose ps\n"
           "}\n")
    body = shell_commands(compose_up_body(old))
    assert "--build" in body
    assert not re.search(r"for\s+\w+\s+in\s+\$\(docker compose config --services\)", body)
    assert "--build" in compose_up_body(old)  # the prose-strip is what makes the reading above honest
    current = shell_commands(compose_up_body((ROOT / "scripts/acceptance-all.sh").read_text(encoding="utf-8")))
    assert "--build" not in current


# ---------------------------------------------------------------- apt on the same footing ----
# The image that installs a system package reaches for a 9.6 MB index over the same path that killed
# pip, and apt's own default is three attempts -- which is what `Ign:` in the release log meant.

APT_VERBS = {"update", "install", "upgrade", "purge"}
APT_RETRIES = re.compile(r"Acquire::Retries=(\d+)")


def apt_invocations(block: str) -> list[str]:
    """Every `apt-get <verb>` in a RUN, with its option tokens skipped.

    Token-walking rather than one regex: `apt-get -o Acquire::Retries=8 update` puts a *value* between
    the flag and the verb, and the first version of this matcher read that invocation as absent -- which
    made the clause below vacuous, since a Dockerfile with no retry settings at all then had zero apt
    calls to complain about. The control test is what caught that.
    """
    tokens = block.replace("&&", " && ").replace(";", " ; ").split()
    found = []
    for index, token in enumerate(tokens):
        if token != "apt-get":
            continue
        for following in tokens[index + 1:]:
            if following.startswith("-") or "=" in following:
                continue
            if following in APT_VERBS:
                found.append(following)
            break
    return found


def apt_runs(text: str) -> list[str]:
    return [block for block in run_steps(text) if apt_invocations(block)]


def test_every_apt_step_retries_its_fetch():
    offenders = []
    for path in sorted((ROOT / "services").glob("*/Dockerfile")):
        for block in apt_runs(path.read_text(encoding="utf-8")):
            settings = [int(n) for n in APT_RETRIES.findall(block)]
            calls = apt_invocations(block)
            if len(settings) < len(calls):
                offenders.append(f"{path.parent.name}: {len(settings)} retry settings for "
                                 f"{len(calls)} apt calls ({', '.join(calls)})")
            elif settings and min(settings) < 5:
                offenders.append(f"{path.parent.name}: Acquire::Retries={min(settings)}")
    assert not offenders, "apt steps that give up early: " + " | ".join(offenders)


def test_the_apt_detector_fires_on_the_shape_that_shipped_until_now():
    before = ("FROM python:3.12-slim\n"
              "RUN apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends ffmpeg\n")
    block = apt_runs(before)
    assert block, "the reader must see this RUN at all"
    calls = apt_invocations(block[0])
    found = [int(n) for n in APT_RETRIES.findall(block[0])]
    assert calls == ["update", "install"], (calls, block[0])
    assert found == [3] and len(found) < len(calls), (found, calls)
    live = apt_runs((ROOT / "services/worker/Dockerfile").read_text(encoding="utf-8"))
    assert live and len(apt_invocations(live[0])) == len(APT_RETRIES.findall(live[0])), live
    assert min(int(n) for n in APT_RETRIES.findall(live[0])) >= 5
