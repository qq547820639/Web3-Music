"""The release-record cell table gets executed on every ladder run, so a cell cannot rot unseen again.

Two scripts split the stamping job: `scripts/stamp_release_faces.py` reads one certification run's
`SUMMARY.txt` plus its per-step logs and prints the figures, and `scripts/release_face_cells.py` holds the
table -- (face file, marker line, regex, template) -- that puts those figures into the six faces
(`docs/RELEASE_CHECKLIST.md`, `docs/TEST_REPORT.md`, `docs/FINAL_RELEASE_STATUS.md`,
`docs/CHANGELOG_COST500.md`, `docs/CODE_WALKTHROUGH.md`, `docs/E2E_ACCEPTANCE_RUNBOOK.md`).

Why this file exists: the table was written for a whole round *without an executor*, and nothing noticed
that three of its cells pinned the short commit as `([0-9a-f]7)` -- one hex digit plus a literal `7`, which
matches almost no sha. A pattern nobody runs cannot fail. acceptance-20260928T054641Z is the run that made
the table get an executor (`--check` / `--apply` / `--self-test`); this file is what keeps that executor
armed, resolving every cell against the real prose each time the unit ladder runs -- 53 cells across 6
faces as measured on 2026-09-28.

The census test below is the green one. The rest are the must-fire arms: a planted duplicate and a removed
phrase have to be *refused* rather than stamped, and a refusal has to leave the file byte-identical,
because the module's own rule is that a half-stamped round is worse than a refused one.
"""
import contextlib
import importlib.util
import json
import pathlib
import re
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The public surface the resident tests drive. A missing name here means the table is back to being a
# table without an executor -- the exact state in which `([0-9a-f]7)` survived a full round.
EXPECTED_API = ("CELLS", "DERIVED", "apply", "compiled_cells", "derive", "required_keys", "resolve",
                "self_test")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RFC = load_module("release_face_cells_under_test", ROOT / "scripts/release_face_cells.py")
READER = load_module("stamp_release_faces_under_test", ROOT / "scripts/stamp_release_faces.py")
GUARD = load_module("release_record_guard_under_test", ROOT / "tests/unit/test_release_record_consistency.py")

# One plausible literal per template key, in the shape the faces actually print. Values only have to render
# here -- every pattern is matched against the prose already on disk -- but a wrong shape would put a
# nonsense reading into a release record the moment someone reuses this table to write.
SHAPE = {
    "stamp": "20260928T054641Z",
    "short": "0123456",
    "rows": "22",
    "pass": "21",
    "skipped": "1",
    "started": "2026-09-28T05:46:41Z",
    "ended": "2026-09-28T06:02:09Z",
    "run_date": "2026-09-28",
    "host_load": "1.23 4.56 7.89",
    "cpus": "4",
    "disk": "8442608",
    "fresh": "1",
    "unit_passed": "425",
    "unit_files": "42",
    "tracked": "236",
    "listed": "236",
    "routes": "100",
    "matrix_writes": "54",
    "green_count": "17",
    "green_list_cn": "`0123456`, `abcdef1`, `1f19952`",
    "green_list_en": "`0123456`, `abcdef1`, `1f19952`",
    "green_list_arrow": "`0123456`→`abcdef1`→`1f19952`",
    "green_rows": "15/16/21",
    "narrowest_rows": "15",
    "widest_rows": "21",
    "repeat_width": "20",
    "repeat_times": "7",
    "red_count": "21",
    "red_days_cn": "26 年 09 月 25 日 7 份、26 年 09 月 26 日 8 份",
    "red_days_en": "26-09-25 = 7, 26-09-26 = 8",
    "mfa": "56/56",
    "mfa_step": "12",
    # Shapes for the browser leg's line/event axes, taken off `report.json` as it is written today. The
    # line counts are deduplicated, the event counts are not, and the whole point of splitting them into
    # cells is that the prose can no longer use one as the other.
    "console_lines": "24",
    "console_network_lines": "24",
    "console_app_lines": "0",
    "console_status_breakdown": "401 记 16 行 / 403 记 6 行 / 500 记 2 行",
    "console_labels": "401×8/403×3/500×1",
    "console_error_events": "48",
    "refusal_events": "50",
    "refusal_lines": "26",
    "refusal_endpoints": "9",
    "refusal_403_events": "18",
    "refusal_top": "GET /api/auth/me -> 401 共 28 次",
    "server_4xx_events": "48",
    "server_endpoints": "8",
    "server_request_lines": "503",
    "server_401_events": "30",
    "server_403_events": "18",
    "server_500_events": "0",
    "server_403_endpoints": "`GET /api/ledger` 2、`POST /api/account/erasure` 2",
    "browser_commit_short": "0123456",
    "restore_assets": "2",
    "browser_report_dir": "browser-a11y-20260928T085606Z",
    "browser_boot_clicks": "2",
    "regression_runs": "23",
    "generic_runs": "18",
    "browser_runs": "21",
    "hold_runs": "4",
    "media_scan": "14/14",
    "media_scan_step": "14",
}


