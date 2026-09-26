"""The signing-secret policy: what it refuses, what it permits, and how it is wired in.

No stack and no .env: everything here runs against settings built with dataclasses.replace, which is
also how the arms stay independent of the host environment.

The defect this closes is not theoretical. Until 2026-09-26 docker-compose.yml restated the same
fallback literals as app/settings.py (`JWT_SECRET: ${JWT_SECRET:-local-development-jwt-secret-...}`)
and docker-compose.production.yml overrode none of them, so a production deploy that forgot a
variable booted happily on a signing key published in a public repository. And .env.example -- the
file CI copies verbatim -- used one value, "change-me-before-sharing", for both the access-token key
and the media-URL key, so whoever could mint a media link could mint a session.
"""
import ast
import dataclasses
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

import app.settings as S  # noqa: E402

MAIN = ROOT / "services/api/app/main.py"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"
PRODUCTION = ROOT / "docker-compose.production.yml"

CLEAN = {"jwt_secret": "a-real-jwt-key-from-a-secret-manager",
         "media_signing_secret": "a-real-media-key-from-a-secret-manager",
         "provider_webhook_secret": "a-real-provider-wh-key-from-a-secret-manager",
         "payment_webhook_secret": "a-real-payment-wh-key-from-a-secret-manager"}


def configured(**overrides):
    values = dict(CLEAN)
    values.update(overrides)
    return dataclasses.replace(S.settings, demo_stack=False, **values)


# ------------------------------------------------------------- the findings ----

def test_a_clean_configuration_raises_nothing():
    assert S.secret_findings(configured()) == []


def in_file_defaults() -> dict[str, str]:
    """The literal second argument of os.getenv for each signing field, read from the source.

    Read from the source rather than from the imported settings object on purpose: an imported value
    depends on the environment the tests happen to run in, and the point of this test is the string
    that is published in the repository.
    """
    source = (ROOT / "services/api/app/settings.py").read_text(encoding="utf-8")
    found = dict((field, literal) for field, _env, literal
                 in re.findall(r'(\w+): str = os\.getenv\("([A-Z_]+)", "([^"]+)"\)', source))
    return {name: found[name] for name in S.SIGNING_SECRETS if name in found}


def test_every_in_file_default_is_a_known_insecure_literal():
    defaults = in_file_defaults()
    assert set(defaults) == set(S.SIGNING_SECRETS), f"a signing field lost its literal or the parser missed it: {defaults}"
    unlisted = {name: value for name, value in defaults.items() if value not in S.INSECURE_SECRET_VALUES}
    assert not unlisted, f"settings.py falls back to a value the policy does not refuse: {unlisted}"


def test_every_repository_literal_is_refused_and_named():
    for name, literal in in_file_defaults().items():
        findings = S.secret_findings(configured(**{name: literal}))
        assert any(name in f for f in findings), (name, findings)
        assert any("literal" in f for f in findings), findings


def test_an_unconfigured_secret_is_refused():
    findings = S.secret_findings(configured(payment_webhook_secret=""))
    assert any("payment_webhook_secret is not configured" in f for f in findings), findings


def test_one_value_signed_two_things_before_and_is_refused_again():
    shared = "the-same-string-for-two-purposes"
    findings = S.secret_findings(configured(jwt_secret=shared, media_signing_secret=shared))
    assert any("jwt_secret and media_signing_secret" in f for f in findings), findings


def test_the_control_that_proves_the_shared_value_rule_is_not_vacuous():
    assert S.secret_findings(configured()) == [], "the clean arm must differ only by value reuse"


def test_the_refusal_is_skipped_only_for_an_explicit_demo_stack_and_says_why():
    demo = dataclasses.replace(S.settings, demo_stack=True)
    assert S.secret_findings(demo), "the demo stack must still be configured on literals, or this proves nothing"
    S.deny_insecure_defaults(demo)  # must not raise
    with pytest.raises(RuntimeError) as refused:
        S.deny_insecure_defaults(configured(jwt_secret="change-me-before-sharing",
                                            media_signing_secret="change-me-before-sharing"))
    message = str(refused.value)
    assert "DEMO_STACK=true" in message, message


def test_the_demo_licence_defaults_to_off():
    """An environment that says nothing must be refused, not trusted. Checked in the source, because
    the imported object legitimately has DEMO_STACK=true when the tests run inside the demo stack."""
    source = (ROOT / "services/api/app/settings.py").read_text(encoding="utf-8")
    assert re.search(r'demo_stack: bool = boolean\("DEMO_STACK", False\)', source), source
    flipped = 'demo_stack: bool = boolean("DEMO_STACK", True)'
    assert not re.search(r'demo_stack: bool = boolean\("DEMO_STACK", False\)', flipped), \
        "the detector must be able to see the fail-open spelling"


# ------------------------------------------- the literals and the example file ----

def env_example_values() -> dict[str, str]:
    pairs = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            pairs[match.group(1)] = match.group(2)
    return pairs


def test_env_example_ships_a_distinct_demo_value_per_signing_purpose():
    values = env_example_values()
    names = {"JWT_SECRET", "MEDIA_SIGNING_SECRET", "PROVIDER_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET"}
    present = {name: values[name] for name in names if name in values}
    assert set(present) == names, f"missing from .env.example: {sorted(names - set(present))}"
    assert len(set(present.values())) == len(present), f"two purposes share a value: {present}"


