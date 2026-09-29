"""Own the live figures in the reference docs that nothing used to recompute.

A 2026-09-28 read-through of the non-release-record docs found sentences that had simply stopped being
true: `docs/ARCHITECTURE.md` still described "12 个 Compose 服务" and named `Acceptance` among them while
`docker-compose.yml` has 15 services of which 12 start by default and Acceptance is profile-gated;
`docs/IMPLEMENTATION.md` said CI runs "三条链" while `.github/workflows/ci.yml` defines five jobs; the cost
note and the rights policy cited `file:line` pointers that had drifted 5-31 lines away from the code they
described. Each of those numbers is produced elsewhere and copied into prose, so the only thing that can
keep them honest is a reader that recomputes them from the same objects the product reads.

Scope, stated so the gate is not mistaken for a census: it covers the figures and the unambiguous
`path/to/file.py:NNN` citations of the seven docs listed in `AUDITED`, and the `path`（N 行）length claims of
the docs listed in `LINE_SCANNED`. Bare-name citations (`main.py:1018`) and migration shorthands
(`001:461-468`) are outside the general scanner because a basename can resolve to several files in this tree;
the pointers this round corrected among them are pinned by name in `POINTER_ROSTER` instead.
"""
import json
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

AUDITED = (
    "docs/ARCHITECTURE.md", "docs/IMPLEMENTATION.md", "docs/COST_OPTIMIZED_500_CONCURRENCY.md",
    "docs/RIGHTS_POLICY.md", "docs/ITERATION_PLAN_PHASE2.md", "docs/ITERATION_CHANGES.md",
    "docs/ITERATION_CHANGES_PHASE3.md",
)
COMPOSE = "docker-compose.yml"
CI = ".github/workflows/ci.yml"
CONTRACT = "shared/contracts/openapi-v13.json"
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Files whose prose states a source file's length. Each claim was hand-counted once and never re-counted:
# six of the seven in `docs/CODE_WALKTHROUGH.md` were wrong today (465 against 1295 lines for the API entry,
# 55 against 142 for settings, 408 against 504 for the worker), and two of the short names it used resolve to
# different files than the prose meant (`quality.py` lives under `app/domain/`, `provider.py` under
# `services/worker/`). Both the number and the path are recomputed here.
LINE_SCANNED = ("docs/CODE_WALKTHROUGH.md", "docs/README_FOR_DEVELOPERS.md")
LINE_CLAIM = re.compile(r"`([^`]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z]{2,5})`（(\d+) 行）")

# The same shape for resident-test case counts: prose that names a test file and states how many cases it
# holds. Three existed across `docs/*.md`, and one was four cases short -- `test_environment_contract.py`
# had grown from 9 to 13 while the gate item still said 9, in the present tense ("把这件事变成常驻门禁"), so a
# reader would size the guard by a number that no longer describes it.
CASE_CLAIM = re.compile(r"`(tests/unit/[a-z_0-9]+\.py)`（(\d+) 条")

def case_claim_problems(root, paths):
    problems = []
    for doc in paths:
        text = (root / doc).read_text(encoding="utf-8")
        for found in CASE_CLAIM.finditer(text):
            target = root / found.group(1)
            if not target.exists():
                problems.append(f"{doc} counts cases in {found.group(1)}, which is not in the tree")
                continue
            real = sum(1 for line in target.read_text(encoding="utf-8").splitlines()
                       if line.startswith("def test_"))
            if real != int(found.group(2)):
                problems.append(f"{doc} says {found.group(1)} holds {found.group(2)} cases; "
                                f"the file defines {real} `test_` functions")
    return problems


def test_the_documented_case_counts_match_the_case_files():
    docs = sorted(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md"))
    problems = case_claim_problems(ROOT, docs)
    assert not problems, "\n".join(problems)
    claims = sum(len(CASE_CLAIM.findall((ROOT / d).read_text(encoding="utf-8"))) for d in docs)
    assert claims >= 3, f"only {claims} case-count claims are left in the docs; the scanner has nothing to say"


def test_the_case_count_rule_fires_when_a_file_grows(tmp_path):
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests/unit/test_x.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    pass\n",
                                                   encoding="utf-8")
    (tmp_path / "docs").mkdir()
    honest = "门禁见 `tests/unit/test_x.py`（2 条）"
    (tmp_path / "docs/d.md").write_text(honest, encoding="utf-8")
    assert case_claim_problems(tmp_path, ("docs/d.md",)) == []
    (tmp_path / "docs/d.md").write_text("门禁见 `tests/unit/test_x.py`（9 条）", encoding="utf-8")
    red = case_claim_problems(tmp_path, ("docs/d.md",))
    assert len(red) == 1 and "defines 2" in red[0], red
    (tmp_path / "docs/d.md").write_text("门禁见 `tests/unit/test_gone.py`（1 条）", encoding="utf-8")
    missing = case_claim_problems(tmp_path, ("docs/d.md",))
    assert len(missing) == 1 and "not in the tree" in missing[0], missing