def values_for(keys=None):
    """A synthetic reading for every name the table asks for, so the census never stops on an unfilled key."""
    wanted = RFC.required_keys() if keys is None else keys
    return {key: SHAPE.get(key, "42") for key in sorted(wanted)}


def names_pattern(problem, pattern):
    """Whether a refusal points at this pattern, tolerating the engine quoting it through `repr`."""
    source = pattern.pattern
    return source in problem or source.replace("\\", "\\\\") in problem


def pick_cell():
    """The first cell the real faces hand one marker line and one match to, with its line and match.

    Nothing is hardcoded from the prose: the anchor, the marker and the span all come off the disk, so a
    face that rewords its sentence moves the fixture instead of breaking the arm. The matched span must not
    contain the marker, otherwise deleting it would test the marker arm rather than the pattern arm.
    """
    for rel, marker, pattern, template in RFC.compiled_cells(values_for()):
        path = ROOT / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines) if marker in line]
        if len(hits) != 1:
            continue
        found = list(pattern.finditer(lines[hits[0]]))
        if len(found) != 1 or marker in found[0].group(0):
            continue
        return {"rel": rel, "marker": marker, "pattern": pattern, "template": template, "lines": lines,
                "index": hits[0], "line": lines[hits[0]], "match": found[0]}
    raise AssertionError("no cell in the table resolves once against a real face, so no fault can be planted "
                         "here -- the whole census is the fault")


@contextlib.contextmanager
def plant(cell, new_line):
    """One real face, rewritten with its marker line replaced, into a temp tree that nothing else lives in.

    Yields (root, bytes-on-disk, single-cell table). Only this cell's face is copied, so any refusal the
    engine reports comes from the planted fault and not from a face that was simply absent.
    """
    lines = list(cell["lines"])
    lines[cell["index"]] = new_line
    values = values_for()
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / cell["rel"]).parent.mkdir(parents=True, exist_ok=True)
        (root / cell["rel"]).write_text("".join(lines), encoding="utf-8")
        cells = [(cell["rel"], cell["marker"], cell["pattern"], cell["template"])]
        yield root, (root / cell["rel"]).read_bytes(), cells, values


def test_the_engine_self_test_passes(capsys):
    """The engine's own eight arms, hung on the ladder instead of only on a stamping day.

    `--self-test` is how the executor proved itself when it landed; that proof used to exist only in a
    terminal someone ran once. This is the cheapest possible hook for it: if an arm goes red, the census
    below is not trustworthy either.
    """
    absent = sorted(name for name in EXPECTED_API if not hasattr(RFC, name))
    assert not absent, (f"scripts/release_face_cells.py no longer exposes {absent}: the cell table is back to "
                        "being a table nobody executes, which is how `([0-9a-f]7)` survived a whole round")
    code = RFC.self_test()
    printed = capsys.readouterr().out
    assert code == 0, f"the engine refused its own fixture arms (exit {code}); the arm report was:\n{printed}"
    census = re.search(r"self-test: (\d+) arms, failures: (.*)", printed)
    assert census, (f"self_test() returned {code} without printing how many arms it ran, so an arm that stops "
                    f"running would look like a pass: {printed!r}")
    assert int(census.group(1)) >= 8, (f"the engine now claims only {census.group(1)} arms; the accepted set "
                                       "was 8, and an arm that silently disappears is invisible to every "
                                       "other test in this file")
    assert census.group(2).strip() == "none", f"the arm census reports its own failures: {census.group(2)!r}"