def test_every_demo_value_in_the_example_file_is_on_the_refusal_list():
    """The runtime list and the file CI copies must agree, or 'forgot to edit' is silently allowed."""
    values = set(env_example_values().values())
    unlisted = {name: value for name, value in
                ((name, env_example_values()[name]) for name in
                 ("JWT_SECRET", "MEDIA_SIGNING_SECRET", "PROVIDER_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET"))
                if value and value not in S.INSECURE_SECRET_VALUES}
    assert not unlisted, f".env.example uses values the policy does not know are demo values: {unlisted}"
    assert not ({value for value in values if value.startswith("change-me")} - S.INSECURE_SECRET_VALUES)
    assert "change-me-before-sharing" in S.INSECURE_SECRET_VALUES, values


# ------------------------------------------------------------- the compose files ----

def compose_service_environment(document: dict, service: str) -> dict:
    return document["services"][service].get("environment") or {}


def load_yaml(path):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_base_compose_no_longer_supplies_a_fallback_for_any_signing_secret():
    environment = compose_service_environment(load_yaml(COMPOSE), "api")
    for name in ("JWT_SECRET", "MEDIA_SIGNING_SECRET", "PROVIDER_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET"):
        value = environment.get(name, "")
        assert name in value, f"{name} is not passed through at all: {value!r}"
        assert ":-" not in value, f"{name} still falls back to a literal: {value!r}"
        assert ":?" in value, f"{name} must fail closed with :? rather than default: {value!r}"


FALLBACK = re.compile(r'([A-Z_]+):\s*"?\$\{([^}\n]*)\}')


def fallback_literals(text: str, name: str) -> list[str]:
    """Every `${NAME:-...}` interpolation for `name` -- every place a value can be defaulted."""
    return [body for field, body in FALLBACK.findall(text) if field == name and ":-" in body]


def test_no_compose_file_falls_back_to_an_in_repo_secret_literal():
    """Checked across every overlay, not only the api service where the defect happened to live."""
    offenders = []
    for path in sorted(ROOT.glob("docker-compose*.yml")):
        text = path.read_text(encoding="utf-8")
        for name in ("JWT_SECRET", "MEDIA_SIGNING_SECRET", "PROVIDER_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET"):
            offenders += [f"{path.name}: {name}: {value}" for value in fallback_literals(text, name)]
    assert not offenders, f"a deployment could still inherit a published default: {offenders}"
    assert fallback_literals("JWT_SECRET: ${JWT_SECRET:-published-default}", "JWT_SECRET"), \
        "the sweep cannot see the shape it forbids"
    assert not fallback_literals("JWT_SECRET: ${JWT_SECRET:?set JWT_SECRET}", "JWT_SECRET"), \
        "the required form is the one being asked for, not a violation"


def test_the_production_overlay_revokes_the_demo_licence():
    assert compose_service_environment(load_yaml(COMPOSE), "api")["DEMO_STACK"] == "true"
    production = load_yaml(PRODUCTION)
    for service in ("api", "worker"):
        assert compose_service_environment(production, service).get("DEMO_STACK") == "false", service


def pinned_value(document: dict, service: str, key: str) -> str:
    return str(compose_service_environment(document, service).get(key, ""))


def test_the_licence_is_a_pinned_literal_not_an_interpolation():
    """${DEMO_STACK:-true} could be flipped by a stray .env on a deploy host; a literal cannot.

    Checked on the parsed value rather than on the file text, because the comment above the line in
    docker-compose.yml explains exactly that rejected spelling and mentions it by name.
    """
    base = pinned_value(load_yaml(COMPOSE), "api", "DEMO_STACK")
    assert base == "true", base
    assert "${" not in base, f"the demo licence is interpolated from the host: {base!r}"
    # the control: the fail-open form must be visibly different to this check
    assert "${" in '${DEMO_STACK:-true}'
    assert "${" not in "true"


# ---------------------------------------------------------------- the wiring ----

def lifespan_calls(path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return any(isinstance(call, ast.Call) and getattr(call.func, "id", "") == name
                       for call in ast.walk(node))
    return False


def test_startup_runs_the_refusal_before_touching_the_database():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
    lifespan = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
    order = [getattr(call.func, "id", "") for call in ast.walk(lifespan) if isinstance(call, ast.Call)]
    assert "deny_insecure_defaults" in order and "wait_for_db" in order
    assert order.index("deny_insecure_defaults") < order.index("wait_for_db"), order


def test_the_wiring_detector_fires_on_a_lifespan_that_forgets_it():
    forgotten = ('from contextlib import asynccontextmanager\n'
                 '@asynccontextmanager\nasync def lifespan(app):\n'
                 '    wait_for_db()\n    yield\n')
    tree = ast.parse(forgotten)
    assert not any(isinstance(call, ast.Call) and getattr(call.func, "id", "") == "deny_insecure_defaults"
                   for call in ast.walk(tree)), "a lifespan without the refusal must look different to the detector"
