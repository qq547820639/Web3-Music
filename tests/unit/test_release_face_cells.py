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