def test_every_cell_resolves_against_the_real_faces():
    """The resident census: every cell in the table must match its face exactly once, right now.

    This is the guard the `([0-9a-f]7)` cells would have failed the day they were written -- a pattern that
    matches zero times is a cell that silently keeps last round's reading. Faces are read from disk, values
    are synthetic, and `resolve` writes nothing, so the ladder runs the executor without stamping.
    """
    values = values_for()
    cells = RFC.compiled_cells(values)
    problems, texts, unchanged = RFC.resolve(cells, ROOT, values)
    faces = sorted({rel for rel, _marker, _pattern, _template in RFC.CELLS})
    unshaped = sorted(set(values) - set(SHAPE))
    census = (f"cells {len(cells)} across {len(texts)} faces ({sorted(texts)}), unchanged {unchanged}, "
              f"keys with no explicit shape {unshaped}")
    assert not problems, (f"a cell no longer resolves against the prose it is supposed to restamp, so the "
                          f"next stamping round either aborts or keeps last round's figure: {problems} "
                          f"[{census}]")
    assert sorted(texts) == faces, (f"the census opened {sorted(texts)} but the table names {faces}: a face "
                                    f"the executor never touches reads as green forever [{census}]")


def test_a_cell_that_matches_twice_is_refused():
    """A planted duplicate match has to be refused, and the refusal must not move a byte.

    Opening on two matches is the failure mode nobody would see: the engine would substitute the first span
    and strand the second one carrying an older round's reading inside the same sentence.
    """
    cell = pick_cell()
    span = cell["match"].group(0)
    doubled = cell["line"][:cell["match"].end()] + span + cell["line"][cell["match"].end():]
    with plant(cell, doubled) as (root, on_disk, cells, values):
        marked = [line for line in (root / cell["rel"]).read_text(encoding="utf-8").splitlines()
                  if cell["marker"] in line]
        assert len(marked) == 1, (f"the plant duplicated the marker line instead of the match: {cell['rel']} "
                                  f"now selects {len(marked)} lines, so this proved the wrong arm")
        problems, _texts, _unchanged = RFC.resolve(cells, root, values)
        assert problems, (f"the census could not see the planted duplicate of {span!r} in {cell['rel']}: a "
                          "cell matching twice would stamp one span and leave the other on last round's figure")
        assert all(cell["marker"] not in problem for problem in problems), (
            f"the refusal went to the marker arm, not the duplicate-match arm: {problems}")
        assert any(names_pattern(problem, cell["pattern"]) for problem in problems), (
            f"the refusal did not name the pattern it refused: {cell['pattern'].pattern!r} in {problems}")
        result = RFC.apply(cells, root, values)
        assert result["ok"] is False, f"apply() stamped a face holding the same figure twice: {result}"
        assert result["written"] == [], f"a refused round must write nothing, yet it wrote: {result['written']}"
        assert (root / cell["rel"]).read_bytes() == on_disk, (f"{cell['rel']} changed on disk even though the "
                                                              "round was refused -- that is a half-stamped face")


def test_a_cell_whose_pattern_no_longer_matches_is_refused():
    """The other half of the census: a phrase that is gone has to stop the round, not pass silently.

    This is the `([0-9a-f]7)` shape exactly -- a pattern matching nothing -- produced here on purpose by
    cutting the matched span out of one real face line.
    """
    cell = pick_cell()
    stripped = cell["line"][:cell["match"].start()] + cell["line"][cell["match"].end():]
    with plant(cell, stripped) as (root, on_disk, cells, values):
        problems, _texts, _unchanged = RFC.resolve(cells, root, values)
        assert problems, (f"{cell['pattern'].pattern!r} no longer appears anywhere in {cell['rel']} and the "
                          "census said nothing -- that is exactly how a stale figure survives a stamping round")
        assert all(cell["marker"] not in problem for problem in problems), (
            f"the marker line went away with the phrase, so this proved the wrong arm: {problems}")
        assert any(names_pattern(problem, cell["pattern"]) for problem in problems), (
            f"the refusal did not name the pattern it refused: {cell['pattern'].pattern!r} in {problems}")
        result = RFC.apply(cells, root, values)
        assert result["ok"] is False and result["written"] == [], (
            f"apply() accepted a face whose cell had nothing left to substitute into: {result}")
        assert (root / cell["rel"]).read_bytes() == on_disk, (
            f"{cell['rel']} was rewritten after a refusal to stamp it")