# Unambiguous citations: the path is real, so the line number is a claim about that file's content.
# Bare names (`main.py:1018`) are skipped on purpose -- this tree has several `main.py` files, and picking
# one by basename would make the gate agree with whichever file the checker happened to open.
PATH_LINE = re.compile(r"`([A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,5}):(\d+)`")


def compose_census(root=ROOT):
    services = yaml.safe_load((root / COMPOSE).read_text(encoding="utf-8"))["services"]
    default = sorted(k for k, v in services.items() if not v.get("profiles"))
    return len(services), len(default), len(services) - len(default)


def ci_job_count(root=ROOT):
    jobs = yaml.safe_load((root / CI).read_text(encoding="utf-8"))["jobs"]
    return len(jobs), sorted(jobs)


def contract_census(root=ROOT):
    contract = json.loads((root / CONTRACT).read_text(encoding="utf-8"))
    paths = contract["paths"]
    operations = sum(1 for item in paths.values() for verb in item if verb in METHODS)
    schemas = len(contract.get("components", {}).get("schemas", {}))
    return len(paths), operations, schemas


def figure_problems(text, compose, jobs, contract):
    """Every number a sentence asserts about the three computed objects, re-derived and compared."""
    problems = []
    total, default, gated = compose
    for found in re.finditer(r"\*\*(\d+)\*\* 个 Compose 服务", text):
        want = {15: total, 12: default, 3: gated}.get(int(found.group(1)))
        if want != int(found.group(1)):
            problems.append(f"'{found.group(0)}' is not a compose reading (15 total / 12 default / 3 gated)")
    for found in re.finditer(r"分([一二三四五六七八九十\d]+)条链", text):
        spelled = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        stated = spelled.get(found.group(1), None) if not found.group(1).isdigit() else int(found.group(1))
        if stated != jobs[0]:
            problems.append(f"'{found.group(0)}' against {jobs[0]} jobs in {CI}: {', '.join(jobs[1])}")
    for found in re.finditer(r"(\d+) paths / (\d+) operations", text):
        if (int(found.group(1)), int(found.group(2))) != contract[:2]:
            problems.append(f"'{found.group(0)}' against {contract[0]} paths / {contract[1]} operations in {CONTRACT}")
    # The walkthrough states the same object in Chinese and adds the schema count, and its last line about
    # `securitySchemes` still described the pre-fix contract; the sentence now carries the measured triple.
    for found in re.finditer(r"(\d+) 条路径、(\d+) 个", text):
        if (int(found.group(1)), int(found.group(2))) != (contract[0], contract[2]):
            problems.append(f"'{found.group(0)}' against {contract[0]} 条路径、{contract[2]} 个 components.schemas")
    return problems


def citation_problems(root, doc, text):
    """Citations that name a real path: the file must exist and the cited line must be inside it."""
    problems = []
    for found in PATH_LINE.finditer(text):
        if "/" not in found.group(1):
            continue
        target, line = root / found.group(1), int(found.group(2))
        if not target.exists():
            problems.append(f"{doc} cites {found.group(1)}, which is not in the tree")
            continue
        if line > len(target.read_text(encoding="utf-8", errors="replace").splitlines()):
            problems.append(f"{doc} cites {found.group(1)}:{line} but that file has fewer lines")
    return problems


# The pointers this round moved onto their target, with the text that has to sit on that line.
POINTER_ROSTER = (
    ("docs/COST_OPTIMIZED_500_CONCURRENCY.md", "services/api/app/main.py", 162, "def _login_key"),
    ("docs/COST_OPTIMIZED_500_CONCURRENCY.md", "services/gateway/nginx.conf", 43, "X-Forwarded-For"),
    ("docs/COST_OPTIMIZED_500_CONCURRENCY.md", "services/gateway/nginx.conf", 61, "X-Forwarded-For"),
    ("docs/RIGHTS_POLICY.md", "scripts/report_drill.py", 407, "DELETE FROM media_assets"),
    ("docs/RIGHTS_POLICY.md", "scripts/hold_drill.py", 494, "DELETE FROM media_assets"),
    ("docs/RIGHTS_POLICY.md", "services/api/app/domain/rights.py", 43, "def allowed"),
    ("docs/ITERATION_CHANGES_PHASE3.md", "services/admin/admin.js", 129, "showModal"),
    ("docs/ITERATION_CHANGES_PHASE3.md", "services/admin/admin.js", 166, "showModal"),
    ("docs/ITERATION_PLAN_PHASE2.md", "services/api/app/main.py", 1018, "generation_steps"),
)


