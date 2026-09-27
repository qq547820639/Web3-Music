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

CHAIN = ROOT / "scripts/acceptance-all.sh"
CAPACITY_SCRIPT = ROOT / "scripts/capacity-gate-500.sh"
STACK_STEPS = ("step_stack_up", "step_commercial", "step_generic_rest_roundtrip")


def function_body(text: str, name: str) -> str:
    found = re.search(rf"{name}\(\)\s*\{{(.*?)\n\}}", text, re.DOTALL)
    assert found, f"{name} is no longer defined in the chain"
    return found.group(1)


def shell_commands(body: str) -> str:
    """Only the executable lines.

    The step's own comment explains why `up --build` was dropped, so a text-face search for that flag
    over the whole body finds it in the prose and reports the shape as still present -- the same trap as
    an absence assertion blocked by a "do not write this back" note. Comments are stripped first.
    """
    return "\n".join(line.strip() for line in body.splitlines()
                     if line.strip() and not line.strip().startswith("#"))


def build_loop_head(body: str) -> str:
    """The line feeding a build loop, when that line is derived from `compose config`.

    Judged as a shape, not as one spelling. The first version of this clause pinned the literal
    `$(docker compose config --services)`, which is exactly the enumeration that hid this round's
    defect: `config --services` omits services that sit behind a profile, so `worker-b` was never
    rebuilt and the contention step raced a new worker against an hours-old one.
    """
    found = re.search(r"for\s+\w+\s+in\s+\$\(docker compose[^\n]*\bconfig\b[^\n]*", body)
    return found.group(0) if found else ""


# `--profile '*'` and `--all-profiles` are the two spellings that make config see the whole file.
PROFILE_WIDE = re.compile(r"--profile\s+[\"']?\*|--all-profiles")


def test_the_chain_builds_one_image_at_a_time():
    text = CHAIN.read_text(encoding="utf-8")
    assert "--build" not in shell_commands(text), \
        "`up --build` builds every service concurrently; measured on the release VM that killed " \
        "worker's apt-get with SIGKILL (rc 137) while other images were downloading"
    helper = function_body(text, "build_images_in_order")
    head = build_loop_head(helper)
    assert head, \
        "the build helper must enumerate whatever compose reports, not a hand-kept list of service " \
        "names that silently goes stale when a profile or a service is added"
    assert PROFILE_WIDE.search(head), \
        "the enumeration has to see profile-gated services: `config --services` skips them, which is " \
        "how acceptance-20260927T030055Z step 6 came to race worker (rebuilt) against worker-b " \
        "(hours old, no media_scan.py) -- the stale one wrote the literal 'clean' into media_assets, " \
        "migration 020's trigger refused it, and a job settled partial"
    assert re.search(r"docker compose[^\n]*\bbuild\b[^\n]*\|\|\s*return 1", helper), \
        "a failed image build has to fail the step rather than fall through to `up`"
    for step in STACK_STEPS:
        body = function_body(text, step)
        assert "build_images_in_order" in body, f"{step} starts a stack without the serial build helper"
        assert re.search(r"docker compose[^\n]*up -d", body), f"{step} no longer starts the stack"
    capacity = shell_commands(CAPACITY_SCRIPT.read_text(encoding="utf-8"))
    assert "--build" not in capacity, "the capacity gate builds every image at once again"
    assert re.search(r"docker compose[^\n]*\bbuild\b", capacity), "the capacity gate stopped building"


def test_the_build_shape_detector_fires_on_the_concurrent_form():
    old = ("step_stack_up() {\n"
           "  docker compose up --build -d\n"
           "  docker compose ps\n"
           "}\n")
    body = shell_commands(function_body(old, "step_stack_up"))
    assert "--build" in body
    assert not build_loop_head(body)
    assert "--build" in function_body(old, "step_stack_up")  # the prose-strip is what makes the above honest
    assert "--build" not in shell_commands(CHAIN.read_text(encoding="utf-8"))


def test_a_step_that_drops_the_helper_is_reported_by_name():
    """The per-step clause needs its own red side: a step going back to `up --build` must not pass."""
    text = CHAIN.read_text(encoding="utf-8")
    drifted = text.replace("  build_images_in_order -f docker-compose.yml -f docker-compose.commercial-test.yml\n"
                           "  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up -d",
                           "  docker compose -f docker-compose.yml -f docker-compose.commercial-test.yml up --build -d")
    assert drifted != text, "the fixture no longer matches the commercial step"
    body = shell_commands(function_body(drifted, "step_commercial"))
    assert "build_images_in_order" not in body
    assert "--build" in body


def test_the_profile_clause_fires_on_the_form_that_caused_this_round():
    """Each new clause needs the minimal shape it exists to reject, and one it must not touch.

    The profile-blind loop is the hard case: it passes every other assertion here, because it is
    compose-derived, serial and fail-fast. Only the profile-wide flag separates it from this round's
    shape -- so if that flag were dropped, this control is what reddens, not the chain's step 6.
    """
    blind = ("build_images_in_order() {\n"
             "  for service in $(docker compose config --services); do\n"
             "    docker compose build \"$service\" || return 1\n"
             "  done\n"
             "}\n")
    blind_body = shell_commands(function_body(blind, "build_images_in_order"))
    assert build_loop_head(blind_body), "the derived-from-config clause must NOT be what catches it"
    assert re.search(r"docker compose[^\n]*\bbuild\b[^\n]*\|\|\s*return 1", blind_body)
    assert not PROFILE_WIDE.search(build_loop_head(blind_body)), \
        "the profile clause went quiet on the exact shape it was written for"

    wide = ("build_images_in_order() {\n"
            "  for service in $(docker compose --profile '*' config --format json); do\n"
            "    docker compose --profile '*' build \"$service\" || return 1\n"
            "  done\n"
            "}\n")
    wide_head = build_loop_head(shell_commands(function_body(wide, "build_images_in_order")))
    assert wide_head and PROFILE_WIDE.search(wide_head)
    flags = lambda line: sorted(set(re.findall(r"--[\w-]+", line)))  # noqa: E731  (one-use reader)
    assert flags(wide_head) == flags(build_loop_head(function_body(
        CHAIN.read_text(encoding="utf-8"), "build_images_in_order"))), \
        "the fixture no longer describes the flags the chain's enumeration is called with"

    hand_list = ("build_images_in_order() {\n"
                 "  for service in api worker gateway; do\n"
                 "    docker compose build \"$service\" || return 1\n"
                 "  done\n"
                 "}\n")
    assert not build_loop_head(shell_commands(function_body(hand_list, "build_images_in_order"))), \
        "a hand-kept list reads as derived enumeration and passes quietly"


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
