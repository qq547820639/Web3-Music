"""The environment contract: every name the code reads is provided somewhere, and every name
provided is read by someone.

Why this is a gate and not a convention. CI runs `cp .env.example .env` (five places in
.github/workflows/ci.yml) and the api/worker containers take `env_file: [.env]`, so this file *is*
the deployment configuration for every run that produces evidence. Two failure modes are invisible
without a check: a name the code reads that nobody provides (the value silently comes from an
in-file default, which is how a published demo signing key reached production -- see
test_secret_defaults.py), and a name provided that nobody reads (a control that looks configured and
does nothing, e.g. the webhook secret that used to sit on the provider-emulator, whose code never
pushes a webhook and which read as if it did).

Three grammars, because the names are written three ways:
  * python: os.getenv("X"), os.environ["X"], and this repo's own wrappers csv("X") / boolean("X").
    A regex aimed only at os.getenv misses the wrappers, and the wrappers are what read
    CORS_ORIGINS and COOKIE_SECURE -- the two that decide whether the session cookies are sent.
  * compose: the `environment:` mapping keys *and* ${NAME} interpolations, including the host-port
    variables that never enter a container.
  * Dockerfile: ENV/ARG, which is a provider, not a reader. (Classifying it as a reader is the
    easiest way to make this check pass for the wrong reason: PYTHONDONTWRITEBYTECODE looked like a
    name the code read and nobody provided.)

Each allow-list entry below carries a reason and is checked to still be true, so a list cannot
accrete into a way of making the gate quiet.
"""
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "backups", "release-evidence",
              "capacity-results", ".cache", "htmlcov"}

READS = re.compile(r"""(?:"""
                  r"""(?:os\.getenv|os\.environ\.get|getenv|environ\.get)\(\s*|"""
                  r"""os\.environ\[|environ\["""
                  r""")["']([A-Z][A-Z0-9_]*)["']"""
                  r"""|(?:\bcsv|\bboolean)\(\s*["']([A-Z][A-Z0-9_]*)["']""", re.VERBOSE)
INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]+)")
DOCKERFILE_SET = re.compile(r"^\s*(?:ENV|ARG)\s+([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
EXAMPLE_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$")

# Read by the code, provided by nothing in the repository, and correct to be so.
HOST_RUNTIME_KNOBS = {
    "WEB_URL": "browser-only host address for the accessibility gate; the container does not read it",
    "ADMIN_URL": "same, for the admin console",
    "E2E_EMAIL": "which account the accessibility gate signs in as; a host-side choice, not app config",
    "E2E_PASSWORD": "the same account's password, overridable so the gate can run against a copied stack",
    "AXE_CACHE_DIR": "where the gate caches axe-core on the host",
    "WORKER_ID": "per-process lease identity; the code derives a unique one when it is unset",
    "CONTRACTS_DIR": "path inside the api image, where the mounted contracts already land",
    "MEDIA_HTTP_MAX_CONNECTIONS": "derived from WORKER_CONCURRENCY, so a static value would freeze what "
                                  "the capacity overlay scales",
    "GENERIC_PROVIDER_AUTH_PREFIX": "the in-code default is 'Bearer ' with a trailing space, which a "
                                    "value routed through compose and python-dotenv cannot survive; "
                                    "see the comment in .env.example",
}

# Provided by the deployment files, read by something other than this repository's python.
OTHER_CONSUMERS = {
    "POSTGRES_USER": "the postgres image's own bootstrap",
    "POSTGRES_PASSWORD": "the postgres image's own bootstrap",
    "POSTGRES_DB": "the postgres image's own bootstrap",
    "MINIO_ROOT_USER": "the MinIO image's own credentials",
    "MINIO_ROOT_PASSWORD": "the MinIO image's own credentials",
    "REDIS_PASSWORD": "the redis image's requirepass",
    "API_WORKERS": "uvicorn's own flag, read by the api image's command line",
    "API_LIMIT_CONCURRENCY": "uvicorn's own flag, read by the api image's command line",
    "API_KEEPALIVE_SECONDS": "uvicorn's own flag, read by the api image's command line",
    "PORT": "the emulator images' uvicorn --port argument",
    "GATEWAY_PORT": "a compose host-port mapping; it never enters a container",
    "PROMETHEUS_PORT": "a compose host-port mapping; it never enters a container",
    "PUBLIC_ORIGIN": "an interpolation input for CORS_ORIGINS in the production overlay",
    "PYTHONDONTWRITEBYTECODE": "the CPython interpreter itself, via the api/worker images",
}


# Only what actually runs. tests/ is deliberately out of the census: it contains the synthetic
# canary names this file feeds its own grammars, and counting those as configuration would make the
# direction checks fail on their own fixtures.
CODE_ROOTS = ("services", "scripts")


def python_reads() -> set[str]:
    names = set()
    paths = [path for root in CODE_ROOTS for path in (ROOT / root).rglob("*.py")]
    for path in paths:
        if SKIP_PARTS & set(path.parts):
            continue
        for match in READS.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            names.add(match.group(1) or match.group(2))
    return names


def dockerfile_provides() -> set[str]:
    names = set()
    for path in ROOT.rglob("Dockerfile*"):
        if SKIP_PARTS & set(path.parts):
            continue
        names.update(DOCKERFILE_SET.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return names


def compose_files() -> list[pathlib.Path]:
    return [ROOT / "docker-compose.yml"] + sorted(ROOT.glob("docker-compose.*.yml"))


def compose_environment_keys() -> set[str]:
    names = set()
    for path in compose_files():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for service in (document.get("services") or {}).values():
            environment = (service or {}).get("environment")
            if isinstance(environment, dict):
                names.update(key for key in environment if isinstance(key, str))
            elif isinstance(environment, list):
                names.update(str(entry).split("=", 1)[0] for entry in environment)
    return names


def compose_interpolations() -> set[str]:
    names = set()
    for path in compose_files():
        names.update(INTERPOLATION.findall(path.read_text(encoding="utf-8")))
    return names


def example_file_keys() -> set[str]:
    return {match.group(1) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if (match := EXAMPLE_LINE.match(line))}


def provided_names() -> set[str]:
    """Everything the repository offers as configuration, by any of the three provider routes.

    One definition, used by every direction check: three hand-copied unions is how a gate ends up
    computing the same question differently in different tests and then agreeing with itself.
    """
    return example_file_keys() | compose_environment_keys() | compose_interpolations() | dockerfile_provides()


# ------------------------------------------------------------------ reality ----

def test_the_reader_grammar_finds_the_names_it_must_see():
    """Non-vacuity first: a census that derived nothing would make every assertion below a tautology."""
    reads = python_reads()
    assert len(reads) >= 60, f"the python grammar derived only {len(reads)} names; it has gone blind"
    for name in ("DATABASE_URL", "JWT_SECRET", "CORS_ORIGINS", "COOKIE_SECURE", "MFA_ENCRYPTION_KEY",
                 "DEMO_STACK", "GENERIC_PROVIDER_AUTH_PREFIX"):
        assert name in reads, f"the python grammar missed {name}"
    assert "CORS_ORIGINS" in reads and "COOKIE_SECURE" in reads, \
        "csv()/boolean() are this repo's own getenv wrappers; if they drop out, the two names that " \
        "decide whether session cookies are sent go uncounted too"


def test_the_wrapper_grammar_catches_a_canary_no_os_getenv_regex_would_see():
    source = ('from .settings import csv, boolean\n'
              'plan: str = csv("G10_CANARY_CSV_WRAPPER", "trial")\n'
              'flag: bool = boolean("G10_CANARY_BOOLEAN_WRAPPER", False)\n'
              'direct: str = __import__("os").getenv("G10_CANARY_DIRECT", "")\n'
              'bracket: str = os.environ["G10_CANARY_BRACKET"]\n')
    found = {m.group(1) or m.group(2) for m in READS.finditer(source)}
    assert {"G10_CANARY_CSV_WRAPPER", "G10_CANARY_BOOLEAN_WRAPPER", "G10_CANARY_DIRECT",
            "G10_CANARY_BRACKET"} <= found, found
    plain = re.compile(r'''os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']''')
    assert "G10_CANARY_CSV_WRAPPER" not in {m.group(1) for m in plain.finditer(source)}, \
        "the naive pattern is supposed to be the one that misses the wrapper, or this control proves nothing"


def test_the_compose_grammars_catch_both_a_mapping_key_and_an_interpolation():
    source = ('services:\n  api:\n    environment:\n      G10_CANARY_KEY: "1"\n'
              '      OTHER: ${G10_CANARY_INTERPOLATED:-x}\n')
    document = yaml.safe_load(source)
    keys = {k for k in document["services"]["api"]["environment"]}
    assert "G10_CANARY_KEY" in keys and "OTHER" in keys
    assert "G10_CANARY_INTERPOLATED" in set(INTERPOLATION.findall(source))


def test_dockerfile_env_counts_as_provided_not_read():
    source = "FROM python:3.12\nENV G10_CANARY_ENV=1\nARG G10_CANARY_ARG=2\n"
    assert {"G10_CANARY_ENV", "G10_CANARY_ARG"} == set(DOCKERFILE_SET.findall(source))
    assert not READS.search(source), "the reader grammar must not claim an ENV line as a read"


# ----------------------------------------------------------------- directions ----

def test_every_name_the_code_reads_is_provided_or_explained():
    unexplained = (python_reads() - provided_names()) - set(HOST_RUNTIME_KNOBS)
    assert not unexplained, (
        f"read by code, provided by nothing in the repo, and not explained: {sorted(unexplained)}. "
        "Either add it to .env.example with its default, or say here why the repository must not provide it")


def test_every_documented_name_is_actually_read_by_someone():
    unread = provided_names() - python_reads() - set(OTHER_CONSUMERS)
    assert not unread, (
        f"provided but read by nobody in this repository or the images: {sorted(unread)}. A control "
        "that is wired to nothing is worse than absent: it reads as configured")


def test_the_allow_lists_cannot_accrete_into_noise():
    """Each entry must still be doing the job it was added for."""
    reads, provided = python_reads(), provided_names()
    stale_knobs = {name: reason for name, reason in HOST_RUNTIME_KNOBS.items() if name not in reads}
    assert not stale_knobs, f"excused as read-by-code but no longer read anywhere: {stale_knobs}"
    now_provided = {name for name in HOST_RUNTIME_KNOBS if name in provided}
    assert not now_provided, f"excused as unprovided, but the repo does provide them now: {now_provided}"
    consumed = reads | provided
    dead = {name for name in OTHER_CONSUMERS if name not in consumed}
    assert not dead, f"attributed to another consumer that no longer exists here: {sorted(dead)}"
    assert all(reason.strip() for reason in list(HOST_RUNTIME_KNOBS.values()) + list(OTHER_CONSUMERS.values()))


def test_the_two_lists_do_not_excuse_each_others_names():
    overlap = set(HOST_RUNTIME_KNOBS) & set(OTHER_CONSUMERS)
    assert not overlap, f"a name cannot be both 'read but unprovided' and 'provided but unread': {overlap}"


def test_the_example_file_and_the_settings_defaults_agree_where_both_name_a_knob():
    """Documenting a tunable is only harmless if the documented value is the default value."""
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = EXAMPLE_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    source = (ROOT / "services/api/app/settings.py").read_text(encoding="utf-8")
    defaults = dict(re.findall(r'\w+: \w+ = [^\n]*os\.getenv\("([A-Z][A-Z0-9_]*)", "([^"]*)"\)', source))
    # Secrets are exempt on purpose, and are checked elsewhere: for them the code default is either
    # empty or a known-bad literal and .env.example is meant to differ (test_secret_defaults.py
    # asserts that every example value is on the refusal list). This test is about the boring
    # tunables, where "documented" and "equal to the default" are the same claim.
    exempt = {"JWT_SECRET", "MEDIA_SIGNING_SECRET", "PROVIDER_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET",
              "MFA_ENCRYPTION_KEY", "DEEPSEEK_API_KEY"}
    drift = {name: (values[name], defaults[name]) for name in defaults
             if name in values and name not in exempt and values[name] != defaults[name]}
    assert not drift, f".env.example ships a value that differs from the code default: {drift}"