def test_the_table_asks_for_no_value_nobody_computes():
    """Every key a template interpolates has to be produced by the reader or by `derive`.

    A key in neither place can never be filled: `compiled_cells` would refuse the round forever, and until
    this test the only way to find that out was to try to stamp the record.
    """
    try:
        tracked = list(READER.runs())
        read = set(READER.figures(tracked[0]))
    except (SystemExit, IndexError, OSError) as exc:
        pytest.skip(f"the reader has no tracked evidence to name its figure keys against: {exc}")
    orphans = sorted(RFC.required_keys() - read - set(RFC.DERIVED))
    assert not orphans, (f"{orphans} are interpolated by a cell, yet neither stamp_release_faces.figures() "
                         f"(which produces {sorted(read)}) nor release_face_cells.DERIVED "
                         f"({sorted(RFC.DERIVED)}) ever computes them, so no run can fill them")


# The other direction: a reading the reader publishes that no face quotes. Every name here is a claim about
# why it is not a hole -- either it is an input the reader uses to build a quoted figure, or a second spelling
# of a quoted reading, or a number the faces state only inside a dated round section. A new published key that
# fits none of those has to be given a cell (or a dated sentence), which is what keeps "the faces quote what
# the run measured" from degrading back into "someone copied a terminal once".
INTERNAL_FIGURES = {
    "commit": "the full sha, used to build `short` and to pair the browser leg; the faces quote the short form",
    "browser_commit": "the leg's sha, used to pair it with the run; the faces quote `browser_commit_short`",
    "fails": "the reader's own green/red decision over the SUMMARY rows",
    "fresh": "the header flag that makes the fresh-database sentence true or not",
    "rows_by_name": "the row ordinal map behind every `第 N 步` figure",
}
DUPLICATE_FIGURES = {
    "erasure_checks_passed": "erasure", "erasure_checks_total": "erasure",
    "member_checks_passed": "member", "member_checks_total": "member",
    "mfa_checks_passed": "mfa", "mfa_checks_total": "mfa",
    "media_scan_checks_passed": "media_scan", "media_scan_checks_total": "media_scan",
    "report_checks_passed": "report", "report_checks_total": "report",
    "regression_ms": "regression_p50_s", "generic_ms": "generic_p50_s",
    "generic_jobs": "generic",
    "run_unit_passed": "unit_passed",
    "fidelity_assets": "restore", "fidelity_workspaces": "restore",
    "lease_jobs": "lease", "lease_workers": "lease", "lease_credits_settled": "lease",
    "browser_violations": "browser_violations_phrase",
}
DATED_ONLY_FIGURES = {
    "browser_mobile_fit": "stated inside dated round sections of FINAL_RELEASE_STATUS, not in an authority sentence",
    "browser_uncaught": "same; the current sentence says '无未捕获异常' only when the list is empty",
    "console_error_events": "the console axis is quoted by `console_lines`/`console_labels`/`refusal_events`",
    # The legal-hold and market-reconciliation counts are quoted only in the dated sections (the item that
    # built each drill, and the round that ran it). Their bullets on the current-authority face do not state a
    # count, so there is no sentence here to own -- and inventing one would be the record quoting itself.
    "hold": "no current-authority sentence states the hold-drill count; the dated items 30/31 do",
    "hold_checks_passed": "hold", "hold_checks_total": "hold", "hold_step": "hold",
    "reconcile": "no current-authority sentence states the reconciliation count; dated item 29 does",
    "reconcile_checks_passed": "reconcile", "reconcile_checks_total": "reconcile",
}
AWAITING_ARCHIVE = {
    "census_selftest_arms": "produced only by a run whose step 1 already ran the cross-check self-test, so "
                            "every earlier archive entry reads NOT-FOUND and a cell would refuse every round",
}