def test_every_audited_doc_is_present():
    missing = [d for d in AUDITED if not (ROOT / d).exists()]
    assert not missing, f"the roster names docs that are not in the tree: {missing}"


def test_the_compose_census_the_architecture_sentence_claims():
    assert compose_census() == (15, 12, 3)


def test_the_ci_job_count_the_implementation_sentence_claims():
    count, names = ci_job_count()
    assert count == 5 and names == ["browser-a11y", "capacity-500", "commercial-flow",
                                    "compose-acceptance", "static-and-unit"]


def test_the_contract_census_the_iteration_record_claims():
    assert contract_census() == (90, 103, 45)


FIGURE_SCANNED = AUDITED + ("docs/CODE_WALKTHROUGH.md",)


def test_the_audited_docs_agree_with_the_tree_right_now():
    problems = []
    for doc in FIGURE_SCANNED:
        text = (ROOT / doc).read_text(encoding="utf-8")
        problems += [f"{doc}: {p}" for p in figure_problems(text, compose_census(), ci_job_count(),
                                                            contract_census())]
        problems += citation_problems(ROOT, doc, text)
    assert not problems, "\n".join(problems)


def roster_problems(root, roster):
    problems = []
    for doc, target, line, needle in roster:
        text = (root / doc).read_text(encoding="utf-8")
        if f"{target}:{line}" not in text and f":{line}`" not in text:
            problems.append(f"{doc} no longer cites {target}:{line}, so the roster entry is stale")
        body = (root / target).read_text(encoding="utf-8").splitlines()
        if line > len(body) or needle not in body[line - 1]:
            shown = body[line - 1].strip() if line <= len(body) else "<past the end of the file>"
            problems.append(f"{target}:{line} does not read `{needle}` -- it reads `{shown}`")
    return problems


def test_the_roster_pointers_land_where_the_docs_say():
    assert not roster_problems(ROOT, POINTER_ROSTER)


def line_count_problems(root, doc, text):
    """Every `path`（N 行） claim: the file must be in the tree and the count must be today's count."""
    problems = []
    for found in LINE_CLAIM.finditer(text):
        target = root / found.group(1)
        if not target.exists():
            problems.append(f"{doc} counts {found.group(1)}, which is not in the tree")
            continue
        real = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
        if real != int(found.group(2)):
            problems.append(f"{doc} says {found.group(1)} is {found.group(2)} lines; `wc -l` reads {real}")
    return problems


def test_the_walkthroughs_line_counts_are_todays_counts():
    problems = []
    total = 0
    for doc in LINE_SCANNED:
        text = (ROOT / doc).read_text(encoding="utf-8")
        found = LINE_CLAIM.findall(text)
        total += len(found)
        assert found, f"{doc} is scanned for line-count claims but states none; drop it from LINE_SCANNED"
        problems += line_count_problems(ROOT, doc, text)
    assert not problems, "\n".join(problems)
    assert total >= 10, f"only {total} line-count claims across {LINE_SCANNED}; the census is not what it was"