def test_every_published_reading_is_quoted_or_accounted_for():
    """No live reading may be published-and-ignored without one of the four stated reasons."""
    try:
        run = max((r for r in READER.runs() if r["fails"] == 0), key=lambda r: r["stamp"])
        published = set(READER.figures(run))
    except (SystemExit, IndexError, OSError) as exc:
        pytest.skip(f"no all-green tracked run to publish figures: {exc}")
    quoted = RFC.required_keys()
    accounted = set(INTERNAL_FIGURES) | set(DUPLICATE_FIGURES) | set(DATED_ONLY_FIGURES) | set(AWAITING_ARCHIVE)
    unexplained = sorted(published - quoted - accounted)
    assert not unexplained, f"{unexplained} are published by the reader and quoted by no cell, with no reason listed"
    # Constructed boundary: a reading that belongs to none of the four buckets has to surface. Without this,
    # an empty `unexplained` could mean "everything is owned" or "the sets swallowed the comparison".
    probe = published | {"a_reading_nobody_classified"}
    assert "a_reading_nobody_classified" in sorted(probe - quoted - accounted)
    # The classifications must stay true: a duplicate's carrier has to be a cell's own key, and an
    # "accounted for" name that stops being published is a stale entry the table should drop.
    broken = sorted(f"{name}->{carrier}" for name, carrier in DUPLICATE_FIGURES.items()
                    if carrier not in quoted)
    assert not broken, f"{broken} claim a carrier no cell interpolates"
    stale = sorted(name for name in accounted if name not in published)
    assert not stale, f"{stale} are classified but the reader no longer publishes them; delete the entry"
    assert len(published & quoted) >= 40, (f"only {len(published & quoted)} published readings are cell-owned; "
                                           "the record has stopped quoting the run it certifies")


def test_a_self_test_never_prints_a_producer_line():
    """`run_step` copies every log line starting `metric ` into the SUMMARY, so a demo line is a fake reading.

    This is the shape that aborted the 2026-09-28 chain and polluted the row: `metric_line --self-test` built
    one of its sample lines with `emit()`, which prints, and the copy step dutifully recorded
    `metrics static-verify a=1`. The tools that define the format must not emit it while describing it.
    """
    import subprocess
    import sys
    probe = re.compile(r"^metric [a-z_]+=")
    for script in ("scripts/metric_line.py", "scripts/server_refusal_census.py"):
        done = subprocess.run([sys.executable, script, "--self-test"], cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 0, f"{script} --self-test failed: {done.stdout[-400:]} {done.stderr[-400:]}"
        printed = [line for line in done.stdout.splitlines() if probe.match(line)]
        assert not printed, f"{script} prints a producer-shaped line into the step log: {printed}"
    # The probe has to be able to see the offence, or the two assertions above only prove the tools are
    # quiet today. `emit()` is the producer's own call, so it is the constructed boundary.
    fired = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts');"
                              "import metric_line; metric_line.emit(a=1)"],
                           cwd=ROOT, capture_output=True, text=True)
    assert probe.match(fired.stdout.strip()), fired.stdout
    # And the chain's own copy rule must survive a step that states nothing: under `set -euo pipefail` a
    # grep with no match returns 1, which is how the pipeline killed the run after row 2.
    launcher = (ROOT / "scripts/acceptance-all.sh").read_text(encoding="utf-8").splitlines()
    at = [i for i, line in enumerate(launcher) if "producer_line#metric" in line]
    assert len(at) == 1, f"the metric copy is not the single block this test reads: {at}"
    assert launcher[at[0] + 1].strip() == "done || true", \
        f"the copy pipeline lost its pipefail shield; the line after the copy reads {launcher[at[0] + 1]!r}"


def test_the_violations_phrase_names_the_bands_when_they_are_not_zero():
    """The face's all-zero sentence is a stamp, so the stamp must be able to say otherwise."""
    phrase = READER.violations_phrase
    assert phrase({}) == "critical / serious / moderate 三档全零"
    assert phrase({"critical": 2, "minor": 1}) == "critical 2、minor 1"
    assert phrase({"critical": 0, "serious": 0}) == "critical / serious / moderate 三档全零"
    assert phrase("NOT-FOUND") == "NOT-FOUND"
    assert phrase({}) != phrase({"moderate": 1}), "a leg that found something would read as a clean one"


def test_a_cell_reading_nobody_handed_it_stops_before_the_census():
    """The values gate: drop one reading and the table refuses to compile rather than rendering a hole.

    The incident this stands in for is the engine docstring's own rule -- a `?` in a release record is a
    fabricated reading -- so the refusal has to happen before any face is even opened.
    """
    whole = values_for()
    cells = RFC.compiled_cells(whole)
    assert len(cells) == len(RFC.CELLS), (f"the complete value set compiled only {len(cells)} of "
                                          f"{len(RFC.CELLS)} cells, so the census above is not the whole table")
    hole = sorted(whole)[0]
    with pytest.raises(SystemExit) as caught:
        RFC.compiled_cells({key: value for key, value in whole.items() if key != hole})
    message = " ".join(str(arg) for arg in caught.value.args)
    assert hole in message, (f"removing {hole!r} aborted with a message that does not name it: {message!r} -- "
                             "a refusal that does not say which reading is missing cannot be acted on")


def test_the_derived_agrees_with_the_gate():
    """`derive` must report the same census the release-record gate reports for the same certified run.

    Both numbers land in prose a reader takes as the size of the reproducible set, and the engine imports
    the gate instead of copying its rule precisely so that this agreement is checkable. Counts are compared
    as data, never as rendered strings, so the test does not re-implement the formatting.
    """
    tracked = list(READER.runs())
    greens = [run for run in tracked if run["fails"] == 0]
    if not greens:
        pytest.skip("the tracked archive holds no all-green run for derive() to certify")
    chosen = max(greens, key=lambda run: run["stamp"])
    derived = RFC.derive(ROOT, READER.figures(chosen))
    archived = GUARD.archived_runs()
    gate_greens, gate_reds = GUARD.split(archived, chosen["stamp"])
    stamp = f"acceptance-{chosen['stamp']}"
    assert set(RFC.DERIVED) <= set(derived), (f"derive() never computed "
                                              f"{sorted(set(RFC.DERIVED) - set(derived))} even though the "
                                              f"table lists them as derived: {sorted(derived)}")
    assert derived["green_count"] == len(gate_greens), (
        f"derive() says {derived['green_count']} prior greens for {stamp}, the gate's own split says "
        f"{len(gate_greens)} -- the stamped figure and the judged figure would be two different claims")
    assert derived["red_count"] == len(gate_reds), (
        f"derive() says {derived['red_count']} judged reds for {stamp}, the gate's split says "
        f"{len(gate_reds)}")
    recorded = next(run["commit"] for run in archived if run["stamp"] == chosen["stamp"])
    assert derived["short"] == recorded, (f"derive() would stamp commit {derived['short']!r} for {stamp}, "
                                          f"whose SUMMARY records {recorded!r} -- this is the cell that once "
                                          "pinned the sha as `([0-9a-f]7)`")
    listed = [part for part in derived["green_list_cn"].split(",") if part.strip()]
    assert len(listed) == len(gate_greens), (f"the commit list derive() would stamp enumerates {len(listed)} "
                                             f"runs while the gate counts {len(gate_greens)}")
    per_day = [int(count) for count in re.findall(r"= (\d+)", derived["red_days_en"])]
    assert sum(per_day) == len(gate_reds), (f"the per-day split {per_day} adds up to {sum(per_day)} judged "
                                            f"reds, the gate counts {len(gate_reds)}")