def test_the_line_count_rule_fires_on_a_stale_number(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert line_count_problems(tmp_path, "d.md", "`x/a.py`（3 行）") == []
    off = line_count_problems(tmp_path, "d.md", "`x/a.py`（408 行）")
    assert len(off) == 1 and "wc -l` reads 3" in off[0], off
    gone = line_count_problems(tmp_path, "d.md", "`x/gone.py`（1 行）")
    assert len(gone) == 1 and "not in the tree" in gone[0], gone
    # A short name is not a path: the prose must say which file it means.
    assert LINE_CLAIM.findall("`worker.py`（408 行）") == []


RUNBOOK_PORTS = "docs/E2E_ACCEPTANCE_RUNBOOK.md"
RUNBOOK_PORT_LINE = re.compile(r"^\s*((?:\d{2,5})(?:/\d{2,5})*)\s+\S.*$", re.M)
INTERPOLATED = re.compile(r"\$\{[A-Z_]+:-([^}]*)\}")


def claimed_ports(text):
    """The host ports the runbook's table states, including the `9000/9001` one-line pair."""
    out = set()
    for found in RUNBOOK_PORT_LINE.finditer(text):
        out |= set(found.group(1).split("/"))
    return out


def composed_host_ports(root=ROOT):
    """Every host port `docker compose` publishes, with `${VAR:-default}` resolved before the split.

    Resolution order is the whole point: the mapping `${GATEWAY_PORT:-8080}:80` contains a colon inside the
    braces, so splitting on ":" first yields `${GATEWAY_PORT` and the gateway and Prometheus -- two of the
    eleven published ports -- read as publishing nothing. That is how a first pass of this check accused the
    runbook of inventing 8080 and 9090.
    """
    services = yaml.safe_load((root / COMPOSE).read_text(encoding="utf-8"))["services"]
    out = set()
    for service in services.values():
        for mapping in (service.get("ports") or []):
            host = INTERPOLATED.sub(r"\1", str(mapping)).split(":")[0]
            if host.isdigit():
                out.add(host)
    return out


def port_table_problems(claimed, published):
    if not claimed:
        return ["the runbook port table is gone, so the comparison proves nothing"]
    return [f"the runbook lists host port {p}, which compose does not publish"
            for p in sorted(claimed - published, key=int)] + \
           [f"compose publishes host port {p}, which the runbook table does not list"
            for p in sorted(published - claimed, key=int)]


def test_the_runbooks_port_table_is_composes_port_table():
    problems = port_table_problems(claimed_ports((ROOT / RUNBOOK_PORTS).read_text(encoding="utf-8")),
                                   composed_host_ports())
    assert not problems, "\n".join(problems)
    # Floor: the default compose ships eleven host ports. A scanner reading fewer is reporting its own
    # blindness -- which is exactly the failure mode the interpolation bug above produced (9 of 11).
    assert len(composed_host_ports()) == 11, sorted(composed_host_ports())


def test_the_port_rule_fires_on_a_port_compose_does_not_publish():
    published = {"8080", "8000", "4173"}
    compliant = "  8080  gateway\n  8000  api\n  4173  web\n"
    assert port_table_problems(claimed_ports(compliant), published) == []
    invented = port_table_problems(claimed_ports(compliant + "  9999  ghost\n"), published)
    assert len(invented) == 1 and "9999" in invented[0], invented
    vanished = port_table_problems(claimed_ports("no table here"), published)
    assert vanished == ["the runbook port table is gone, so the comparison proves nothing"], vanished
    dropped = port_table_problems(claimed_ports("  8080  gateway\n  8000  api\n"), published)
    assert len(dropped) == 1 and "4173" in dropped[0], dropped


def test_the_gate_can_fire(tmp_path):
    """A doc that disagrees with the tree must be named, not passed.

    Constructed boundary rather than a random corpus: a sentence one number off, a citation one line past
    the end of its file, and a citation of a path that is not in the tree.
    """
    fake = tmp_path / "docs"
    fake.mkdir(parents=True)
    (tmp_path / "x").mkdir(parents=True)
    (tmp_path / "x" / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    wrong = "本地共 **16** 个 Compose 服务，分三条链，实测 91 paths / 103 operations"
    problems = figure_problems(wrong, compose_census(), ci_job_count(), contract_census())
    assert len(problems) == 3, problems
    assert citation_problems(tmp_path, "d.md", "`x/a.py:1`") == []
    past_end = citation_problems(tmp_path, "d.md", "`x/a.py:9`")
    assert len(past_end) == 1 and "fewer lines" in past_end[0], past_end
    absent = citation_problems(tmp_path, "d.md", "`x/gone.py:1`")
    assert len(absent) == 1 and "not in the tree" in absent[0], absent
    # A roster entry one line off must name both faults: the line no longer reads the needle, and the
    # document no longer cites that line. Without the compliant side the pair proves nothing.
    entry = POINTER_ROSTER[3]
    assert roster_problems(ROOT, [entry]) == []
    drifted = (entry[0], entry[1], entry[2] + 1, entry[3])
    fires = roster_problems(ROOT, [drifted])
    assert len(fires) == 2 and any("does not read" in p for p in fires) and \
        any("no longer cites" in p for p in fires), fires


def test_the_compose_census_reads_profiles_not_line_count():
    """The 12-vs-15 split has to come from `profiles`, so a profile added silently re-divides it."""
    services = yaml.safe_load((ROOT / COMPOSE).read_text(encoding="utf-8"))["services"]
    profiled = sorted(k for k, v in services.items() if v.get("profiles"))
    assert profiled == ["acceptance", "clamav", "worker-b"], profiled