def write_report(root, stamp, commit, generated_at, **extra):
    """One `browser-a11y-<stamp>/report.json` inside a throwaway evidence tree."""
    directory = root / f"browser-a11y-{stamp}"
    directory.mkdir(parents=True, exist_ok=True)
    body = {"generated_at": generated_at, "git_commit": commit, "views_scanned": 118, "axe_scans": 92}
    body.update(extra)
    (directory / "report.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return directory / "report.json"


def build_pairing_tree(root):
    """A certified run plus three a11y legs: the pair, a same-day newer one on another tree, a late one.

    `20260928` really did hold four chains and three a11y legs, which is what made the old
    "newest report of the day" rule mis-pair; the fixture keeps that shape so the arm tests the rule and
    not a toy case.
    """
    run_dir = root / "release-evidence" / "acceptance-20260928T084412Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "SUMMARY.txt").write_text(
        "started_at=2026-09-28T08:44:12Z\ngit_commit=" + ("a" * 40) + "\n"
        "STEP | RESULT | STARTED_AT | FINISHED_AT\n"
        "erasure-drill | PASS | 2026-09-28T08:52:00Z | 2026-09-28T08:54:00Z\n"
        "browser-a11y | PASS | 2026-09-28T08:56:01Z | 2026-09-28T09:00:20Z\n"
        "capacity-gate-500 | SKIPPED (CAPACITY=1 才执行) | 2026-09-28T09:00:20Z | 2026-09-28T09:00:20Z\n",
        encoding="utf-8")
    evidence = root / "release-evidence"
    own = write_report(evidence, "20260928T085606Z", "a" * 40, "2026-09-28T09:00:19Z")
    write_report(evidence, "20260928T093000Z", "b" * 40, "2026-09-28T09:44:00Z")
    write_report(evidence, "20260928T074500Z", "a" * 40, "2026-09-28T07:52:00Z")
    run = {"stamp": "20260928T084412Z", "commit": "a" * 40}
    return run, own, evidence


def test_the_browser_report_is_paired_by_commit_and_window(capsys):
    """The reader must find the a11y leg that belongs to the certified run, not the newest one that day.

    Directly reproduced from the defect this round fixed: with four chains on 2026-09-28 the date glob
    handed a certified run another run's report, so the faces quoted a hidden-element census and a
    `git_commit` from a tree the run never tested. The must-fire half is the pair being deleted: with only
    a newer same-day report left, the answer has to be "no report", not "the closest one".
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run, own, evidence = build_pairing_tree(root)
        original = READER.EVIDENCE
        READER.EVIDENCE = evidence
        try:
            chosen = READER.browser_pair(run)
            assert chosen == own, (f"the pairing did not pick the leg inside the run's own window and on its "
                                   f"own commit -- it read {chosen}, which is how a certified run ends up "
                                   f"quoting another tree's figures")
            own.unlink()
            assert READER.browser_pair(run) is None, ("a same-day report from a different commit was paired "
                                                     "to a run that never produced it")
            figures = READER.figures({**run, "rows": 3, "pass": 2, "skipped": 1, "fails": 0,
                                      "started": "2026-09-28T08:44:12Z", "ended": "2026-09-28T09:00:20Z",
                                      "host_load": "1.0 1.0 1.0", "cpus": "4", "disk": "1", "fresh": "1",
                                      "dir": run["stamp"]})
        finally:
            READER.EVIDENCE = original
        assert figures["browser_report"] == "MISSING", figures["browser_report"]
        assert figures["console_lines"] == "NOT-FOUND", (f"an unpaired run must read NOT-FOUND, got "
                                                         f"{figures['console_lines']!r}")
        capsys.readouterr()


def test_a_run_whose_a11y_leg_tested_another_tree_cannot_be_stamped():
    """`derive` refuses rather than stamping 'the same commit' onto a run that is not the same commit.

    The status file's sentence makes that claim, so the claim is a premise of the stamping round. Without
    the refusal the faces would carry a sentence that is false exactly where it matters -- which tree the
    browser leg actually exercised.
    """
    tracked = list(READER.runs())
    greens = [run for run in tracked if run["fails"] == 0]
    if not greens:
        pytest.skip("the tracked archive holds no all-green run to falsify against")
    chosen = max(greens, key=lambda run: run["stamp"])
    figures = READER.figures(chosen)
    if figures.get("browser_commit") in (None, "NOT-FOUND", "MISSING"):
        pytest.skip(f"{chosen['stamp']} has no paired browser report, so there is no commit claim to test")
    assert figures["browser_commit"] == chosen["commit"], (
        f"the certified run {chosen['stamp']} records commit {chosen['commit'][:7]} while its paired a11y "
        f"leg ({figures['browser_report']}) carries {figures['browser_commit'][:7]}: the faces would state "
        "'the same commit' about two different trees")
    tampered = dict(figures)
    tampered["browser_commit"] = "f" * 40
    with pytest.raises(SystemExit) as caught:
        RFC.derive(ROOT, tampered)
    message = " ".join(str(arg) for arg in caught.value.args)
    assert "fffffff" in message and chosen["commit"][:7] in message, (
        f"the refusal did not name both commits so a reader could not act on it: {message!r}")


def test_a_step_reading_round_trips_from_producer_to_reader(tmp_path):
    """The three-party handoff of `metric k=v`: producer prints it, the chain prefixes it, reader parses it.

    This is where the format could quietly break -- the producer's line carries no step name (the chain adds
    one), so a parser written against the log shape would find nothing in SUMMARY, and vice versa. Both
    halves are exercised through the real functions, and a run whose steps emitted nothing must read as
    absent rather than as a zero the faces could stamp.
    """
    metrics = load_module("metric_line_under_test", ROOT / "scripts/metric_line.py")
    line = metrics.emit(p50_s="4.095", p95_s="6.43", note="2 workspaces, 2 assets")
    assert line.startswith("metric ") and not line.startswith("metrics "), line
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text("STEP | RESULT | STARTED_AT | FINISHED_AT\n"
                       "provider-regression-100 | PASS | 2026-01-01T00:00:00Z | 2026-01-01T00:01:00Z\n"
                       f"metrics provider-regression-100 {line[len('metric '):]}\n", encoding="utf-8")
    table = READER.step_metrics(tmp_path)
    assert table == {"provider-regression-100": {"note": "2 workspaces, 2 assets",
                                                 "p50_s": "4.095", "p95_s": "6.43"}}, table
    # A step may state several readings on separate lines (row 1 states the ladder count and the census's own
    # arm count), and the chain copies them in order rather than keeping only the last. The reader therefore
    # merges per step: assigning the line wholesale would let the later line erase the earlier one, and the
    # erased figure would read as "this run never produced it".
    summary.write_text("STEP | RESULT | STARTED_AT | FINISHED_AT\n"
                       "static-verify | PASS | 2026-01-01T00:00:00Z | 2026-01-01T00:00:30Z\n"
                       "metrics static-verify refusal_census_selftest_arms=11\n"
                       "metrics static-verify unit_passed=453\n", encoding="utf-8")
    merged = READER.step_metrics(tmp_path)
    assert merged == {"static-verify": {"refusal_census_selftest_arms": "11", "unit_passed": "453"}}, merged
    empty = tmp_path / "older-run"
    empty.mkdir()
    (empty / "SUMMARY.txt").write_text("STEP | RESULT | STARTED_AT | FINISHED_AT\n"
                                       "provider-regression-100 | PASS | a | b\n", encoding="utf-8")
    assert READER.step_metrics(empty) == {}, "a run without readings produced something anyway"
    # The mapping's step names must be steps the pipeline really writes, spelled exactly -- a typo here
    # would read NOT-FOUND forever and the cells that consume it would refuse every round for a wrong reason.
    newest = max((r for r in READER.runs() if r["fails"] == 0), key=lambda r: r["stamp"])
    written = set(READER.rows_by_name(ROOT / "release-evidence" / f"acceptance-{newest['stamp']}"))
    unknown = sorted({step for step, _key in READER.METRIC_FIGURES} - written)
    assert not unknown, f"METRIC_FIGURES names steps the certified run never wrote: {unknown}"


def test_two_qualifying_legs_are_refused_not_guessed():
    """An ambiguous pairing now refuses instead of taking the newest.

    The review's unreproduced suspicion, now built: two legs on the certified run's own commit with
    `generated_at` inside that run's window used to be settled by `candidates[-1]`, so a face could cite one
    leg while the stamped figures came from the other. Both must be named in the refusal, and removing one
    must make the remaining leg resolve -- otherwise this is a blanket failure, not a tie-break refusal.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run, own, evidence = build_pairing_tree(root)
        twin = write_report(evidence, "20260928T085800Z", "a" * 40, "2026-09-28T09:00:20Z")
        original = READER.EVIDENCE
        READER.EVIDENCE = evidence
        try:
            with pytest.raises(SystemExit) as raised:
                READER.browser_pair(run)
            message = str(raised.value)
            assert "20260928T085800Z" in message and "20260928T085606Z" in message, message
            twin.unlink()
            assert READER.browser_pair(run) == own, "after the tie is gone the pairing must still resolve"
        finally:
            READER.EVIDENCE = original
