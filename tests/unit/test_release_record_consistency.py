"""The release record's reproducibility figures must agree with the archived runs they describe.

docs/RELEASE_CHECKLIST.md, docs/TEST_REPORT.md and docs/CHANGELOG_COST500.md each say a version of
"this host has already produced N runs with FAIL=0 (commits ..., old to new; then 15/15/16/... rows
respectively)" plus "M judged-red runs are kept as findings (x on day A, y on day B)". Those figures
were maintained by editing prose, and prose drifts: the current text says 12 and lists 21 commits,
because an earlier patch replaced only the *tail* of the list and left the nine hashes that were
already there. A reader who trusted the number drew the wrong conclusion about how reproducible the
green is -- which is the one claim that sentence exists to support.

So the figures are checked against their source: every tracked release-evidence/acceptance-*/SUMMARY.txt
is parsed, the runs are split into green and red by their own step rows, and each document's sentence
must agree on the count, the exact commit sequence, the per-run row counts, the red total and the
per-day split. Tracked files only, because a scratch run left in the working tree by the person writing
the sentence would otherwise move the denominator (measured during this very session: three abandoned
browser runs sat under release-evidence/ until they were deleted).

Every clause is given the sample that must make it fail.
"""
import collections
import importlib.util
import json
import pathlib
import re
import yaml
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STEP_ROW = re.compile(r"^([a-z0-9-]+) \| (\w+)", re.MULTILINE)
COMMIT = re.compile(r"^git_commit=(\w{7})", re.MULTILINE)
SHORT = re.compile(r"`(\w{7})`")
FINISHED = re.compile(r"finished_at=(\S+)")


def ended_of(text):
    """When the chain said it was done -- or None when it never said it at all.

    A record with no FAIL row is not automatically a green run: `acceptance-20260928T184002Z` is the shape a
    chain leaves behind when it dies mid-flight (two rows, no failure, no `finished_at=`), and reading that
    as "all green" is how a half-run would become the authority the whole record quotes.
    """
    found = FINISHED.search(text)
    return found.group(1) if found else None


def archived_runs():
    """Every tracked acceptance SUMMARY, oldest first, each reduced to (stamp, commit, rows, fails)."""
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "release-evidence/acceptance-*/SUMMARY.txt"],
                            capture_output=True, text=True, check=True).stdout.split()
    runs = []
    for relative in sorted(listed):
        text = (ROOT / relative).read_text(encoding="utf-8", errors="replace")
        rows = STEP_ROW.findall(text)
        commit = COMMIT.search(text)
        host = re.search(r'host_load="([^"]*)"', text)
        cpus = re.search(r"docker_cpus=(\d+)", text)
        runs.append({
            "stamp": pathlib.Path(relative).parent.name.replace("acceptance-", ""),
            "commit": commit.group(1) if commit else "?",
            "started": (re.search(r"started_at=(\S+)", text) or [None, None])[1],
            "ended": ended_of(text),
            "rows": len(rows),
            "fails": sum(1 for _, result in rows if result == "FAIL"),
            "host_load": host.group(1) if host else None,
            "cpus": cpus.group(1) if cpus else None,
        })
    if not runs:
        raise SystemExit("no tracked acceptance evidence: the guard would be comparing prose to nothing")
    return runs


def authority(runs):
    """The record's authority is the newest all-green archived run, not the newest directory.

    A judged-red finding can legitimately post after it -- acceptance-20260927T221550Z, the compose address
    collision this round found by running the chain, did exactly that -- and a guard that read "newest" off
    the directory listing would demand the record certify a run that failed.
    """
    greens = [r for r in runs if r["fails"] == 0]
    assert greens, "the archive holds no all-green run for the record to be about"
    return max(greens, key=lambda r: r["stamp"])


def split(runs, certified):
    """Everything the docs call 'prior' -- the archived runs other than the one being cited."""
    others = [r for r in runs if r["stamp"] != certified]
    return ([r for r in others if r["fails"] == 0], [r for r in others if r["fails"] > 0])


def days(reds):
    """Per-day red counts, keyed by the six digits the documents print (26 年 09 月 25 日 -> 260925)."""
    counts = {}
    for run in reds:
        key = run["stamp"][2:8]
        counts[key] = counts.get(key, 0) + 1
    return counts


def check_sentence(label, text, greens, reds, certified, *, count_pattern, list_pattern,
                   red_total_pattern=None, day_pattern=None):
    """Compare one document's reproducibility sentence against the runs it describes.

    Only the count, the commit sequence, the red total and the per-day split are checked here; the
    per-run row counts are a separate clause in problems_for, because two of the three documents write
    them and the changelog instead states a growth range.
    """
    problems = []
    count = re.search(count_pattern, text)
    commits = re.search(list_pattern, text)
    if not count or not commits:
        return [f"{label}: the reproducibility sentence could not be read"]
    listed = SHORT.findall(commits.group(1))
    expected = [g["commit"] for g in greens]   # the sentence lists the prior runs; the certified one
    # is named at the head of the same line, so appending it here would demand a 13th commit that the
    # prose never claims.
    if int(count.group(1)) != len(greens):
        problems.append(f"{label}: says {count.group(1)} prior FAIL=0 runs, the archive has {len(greens)}")
    if listed != expected:
        duplicated = sorted({c for c in listed if listed.count(c) > 1})
        detail = f" (duplicate in the list: {duplicated})" if duplicated else ""
        problems.append(f"{label}: lists {len(listed)} commits{detail}, expected {len(expected)}")
    if red_total_pattern:
        total = re.search(red_total_pattern, text)
        if not total or int(total.group(1)) != len(reds):
            problems.append(f"{label}: says {(total.group(1) if total else '?')} judged reds, "
                            f"the archive has {len(reds)}")
    if day_pattern:
        stated = {(y + m + d): int(n) for y, m, d, n in re.findall(day_pattern, text)}
        if stated and stated != days(reds):
            problems.append(f"{label}: per-day red split {stated} != {days(reds)}")
    return problems


def host_clause(label, line, certified_run):
    """A face that quotes the host readings must quote the run's own.

    The checklist states the load average and the VM size the certified run started under, and the
    reproducibility argument for a co-tenant-sensitive suite depends on those being that run's
    numbers. Nothing else in the repo reads them, so until this clause existed the sentence carried a
    previous round's load average through a full pass of the gate -- the same drift as a stale commit
    hash, in a slot the sentence only gained because someone asked what the machine was doing.
    """
    if 'host_load="' not in line:
        return []                      # faces that do not quote the readings are not judged on them
    problems = []
    stated = re.search(r'host_load="([^"]*)"', line)
    vcpu = re.search(r"Docker VM (\d+) vCPU", line)
    for pattern, key, name in ((stated, "host_load", "host load"), (vcpu, "cpus", "Docker vCPU")):
        if not pattern:
            problems.append(f"{label}: quotes a host reading but the {name} figure cannot be read")
        elif certified_run.get(key) is None:
            problems.append(f"{label}: states {name} but the certified SUMMARY records none")
        elif pattern.group(1) != str(certified_run[key]):
            problems.append(f"{label}: says {name} {pattern.group(1)!r}, the certified run recorded "
                            f"{certified_run[key]!r}")
    return problems


CHECKLIST = ROOT / "docs/RELEASE_CHECKLIST.md"
REPORT = ROOT / "docs/TEST_REPORT.md"
CHANGELOG = ROOT / "docs/CHANGELOG_COST500.md"
STATUS = ROOT / "docs/FINAL_RELEASE_STATUS.md"
RUNBOOK = ROOT / "docs/E2E_ACCEPTANCE_RUNBOOK.md"

SENTENCES = (
    (CHECKLIST, r"已有 (\d+) 次 `FAIL=0`", r"次 `FAIL=0`（(.*?)，旧→新", r"当时分别是 ([\d/]+) 行",
     r"留档的判红共 (\d+) 份", r"(\d{2}) 年 (\d{2}) 月 (\d{2}) 日 (\d+) 份"),
    (REPORT, r"已有 (\d+) 次 `FAIL=0`", r"次 `FAIL=0`（(.*?)，旧→新", r"当时分别 ([\d/]+) 行",
     r"留档判红共 (\d+) 份", r"(\d{2}) 年 (\d{2}) 月 (\d{2}) 日 (\d+) 份"),
    (CHANGELOG, r"前序 (\d+) 次 `FAIL=0`", r"次 `FAIL=0` 为 (`[0-9a-f]{7}`(?:→`[0-9a-f]{7}`)*)", None,
     r"(\d+) 份判红 SUMMARY 留档", r"(\d{2}) 年 (\d{2}) 月 (\d{2}) 日 (\d+) 份"),
    # The status file was the fourth face carrying these figures, in English, with nothing comparing
    # them: its header described `414752d` as the authority for two full rounds after two newer green
    # runs had been archived. A face that is not judged is a face that drifts.
    (STATUS, r"(\d+) prior green runs", r"prior green runs[^(]*\((`[0-9a-f]{7}`(?:, `[0-9a-f]{7}`)*) — older",
     r"at (\d+(?:/\d+)+) rows", r"and (\d+) judged-red SUMMARY", r"(\d{2})-(\d{2})-(\d{2}) = (\d+)"),
)

FACES = (CHECKLIST, REPORT, CHANGELOG, STATUS)


def sentence_face(path):
    """The single line a document carries its reproducibility figures on, located by content."""
    text = path.read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if "FAIL=0" in line]
    assert len(hits) == 1, f"{path.name}: expected one reproducibility sentence, found {len(hits)}"
    return hits[0]


def certified_stamp_of(line):
    found = re.search(r"release-evidence/(acceptance-\d{8}T\d{6}Z)", line)
    assert found, "the sentence does not name the run it certifies"
    return found.group(1).replace("acceptance-", "")


def problems_for(path, runs, line=None):
    line = line or sentence_face(path)
    certified = certified_stamp_of(line)
    greens, reds = split(runs, certified)
    count_pattern, list_pattern, rows_pattern, red_total_pattern, day_pattern = next(
        p for p in SENTENCES if p[0] == path)[1:]
    problems = check_sentence(path.name, line, greens, reds, certified,
                              count_pattern=count_pattern, list_pattern=list_pattern,
                              red_total_pattern=red_total_pattern, day_pattern=day_pattern)
    certified_run = next((r for r in runs if r["stamp"] == certified), None)
    assert certified_run, f"{path.name}: cites {certified}, which is not in the tracked archive"
    problems += host_clause(path.name, line, certified_run)
    if path == CHANGELOG:
        # the changelog states a growth range instead of one figure per run
        growth = re.search(r"行数 (\d+)→(\d+)", line)
        widest = max([g["rows"] for g in greens] + [0])
        if not growth:
            problems.append(f"{path.name}: the row-growth figure could not be read")
        elif int(growth.group(2)) != widest:
            problems.append(f"{path.name}: rows →{growth.group(2)} but the widest prior green run is {widest}")
        return problems
    head = (re.search(r"上的 \*\*(\d+) 步全 PASS", line) or re.search(r"\*\*(\d+) 步 PASS", line)
            or re.search(r"\*\*(\d+) steps PASS", line))
    if head and int(head.group(1)) != certified_run["rows"] - 1:
        problems.append(f"{path.name}: says {head.group(1)} steps pass out of {certified_run['rows']} rows "
                        f"for {certified}")
    stated = re.search(rows_pattern, line) if rows_pattern else None
    # "当时分别是 …行" describes the prior runs, one figure each -- the certified run's own width is
    # stated earlier in the same sentence as "N 步全 PASS + 1 步按开关跳过", checked just below.
    target = [g["rows"] for g in greens]
    if not stated:
        problems.append(f"{path.name}: no per-run row count could be read")
    elif [int(x) for x in stated.group(1).split("/")] != target:
        problems.append(f"{path.name}: row counts {stated.group(1)} != {'/'.join(map(str, target))}")
    repeat = re.search(r"同样 (\d+) 行绿了 (\d+) 次", line)
    if repeat:
        # Two of the three Chinese faces end the reproducibility sentence with a rhetorical figure --
        # "it is not as if the same N rows went green M times". That number drifted the same way the
        # others did (it read 12 while seven archived runs were 20 rows wide), and it is the one
        # figure here that a reader takes as the size of the reproducible set.
        width, times = int(repeat.group(1)), int(repeat.group(2))
        actual = sum(1 for g in greens if g["rows"] == width)
        if times != actual:
            problems.append(f"{path.name}: says {width} rows went green {times} times, "
                            f"the archive has {actual}")
    return problems


# ------------------------------------------------------------------ the guards ----

def test_the_docs_agree_with_the_archived_runs():
    runs = archived_runs()
    offenders = {path.name: problems for path in FACES
                 if (problems := problems_for(path, runs))}
    assert not offenders, "the release record disagrees with its own evidence: " + \
        " | ".join(f"{name}: {p}" for name, problems in offenders.items() for p in problems)


def test_the_certified_commit_is_the_one_its_run_recorded():
    runs = archived_runs()
    for path in FACES:
        line = sentence_face(path)
        stamp = certified_stamp_of(line)
        cited = re.search(r"commit `(\w{7})`", line) or re.search(r"`(\w{7})`", line)
        recorded = next((r["commit"] for r in runs if r["stamp"] == stamp), None)
        assert recorded, f"{path.name}: cites {stamp}, which is not in the archive"
        assert cited and cited.group(1) == recorded, \
            f"{path.name}: cites commit {cited and cited.group(1)} for {stamp}, whose SUMMARY says {recorded}"


# ------------------------------------------------------------------ the controls ----

def run(stamp, commit, rows, fails=0):
    return {"stamp": stamp, "commit": commit, "rows": rows, "fails": fails}


PRIOR = [run("20260925T100000Z", "aaaaaaa", 15), run("20260925T110000Z", "bbbbbbb", 16),
         run("20260925T120000Z", "ccccccc", 17, fails=1), run("20260925T130000Z", "ddddddd", 18)]
CERTIFIED = "20260926T090000Z"
GOOD = ("权威运行 `release-evidence/acceptance-20260926T090000Z/` commit `eeeeeee`：已有 3 次 `FAIL=0`"
        "（`aaaaaaa`, `bbbbbbb`, `ddddddd`，旧→新；当时分别是 15/16/18 行），"
        "留档的判红共 1 份（26 年 09 月 25 日 1 份）")


def check(good=GOOD, runs=PRIOR + [run(CERTIFIED, "eeeeeee", 19)]):
    greens, reds = split(runs, CERTIFIED)
    return check_sentence("control", good, greens, reds, "eeeeeee",
                          count_pattern=r"已有 (\d+) 次 `FAIL=0`",
                          list_pattern=r"次 `FAIL=0`（(.*?)，旧→新",
                          red_total_pattern=r"留档的判红共 (\d+) 份",
                          day_pattern=r"(\d{2}) 年 (\d{2}) 月 (\d{2}) 日 (\d+) 份")


def test_the_checker_passes_on_a_sentence_that_matches_the_archive():
    assert check() == [], check()


def test_the_checker_reports_a_count_that_does_not_match_its_own_list():
    problems = check(good=GOOD.replace("已有 3 次", "已有 12 次"))
    assert any("says 12 prior" in p for p in problems), problems


def test_the_checker_reports_a_duplicated_commit_in_the_list():
    """The exact shape this guard was written for: a tail-anchored patch left nine hashes twice."""
    problems = check(good=GOOD.replace("`ddddddd`，旧→新", "`aaaaaaa`, `bbbbbbb`, `ccccccc`, `ddddddd`，旧→新"))
    assert any("duplicate in the list" in p for p in problems), problems


def test_the_checker_reports_a_red_run_counted_as_green():
    """`ddddddd` really did fail: a sentence that still calls it one of the greens must not read clean."""
    problems = check(runs=[PRIOR[0], PRIOR[1], PRIOR[2], run(PRIOR[3]["stamp"], PRIOR[3]["commit"], 18, fails=1),
                           run(CERTIFIED, "eeeeeee", 19)])
    assert any("says 3 prior" in p and "archive has 2" in p for p in problems), problems


def test_the_checker_reports_a_wrong_red_total_and_a_wrong_day_split():
    problems = check(good=GOOD.replace("判红共 1 份", "判红共 9 份"))
    assert any("judged reds" in p for p in problems), problems
    problems = check(good=GOOD.replace("25 日 1 份", "25 日 2 份"))
    assert any("per-day red split" in p for p in problems), problems


def test_the_face_finder_refuses_a_document_with_two_sentences():
    import pytest
    text = CHECKLIST.read_text(encoding="utf-8")
    doubled = text.replace("FAIL=0", "FAIL=0", 1) + "\n" + sentence_face(CHECKLIST) + "\n"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pathlib.Path, "read_text", lambda self, **kw: doubled if self == CHECKLIST else "")
        try:
            sentence_face(CHECKLIST)
        except AssertionError as exc:
            assert "found 2" in str(exc), str(exc)
        else:
            raise AssertionError("two sentences in one document must not read as one")


# ------------------------------------------------------- the host-reading clause's controls ----

def test_the_host_clause_passes_on_the_readings_the_run_recorded():
    line = '启动时宿主 `host_load="5.81 4.99 5.67"`、Docker VM 4 vCPU'
    assert host_clause("control", line, {"host_load": "5.81 4.99 5.67", "cpus": "4"}) == []


def test_the_host_clause_judges_nothing_on_a_face_that_quotes_no_readings():
    assert host_clause("control", "a sentence without any host figures", {"host_load": None,
                                                                         "cpus": None}) == []


def test_the_host_clause_reports_a_previous_round_load_average():
    """What actually shipped through the gate once: the certified run's own line was rewritten for the
    new stamp while the load average next to it still described the run before it."""
    problems = host_clause("control", 'host_load="22.05 22.50 23.10"、Docker VM 4 vCPU',
                           {"host_load": "5.81 4.99 5.67", "cpus": "4"})
    assert any("says host load" in p for p in problems), problems


def test_the_host_clause_refuses_a_reading_with_nothing_behind_it():
    problems = host_clause("control", 'host_load="5.81 4.99 5.67"、Docker VM 4 vCPU',
                           {"host_load": None, "cpus": None})
    assert len([p for p in problems if "records none" in p]) == 2, problems


# ------------------------------------------- the status file's own per-round readings ----

STAMP_ON_LINE = re.compile(r"acceptance-(\d{8}T\d{6}Z)")
LOAD_ON_LINE = re.compile(r'host load "([^"]*)" on a (\d+)-cpu')
WINDOW_ON_LINE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) → (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")


def window_clause(label, line, run):
    """One line's `started → ended` pairs against the run that line names. Pure, so the controls can fire it."""
    stamps = STAMP_ON_LINE.findall(line)
    if len(stamps) != 1:
        return [], 0
    problems = []
    pairs = list(WINDOW_ON_LINE.findall(line))
    for started, ended in pairs:
        if run is None:
            problems.append(f"{label}: line quotes {started} → {ended} for acceptance-{stamps[0]}, "
                            f"which is not in the tracked archive")
        elif (run.get("started"), run.get("ended")) != (started, ended):
            problems.append(f"{label}: acceptance-{stamps[0]} quoted as {started} → {ended}, its SUMMARY "
                            f"says {run.get('started')} → {run.get('ended')}")
    return problems, len(pairs)


def window_problems(runs):
    """A line that names one run and quotes a `started → ended` window must quote that run's own.

    Why this exists and host_clause does not cover it: the faces' stamped line is handled by the writer
    (`scripts/release_face_cells.py` substitutes both fields), but every *round section* in the status file
    opens with its own hand-written window, and nothing read those. On 2026-09-28 a section was committed
    saying `08:18:06Z → 08:35:52Z` while its SUMMARY says `finished_at=2026-09-28T08:35:46Z` -- a
    six-second lie, invisible to every gate until this clause. The rule is deliberately line-local and
    conservative, like the host-reading one: a line naming two runs is skipped rather than guessed at,
    so the coverage floor in the test below is what proves the census is still looking at anything.
    """
    by_stamp = {r["stamp"]: r for r in runs}
    problems = []
    pairs = 0
    for path in FACES:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            found, count = window_clause(path.name, line, by_stamp.get(
                (STAMP_ON_LINE.findall(line) or [None])[0]))
            problems += found
            pairs += count
    return problems, pairs


def paired_readings(line):
    """The run and the readings a single line states, or nothing when the line is ambiguous.

    Either order is allowed -- one section names the evidence directory first, another quotes the
    machine state first -- but a line carrying two runs or two load averages is not something this
    rule may guess at, so it is skipped rather than paired arbitrarily.
    """
    stamps = STAMP_ON_LINE.findall(line)
    loads = LOAD_ON_LINE.findall(line)
    if len(stamps) == 1 and len(loads) == 1:
        return [(stamps[0], loads[0][0], loads[0][1])]
    return []


def status_reading_problems(runs):
    """The English status file quotes a load average per round, so each line is paired with its own run.

    The three Chinese faces carry one reproducibility sentence and are checked against the certified
    run; the status file accumulates a section per round, so the only sound rule is line-local: the
    run named on the line and the readings on that same line must agree with that run's SUMMARY.
    """
    by_stamp = {r["stamp"]: r for r in runs}
    problems = []
    for line in STATUS.read_text(encoding="utf-8").splitlines():
        for stamp, load, cpus in paired_readings(line):
            run = by_stamp.get(stamp)
            if run is None:
                problems.append(f"{STATUS.name}: line cites {stamp}, which is not in the tracked archive")
                continue
            if run.get("host_load") is None:
                problems.append(f"{STATUS.name}: {stamp} quotes a load average, its SUMMARY records none")
                continue
            if load != run["host_load"] or cpus != str(run["cpus"]):
                problems.append(f"{STATUS.name}: {stamp} says load {load!r}/{cpus}-cpu, SUMMARY says "
                                f"{run['host_load']!r}/{run['cpus']}-cpu")
    return problems


def test_the_status_file_quotes_each_round_its_own_host_readings():
    problems = status_reading_problems(archived_runs())
    assert not problems, " | ".join(problems)
    # Coverage floor: the rule is line-local by construction, so a section that stops naming its run
    # and its machine state on one line would silently drop out of the check rather than fail it.
    paired = [r for line in STATUS.read_text(encoding="utf-8").splitlines() for r in paired_readings(line)]
    # The authority is the newest **all-green** run, not the newest directory. A judged-red finding can
    # legitimately be newer -- acceptance-20260927T221550Z is exactly that case, the compose collision this
    # round discovered by running the chain -- so reading "newest" off the directory listing would demand
    # that the record certify a run that failed.
    runs = archived_runs()
    newest = authority(runs)
    assert paired, "no line in the status file pairs a run with its host readings"
    assert any(stamp == newest["stamp"] for stamp, _, _ in paired), \
        f"the certified run {newest['stamp']} is not paired with its readings: {paired}"


def test_every_tracked_summary_is_a_verdict_the_chain_finished():
    """No record may enter the archive without the line that says the pipeline ran to its end.

    `fails == 0` is not "green": a chain killed between rows leaves a SUMMARY whose rows all passed and whose
    last step simply does not exist. That record would be newer than the real authority, and the faces select
    the authority by stamp, so the whole release record would start quoting a two-row half-run.
    """
    aborted = [r["stamp"] for r in archived_runs() if not r["ended"]]
    assert not aborted, (f"{aborted} have no `finished_at=` line, so nothing proves the chain reached its end; "
                         "either re-run to completion or keep the record off the tracked archive")


def test_the_verdict_rule_sees_a_record_whose_last_line_was_never_written():
    """Constructive control: strip one real record's `finished_at` line and the rule must call it aborted."""
    sample = max(archived_runs(), key=lambda r: r["stamp"])
    path = ROOT / "release-evidence" / f"acceptance-{sample['stamp']}" / "SUMMARY.txt"
    text = path.read_text(encoding="utf-8", errors="replace")
    assert ended_of(text) == sample["ended"], "the seam this guard reads is not the one archived_runs uses"
    stripped = "\n".join(line for line in text.splitlines() if not line.startswith("finished_at="))
    assert ended_of(stripped) is None
    assert STEP_ROW.findall(stripped), "the stripped record lost its rows too, so the control proves nothing"


def test_the_status_check_fires_on_a_load_average_borrowed_from_another_round():
    """Line pairing is the whole point: the same file holds several rounds, so a global rule would let
    a section describe one run while quoting another machine state."""
    runs = [run(CERTIFIED, "eeeeeee", 20) | {"host_load": "5.81 4.99 5.67", "cpus": "4"}]
    good = f'run `acceptance-{CERTIFIED}` host load "5.81 4.99 5.67" on a 4-cpu Docker VM'
    assert not status_reading_problems_on(good, runs), status_reading_problems_on(good, runs)
    bad = f'run `acceptance-{CERTIFIED}` host load "22.05 22.50 23.10" on a 4-cpu Docker VM'
    assert any("SUMMARY says" in p for p in status_reading_problems_on(bad, runs)), bad
    wrong_cpus = f'run `acceptance-{CERTIFIED}` host load "5.81 4.99 5.67" on a 8-cpu Docker VM'
    assert any("SUMMARY says" in p for p in status_reading_problems_on(wrong_cpus, runs)), wrong_cpus
    assert status_reading_problems_on("a paragraph with no run named in it", runs) == []
    ambiguous = (f'`acceptance-{CERTIFIED}` and `acceptance-20260920T000000Z` '
                 'host load "1.00 1.00 1.00" on a 4-cpu')
    assert status_reading_problems_on(ambiguous, runs) == [], \
        "a line naming two runs must be skipped, not paired with whichever comes first"


def status_reading_problems_on(text, runs):
    """The line rule, pointed at a sample instead of the file."""
    by_stamp = {r["stamp"]: r for r in runs}
    problems = []
    for line in text.splitlines():
        for stamp, load, cpus in paired_readings(line):
            found = by_stamp.get(stamp)
            if found is None:
                problems.append(f"cites {stamp}, not tracked")
            elif load != found["host_load"] or cpus != str(found["cpus"]):
                problems.append(f"{stamp} says {load}/{cpus}, SUMMARY says {found['host_load']}/{found['cpus']}")
    return problems


# ------------------------------------------- a citation must point at tracked evidence ----

EVIDENCE_REF = re.compile(r"release-evidence/((?:acceptance|browser-a11y)-(?:\d{8}T\d{6}Z))")
DOCUMENTS = (CHECKLIST, REPORT, CHANGELOG, STATUS, ROOT / "docs/CODE_WALKTHROUGH.md")


def tracked_evidence():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "release-evidence"],
                            capture_output=True, text=True, check=True).stdout.split()
    return {pathlib.Path(relative).parent.name for relative in listed}


def citation_problems(tracked):
    """Every evidence directory the record names has to be in the repository, not just on this laptop.

    The reproducibility argument is that a reader can re-derive the figures from the archive; a run
    cited by a document but left untracked is exactly the reading nobody else can check, and it stays
    invisible to every other guard here because those read the tracked set as their denominator.
    """
    problems = []
    for path in DOCUMENTS:
        cited = set(EVIDENCE_REF.findall(path.read_text(encoding="utf-8")))
        for stamp in sorted(cited - tracked):
            problems.append(f"{path.name}: cites release-evidence/{stamp}, which is not tracked")
    return problems


def test_every_evidence_directory_the_record_cites_is_tracked():
    problems = citation_problems(tracked_evidence())
    assert not problems, " | ".join(problems)


def test_the_citation_check_fires_on_an_untracked_run():
    """The denominator is the tracked set, so the sample has to show a missing directory reddens it --
    an empty intersection would otherwise pass for the wrong reason."""
    tracked = tracked_evidence()
    assert tracked, "nothing is tracked under release-evidence/, so this guard compares prose to nothing"
    sample = "证据 `release-evidence/acceptance-20260101T000000Z/SUMMARY.txt`"
    stamp = EVIDENCE_REF.findall(sample)[0]
    assert stamp not in tracked
    assert any("not tracked" in p for p in citation_problems_on(sample, tracked)), sample
    assert citation_problems_on(sample, tracked | {stamp}) == []


def citation_problems_on(text, tracked):
    return [f"cites {stamp}, not tracked" for stamp in set(EVIDENCE_REF.findall(text)) - tracked]


# ------------------------------------------------ the fourth face's own controls ----

STATUS_PATTERNS = next(p for p in SENTENCES if p[0] is STATUS)[1:]
STATUS_GOOD = ("Authoritative run: evidence in `release-evidence/acceptance-20260926T090000Z/`, commit `eeeeeee`, "
               'started under `host_load="5.81 4.99 5.67"` on a Docker VM 4 vCPU, with 3 prior green runs on this host '
               "(`aaaaaaa`, `bbbbbbb`, `ddddddd` — older → newer, at 15/16/18 rows with `FAIL=0` in every "
               "`SUMMARY.txt`), and 1 judged-red SUMMARY kept as a finding (26-09-25 = 1).")


def check_status(good=STATUS_GOOD, runs=PRIOR + [run(CERTIFIED, "eeeeeee", 19)]):
    """The status file's sentence, judged through the same reader the Chinese faces use.

    The four figures are the ones that drifted here in reality: this header described `414752d` as the
    authority across two later green runs, so a control that only proves the Chinese faces fire would
    leave the newly-judged face judged by nothing.
    """
    greens, reds = split(runs, CERTIFIED)
    count_p, list_p, _rows_p, red_p, day_p = STATUS_PATTERNS
    return check_sentence("status face", good, greens, reds, CERTIFIED, count_pattern=count_p,
                          list_pattern=list_p, red_total_pattern=red_p, day_pattern=day_p)


def test_the_status_face_control_sentence_reads_clean():
    assert check_status() == [], check_status()


def test_the_status_face_reports_a_green_count_that_drifted():
    problems = check_status(good=STATUS_GOOD.replace("with 3 prior green runs", "with 12 prior green runs"))
    assert any("says 12 prior" in p for p in problems), problems


def test_a_newer_red_run_does_not_become_the_authority():
    """The two readings of "newest" must be shown to differ, or the green-only floor is untested.

    Plant a judged-red directory newer than every archived green: the directory listing's newest is the
    plant, the record's authority stays the newest all-green run. Swapping the rule back to
    `max(archived_runs())` would make this assertion invert, which is the point.
    """
    runs = archived_runs()
    plant = {"stamp": "20990101T000000Z", "commit": "x" * 40, "rows": 23,
             "fails": 1, "host_load": "1.0 1.0 1.0", "cpus": "4"}
    with_red = runs + [plant]
    assert max(with_red, key=lambda r: r["stamp"]) is plant, "the plant is not the newest directory"
    authority = max([r for r in with_red if r["fails"] == 0], key=lambda r: r["stamp"])
    assert authority["stamp"] != plant["stamp"], "a run that failed became the record's authority"
    real_green = max([r for r in runs if r["fails"] == 0], key=lambda r: r["stamp"])
    assert authority["stamp"] == real_green["stamp"], \
        "the authority moved away from the newest archived green for no reason"


def test_the_status_face_reports_a_wrong_red_total_and_day_split():
    assert any("judged reds" in p for p in
               check_status(good=STATUS_GOOD.replace("and 1 judged-red", "and 9 judged-red")))
    assert any("per-day red split" in p for p in
               check_status(good=STATUS_GOOD.replace("(26-09-25 = 1)", "(26-09-25 = 2)")))


def test_the_status_face_reports_a_duplicated_commit():
    problems = check_status(good=STATUS_GOOD.replace("`ddddddd` — older", "`aaaaaaa`, `ddddddd` — older"))
    assert any("duplicate in the list" in p for p in problems), problems


def mutated_line(path, old, new):
    line = sentence_face(path)
    assert line.count(old) == 1, f"{path.name}: {old!r} is not a unique anchor in its sentence"
    return line.replace(old, new)


def mutated_status_line(old, new):
    return mutated_line(STATUS, old, new)


def test_the_rows_clause_fires_on_the_fourth_face():
    """The per-run row list is its own clause in `problems_for`, so it gets its own red side."""
    runs = archived_runs()
    assert problems_for(STATUS, runs) == []
    stale = mutated_status_line(re.search(r"at [\d/]+ rows", sentence_face(STATUS)).group(0), "at 15/16 rows")
    problems = problems_for(STATUS, runs, stale)
    assert any("row counts" in p for p in problems), problems


def test_the_step_width_clause_fires_on_the_fourth_face():
    """The anchor is read from the face, not typed here.

    This control used to hardcode "**20 steps PASS". That made the guard a second face of the very
    figure it judges: the round that widened the pipeline had to remember to edit the test as well, and
    a missed edit reads as a broken control rather than as a stale document.
    """
    runs = archived_runs()
    line = sentence_face(STATUS)
    head = re.search(r"\*\*(\d+) steps PASS and \d+ recorded as skipped\*\*", line)
    assert head, "the status face no longer states the step width this clause guards"
    stated, one_less = int(head.group(1)), int(head.group(1)) - 1
    stale = mutated_status_line(head.group(0), head.group(0).replace(f"**{stated} steps PASS",
                                                                    f"**{one_less} steps PASS", 1))
    problems = problems_for(STATUS, runs, stale)
    assert any(f"says {one_less} steps pass" in p for p in problems), problems


def test_the_host_clause_fires_on_the_fourth_face():
    """The wrong reading is taken from another real run, so the control cannot drift with the docs."""
    runs = archived_runs()
    certified = authority(runs)
    other = next(r for r in reversed(runs) if r["fails"] == 0 and r["host_load"]
                 and r["host_load"] != certified["host_load"])
    stale = mutated_status_line(f'host_load="{certified["host_load"]}"', f'host_load="{other["host_load"]}"')
    problems = problems_for(STATUS, runs, stale)
    assert any("says host load" in p for p in problems), problems


def test_the_repeat_figure_clause_fires_on_a_stale_count():
    """「同样 W 行绿了 M 次」 is the figure a reader takes as the size of the reproducible set.

    It read 12 while seven archived runs were 20 rows wide -- the same kind of drift as a stale commit
    list, in the one clause of the sentence that was never recomputed when the archive grew.

    Both arms take their numbers from the archive the guard is reading, so widening the pipeline moves
    the control with the documents instead of breaking it: the red arm is the stated width with a count
    the archive cannot support, the quiet arm is a different width with the count the archive does give.
    """
    runs = archived_runs()
    assert problems_for(CHECKLIST, runs) == []
    line = sentence_face(CHECKLIST)
    stated = re.search(r"同样 (\d+) 行绿了 (\d+) 次", line)
    assert stated, "the checklist no longer carries the reproducibility figure this clause guards"
    greens, _reds = split(runs, certified_stamp_of(line))
    width = int(stated.group(1))
    actual = sum(1 for r in greens if r["rows"] == width)
    stale = mutated_line(CHECKLIST, stated.group(0), f"同样 {width} 行绿了 {actual + 5} 次")
    problems = problems_for(CHECKLIST, runs, stale)
    assert any(f"went green {actual + 5} times, the archive has {actual}" in p for p in problems), problems
    # ...and the clause must stay quiet when the two numbers describe the same archive differently.
    other_width = sorted({r["rows"] for r in greens})[0]
    other_count = sum(1 for r in greens if r["rows"] == other_width)
    other = mutated_line(CHECKLIST, stated.group(0), f"同样 {other_width} 行绿了 {other_count} 次")
    assert problems_for(CHECKLIST, runs, other) == [], problems_for(CHECKLIST, runs, other)


# ------------------------------------------------------------------ the window clause ----

def fake_run(stamp="20260928T081806Z", started="2026-09-28T08:18:06Z", ended="2026-09-28T08:35:46Z"):
    return {"stamp": stamp, "started": started, "ended": ended}


def test_the_window_clause_passes_on_the_run_it_names():
    line = ("Authoritative run `acceptance-20260928T081806Z` (commit `3dbd175`, "
            "2026-09-28T08:18:06Z → 2026-09-28T08:35:46Z, 23 rows)")
    problems, pairs = window_clause("control", line, fake_run())
    assert pairs == 1, f"the clause saw no window in a line that quotes one: {line}"
    assert not problems, f"a window matching its run was reported: {problems}"


def test_the_window_clause_fires_on_a_finished_at_copied_from_the_previous_round():
    """The exact error this clause was written for: a section hand-typed six seconds off."""
    line = ("Authoritative run `acceptance-20260928T081806Z` (commit `3dbd175`, "
            "2026-09-28T08:18:06Z → 2026-09-28T08:35:52Z, 23 rows)")
    problems, pairs = window_clause("control", line, fake_run())
    assert pairs == 1, "the planted window was not seen at all, so the next assertion proves nothing"
    assert problems and "08:35:46Z" in problems[0], (
        f"a quoted end that is six seconds later than the SUMMARY's finished_at must be named with the "
        f"true value so a reader can fix it without opening the archive: {problems}")


def test_the_window_clause_fires_on_an_untracked_run():
    line = "`acceptance-20990101T000000Z` ran 2026-09-28T08:18:06Z → 2026-09-28T08:35:46Z"
    problems, pairs = window_clause("control", line, None)
    assert pairs == 1 and problems and "not in the tracked archive" in problems[0], (
        f"a window attached to a run that is not in the evidence is the same class of claim as citing an "
        f"untracked directory: {problems}")


def test_a_line_naming_two_runs_is_not_guessed_at():
    """The conservative side: with two stamps on one line the clause cannot know whose window it is."""
    line = ("like `acceptance-20260928T081806Z` and `acceptance-20260928T065139Z`, "
            "2026-09-28T08:18:06Z → 2026-09-28T08:35:46Z")
    problems, pairs = window_clause("control", line, fake_run())
    assert pairs == 0 and not problems, (
        f"an ambiguous line must be skipped, not attributed to whichever stamp the reader liked: {problems}")


def test_every_window_in_the_record_matches_its_own_run():
    problems, pairs = window_problems(archived_runs())
    assert not problems, "the release record misquotes a run's window: " + " | ".join(problems)
    # Coverage floor: the rule is line-local, so a face that stopped naming its run on the same line as its
    # window would drop out of the census silently -- four faces open with a stamped window each.
    assert pairs >= 4, (f"only {pairs} quoted windows were read across the four faces; the stamped lines "
                        f"and the round sections all carry one, so the census has gone blind")


def census_module():
    spec = importlib.util.spec_from_file_location("server_refusal_census_under_test",
                                                 ROOT / "scripts/server_refusal_census.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reader_module():
    spec = importlib.util.spec_from_file_location("stamp_release_faces_for_census",
                                                 ROOT / "scripts/stamp_release_faces.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_two_refusal_observers_agree_on_the_certified_run():
    """Whatever the gate recorded as refused has to be a subset of what the api answered.

    Two observers of one fact, neither reading the other's file: the browser leg sees responses
    (including the ones it fulfills itself), the api log sees requests. The subset direction is the point --
    a refusal the server answered but no page recorded means the gate missed traffic it was sitting on, and
    that is exactly how the console sentence was able to claim six refusals for eighteen. Once a certified
    run carries `refusal_counts`, its cross-check artifact has to be committed too, or the reading exists
    only on the laptop that ran the chain.
    """
    census, reader = census_module(), reader_module()
    runs = [r for r in reader.runs() if r["fails"] == 0]
    chosen = max(runs, key=lambda r: r["stamp"])
    report = reader.browser_pair(chosen)
    if report is None:
        pytest.skip(f"{chosen['stamp']} has no paired browser report to reconcile against")
    data = json.loads(report.read_text(encoding="utf-8"))
    if "refusal_counts" not in data:
        pytest.skip(f"{report.parent.name} predates the per-endpoint census; the reconciliation starts "
                    "with the first certified run that recorded it")
    artifact = report.parent / "server-refusals.txt"
    assert artifact.exists(), (f"{report.parent.name} recorded refusals but has no {artifact.name}; run "
                               "`python3 scripts/server_refusal_census.py --write` and `git add -f` it")
    tracked = tracked_evidence()
    assert artifact.parent.name in tracked, (f"{artifact} is not tracked, so the cross-check is a host-only "
                                             "reading while the faces quote it")
    server = collections.Counter()
    parsed = 0
    for line in artifact.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(\d{3}) (\w+)\s+(\S+)\s+(\d+)$", line)
        if m:
            code, method, path, count = m.groups()
            server[(method, path, int(code))] += int(count)
            parsed += 1
        if line.startswith("request_lines_parsed:"):
            parsed = max(parsed, int(line.split(":", 1)[1].strip()))
    problems = census.reconcile(server, census.browser_census(report), parsed, parsed)
    assert not problems, "the api and the browser leg disagree about what was refused: " + " | ".join(problems)


def test_the_refusal_reconciliation_fires_on_both_polarities():
    """Each direction needs its own broken-shape control, or the subset rule could be silently vacuous."""
    census = census_module()
    server = collections.Counter({("GET", "/api/auth/me", 401): 28, ("POST", "/api/account/erasure", 403): 2})
    honest = collections.Counter(server)
    honest[("GET", census.INJECTED, 500)] = 2
    assert census.reconcile(server, honest, 40, 40) == [], (
        "the honest pair went red, so the rule cannot be read as a subset check")
    blind = collections.Counter(honest)
    del blind[("POST", "/api/account/erasure", 403)]
    missing = census.reconcile(server, blind, 40, 40)
    assert any("2x POST /api/account/erasure" in p for p in missing), (
        f"a gate that recorded none of the refusals the api answered was not named: {missing}")
    reached = collections.Counter(server)
    reached[("GET", census.INJECTED, 500)] = 2
    assert any("reached the api" in p for p in census.reconcile(reached, honest, 40, 40)), (
        "the injected route showed up on the server side and the census said nothing, so the stale-panel "
        "arm could be reading a real 500 without anyone noticing")
    assert any("the parser saw nothing" in p for p in
               census.reconcile(collections.Counter(), collections.Counter(), 0, 60)), (
        "zero parsed lines was read as 'no refusals' rather than as an instrument failure")


def test_the_unsettled_exemption_is_named_and_not_a_rubber_stamp():
    """A shortfall may only be excused by evidence that names the endpoint it explains, never by an absence.

    The shapes that must be told apart: a page closed while still waiting on THIS endpoint (excused, and
    the census prints which page and what it waited on), a page closed while waiting on something else
    (red), the gate recorded nothing left open (red -- the blindness then has no story), a legacy string
    entry or a missing reading (red: no reading is not a clean reading).
    """
    census = census_module()
    server = collections.Counter({("GET", "/api/auth/me", 401): 3})
    seen = collections.Counter({("GET", "/api/auth/me", 401): 2})
    assert census.shortfall(server, seen) == [(("GET", "/api/auth/me", 401), 3, 2)], (
        "the shortfall is not exposed as data, so the census cannot print what it is excusing")
    named_waiting = [{"label": "desktop-stale-admin", "error": "TimeoutError",
                      "outstanding": ["GET /api/auth/me"]}]
    assert census.reconcile(server, seen, 40, 60, named_waiting) == [], (
        "a page named as still waiting on exactly this endpoint did not excuse the shortfall, so the "
        "settling reading is not consumed anywhere")
    off_target = [{"label": "desktop-axe-studio", "error": "TimeoutError",
                   "outstanding": ["POST /api/auth/logout"]}]
    assert census.reconcile(server, seen, 40, 60, off_target), (
        "a page waiting on a DIFFERENT endpoint excused this shortfall -- the exemption is a tolerance "
        "with a label on it again")
    assert census.outstanding_keys(named_waiting) == {"GET /api/auth/me"}, (
        "the outstanding reading is not taken as data, so the note and the judgement can disagree")
    assert census.reconcile(server, seen, 40, 60, []), (
        "an empty unsettled list excused the shortfall -- the exemption became a tolerance")
    assert census.reconcile(server, seen, 40, 60), (
        "a report with no unsettled reading excused the shortfall -- absence read as innocence")


def test_a_certified_run_that_recorded_the_request_axis_wrote_it_into_the_artifact(tmp_path):
    """The axis only counts as measured if the run that recorded it also published it.

    A `report.json` with `request_counts` whose cross-check artifact has no `gate_issued_events` line would
    mean the census read the field and dropped it, which is how a witness becomes a rumour again.
    """
    census, reader = census_module(), reader_module()
    runs = [r for r in reader.runs() if r["fails"] == 0]
    chosen = max(runs, key=lambda r: r["stamp"])
    report = reader.browser_pair(chosen)
    if report is None:
        pytest.skip(f"{chosen['stamp']} has no paired browser report")
    data = json.loads(report.read_text(encoding="utf-8"))
    if "request_counts" not in data:
        pytest.skip(f"{report.parent.name} predates the request axis")
    assert data["request_events"] == sum(data["request_counts"].values()), (
        f"{report.parent.name} publishes a total that is not the sum of its own per-endpoint counts")
    artifact = report.parent / "server-refusals.txt"
    assert artifact.exists(), f"{report.parent.name} recorded the axis but has no artifact"
    text = artifact.read_text(encoding="utf-8")
    for key in ("gate_issued_events", "gate_aborted_events"):
        assert f"{key}: " in text, f"{artifact} has no {key} line although the report carried it"
    assert "unsettled_at_close" in data, (
        f"{report.parent.name} settled its pages but did not publish what the settling found")


# ------------------------------------------------- figures the tracked contracts already settle
MATRIX = ROOT / "shared/contracts/authority-matrix.json"
OPENAPI = ROOT / "shared/contracts/openapi-v13.json"

# (face, marker selecting exactly one line, pattern, keys of contract_figures in capture order). These are
# the copies of a version-controlled artifact's own numbers that live in prose: they need no run to
# recompute, so the only honest rule is "equal to the artifact, right now".
CONTRACT_QUOTES = (
    ("docs/CODE_WALKTHROUGH.md", "authority-matrix.json", r"（(\d+) 条路由 / (\d+) 条写操作的权限派生件",
     ("routes", "writes")),
    ("docs/CODE_WALKTHROUGH.md", "authority-matrix.json", r"OpenAPI v13（(\d+) paths", ("openapi_paths",)),
    ("docs/CODE_WALKTHROUGH.md", "由运行中的 FastAPI", r"OpenAPI v13（(\d+) paths）", ("openapi_paths",)),
    ("docs/RELEASE_CHECKLIST.md", "哪些端点带这道检查不靠本文复述",
     r"现读 (\d+) 条路由 / (\d+) 条写操作 / 其中 (\d+) 条带再认证", ("routes", "writes", "step_up")),
    # The same two contract figures stated in the test report's own words. This phrasing existed for four
    # rounds with the pre-v13 numbers (96 / 51) while the walkthrough line beside it was policed, so the
    # gate read green and the report stayed wrong.
    ("docs/TEST_REPORT.md", "从 AST 派生",
     r"从 AST 派生 (\d+) 条 /api 路由、(\d+) 条写操作", ("routes", "writes")),
    # 2026-09-29: a subagent census found nine more present-tense figures with no owner. They were first given
    # a parallel matcher in `test_reference_doc_figures.py`, which a second review proved wrong in three ways
    # -- it globbed every `docs/*.md`, so a soft-wrapped *history* sentence (`docs/FINAL_RELEASE_STATUS.md:1390`)
    # would read as a live claim; three of its four route figures duplicated these rows; and its unanchored
    # nouns accused unrelated sentences. The shape that does not have those defects is this one: a marker that
    # must select exactly one line, and one derivation per figure.
    ("docs/RELEASE_CHECKLIST.md", "审批流程稳定",
     r"（(\d+) 条 /api 路由、(\d+) 条写操作", ("routes", "writes")),
    ("docs/RELEASE_CHECKLIST.md", "审批流程稳定",
     r"只有 (\d+) 条写路由不带角色检查", ("unguarded_writes",)),
    ("docs/CODE_WALKTHROUGH.md", "healthcheck/`service_completed_successfully`",
     r"（\+(\d+) 个覆层）", ("compose_overlays",)),
    ("docs/CODE_WALKTHROUGH.md", "跨租户外键 + Payout 不可变",
     r"(\d+) 个迁移", ("migrations",)),
    ("docs/CODE_WALKTHROUGH.md", "跨租户外键 + Payout 不可变",
     r"db/migrations/001…(\d+)\.sql", ("migration_last",)),
    ("docs/CODE_WALKTHROUGH.md", "JSON Schema：顶层",
     r"顶层 (\d+) 个", ("schemas_top",)),
    ("docs/CODE_WALKTHROUGH.md", "JSON Schema：顶层",
     r"`design-reference/` (\d+) 个旧版", ("schemas_legacy",)),
    ("docs/CODE_WALKTHROUGH.md", "默认/商业/Provider 契约/Payment 契约",
     r"(\d+) 个测试模块", ("acceptance_suites",)),
    ("docs/CODE_WALKTHROUGH.md", "compose-acceptance`（真实起 Docker",
     r"(\d+) 个 job", ("ci_jobs",)),
    ("docs/CODE_WALKTHROUGH.md", "信息架构其实不差",
     r"(\d+) 条 /api 路由", ("routes",)),
    ("docs/CODE_WALKTHROUGH.md", "前端体验层面",
     r"(\d+) 条 /api 路由", ("routes",)),
    ("docs/TEST_REPORT.md", "三类注入对照",
     r"(\d+) 条判决函数单测", ("gate_cases",)),
)


def contract_figures():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))["routes"]
    document = json.loads(OPENAPI.read_text(encoding="utf-8"))
    migrations = sorted(int(p.name[:3]) for p in (ROOT / "db/migrations").glob("[0-9]" * 3 + "*.sql"))
    top = sorted((ROOT / "shared/contracts").glob("*.schema.json"))
    legacy = sorted((ROOT / "shared/contracts/design-reference").glob("*.schema.json"))
    figures = {"routes": len(matrix),
               "writes": sum(1 for r in matrix if r.get("writes")),
               "step_up": sum(1 for r in matrix if r.get("step_up")),
               "unguarded_writes": sum(1 for r in matrix if r.get("writes")
                                       and r.get("authority") == "app-level-only"),
               "openapi_paths": len(document.get("paths", {})),
               "migrations": len(migrations),
               "migration_last": max(migrations) if migrations else 0,
               "schemas_top": len(top),
               "schemas_legacy": len(legacy),
               "compose_overlays": len(sorted(ROOT.glob("docker-compose.*.yml"))),
               "acceptance_suites": len(sorted((ROOT / "services/acceptance").rglob("test_*.py"))),
               "ci_jobs": len(yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(
                   encoding="utf-8"))["jobs"]),
               "gate_cases": sum(1 for line in (ROOT / "tests/unit/test_browser_a11y_gate.py").read_text(
                   encoding="utf-8").splitlines() if line.startswith("def test_")),
               "security_schemes": sorted((document.get("components", {}) or {}).get("securitySchemes", {}) or {})}
    return figures



def contract_problems(root, figures):
    problems = []
    for rel, marker, pattern, keys in CONTRACT_QUOTES:
        path = root / rel
        if not path.exists():
            problems.append(f"{rel} is missing, so its quoted contract figures cannot be checked")
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines) if marker in line]
        if len(hits) != 1:
            problems.append(f"{rel}: marker {marker!r} selects {len(hits)} lines, expected 1")
            continue
        found = list(re.finditer(pattern, lines[hits[0]]))
        if len(found) != 1:
            problems.append(f"{rel}: {pattern!r} matched {len(found)} times, expected 1")
            continue
        for value, key in zip(found[0].groups(), keys):
            if int(value) != figures[key]:
                problems.append(f"{rel}: says {key}={value}, the tracked artifact says {figures[key]}")
    return problems


def test_figures_quoted_from_the_tracked_contracts_match_those_contracts():
    figures = contract_figures()
    problems = contract_problems(ROOT, figures)
    assert not problems, " | ".join(problems)
    # A vacuous census is the failure mode here: four quotes across two faces, all resolved against a real
    # artifact whose own fields (not a method-name guess) decide.
    assert figures["routes"] > 50 and figures["writes"] > 20 and figures["openapi_paths"] > 50, figures


def test_a_stale_contract_figure_is_named():
    """Plant one wrong digit in every registered row and the clause has to name that row's truth.

    This used to tamper CONTRACT_QUOTES[0] alone, which left the other 15 rows asserting nothing about their
    own teeth: a row whose pattern silently stopped matching, or whose marker landed on the wrong sentence,
    would have read as "the figures agree".
    """
    figures = contract_figures()
    faces = sorted({rel for rel, _m, _p, _k in CONTRACT_QUOTES})
    for number, (rel, marker, pattern, keys) in enumerate(CONTRACT_QUOTES):
        text = (ROOT / rel).read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if marker in line)
        found = re.search(pattern, lines[index])
        assert found, f"row {number} ({rel}, {marker!r}): its pattern matches nothing on its own line"
        stale = str(int(found.group(1)) + 7)
        tampered = (lines[:index] + [lines[index][:found.start(1)] + stale + lines[index][found.end(1):]]
                    + lines[index + 1:])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sources = faces + [str(MATRIX.relative_to(ROOT)), str(OPENAPI.relative_to(ROOT))]
            for source in sources:
                target = root / source
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / source).read_bytes())
            # the copied face has to be replaced by the tampered one *after* the copy, or the arm reads the
            # pristine line back and reports "the figure agrees" about a plant it never installed.
            (root / rel).write_text("".join(tampered), encoding="utf-8")
            problems = contract_problems(root, figures)
        named = [p for p in problems if rel in p and f"{keys[0]}={stale}" in p]
        assert named, f"row {number} ({rel}, {marker!r}) did not name the planted stale figure: {problems}"


def test_the_contract_quoting_census_is_not_vacuous():
    """Every face the table names has to be openable, or the clause reports a clean sheet over nothing."""
    figures = contract_figures()
    faces = sorted({rel for rel, _m, _p, _k in CONTRACT_QUOTES})
    absent = [rel for rel in faces if not (ROOT / rel).exists()]
    assert not absent, f"the table quotes faces that are not in the tree: {absent}"
    assert len(CONTRACT_QUOTES) >= 4, (f"only {len(CONTRACT_QUOTES)} contract quotes are registered; the "
                                       "prose carries more copies than the clause checks")
    assert figures["security_schemes"], (
        "the tracked OpenAPI contract carries no securitySchemes, so the walkthrough's sentence about them "
        "needs to be re-read rather than left asserting the opposite of what the artifact holds")


# ------------------------------------------------------------------ the "same commit" pairing claim
RUN_STAMP = re.compile(r"acceptance-(\d{8}T\d{6}Z)")
LEG_STAMP = re.compile(r"browser-a11y-(\d{8}T\d{6}Z)")
# Only lines that *assert* the equality are in scope. Two faces name a browser leg and an acceptance run on
# the same line without claiming they belong together ("浏览器侧另留两条发现记录: ..."), and reading a
# proximity pairing out of such a line would make the clause fire on prose that says nothing checkable.
SAME_COMMIT_CLAIM = ("同一 commit", "stamped with the same commit", "同一個 commit")
COMMIT_TOKEN = re.compile(r"`([0-9a-f]{7})`")
# Sentences, not lines: these faces are single physical lines carrying many claims, and pairing a leg with
# every run stamp anywhere on the line would compare a leg against runs it never refers to.
SENTENCE_SPLIT = re.compile(r"[。；;]|\.\s")


def sentences(line):
    return [part for part in SENTENCE_SPLIT.split(line) if part.strip()]


def leg_commits():
    out = {}
    for path in sorted((ROOT / "release-evidence").glob("browser-a11y-*/report.json")):
        stamp = path.parent.name.replace("browser-a11y-", "")
        try:
            out[stamp] = json.loads(path.read_text(encoding="utf-8")).get("git_commit", "?")
        except ValueError:
            out[stamp] = "??"
    return out


def pairing_problems(runs, legs, paths):
    """A sentence that says the a11y record carries the run's commit has to name things that can be checked.

    Three readings are compared: the leg's own `git_commit`, every 7-hex commit token in the sentence, and
    every run stamp in the sentence (via its tracked SUMMARY). All named values must agree, and a sentence
    that claims the equality while naming nothing resolvable is reported rather than skipped -- that shape
    is unfalsifiable prose, which is the thing this whole file exists to stop.
    """
    problems = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for sentence in sentences(line):
                if not any(claim in sentence for claim in SAME_COMMIT_CLAIM):
                    continue
                leg_stamps = LEG_STAMP.findall(sentence)
                run_stamps = RUN_STAMP.findall(sentence)
                tokens = COMMIT_TOKEN.findall(sentence)
                named = []
                for leg in leg_stamps:
                    if leg not in legs:
                        problems.append(f"{path.name}:{number} names {leg}, whose report.json is not on disk")
                    else:
                        named.append(legs[leg][:7])
                named += [runs[r][:7] for r in run_stamps if r in runs]
                named += [t for t in tokens if len(t) == 7 and t.islower() and not t.isdigit()]
                unresolved = [r for r in run_stamps if r not in runs]
                if unresolved:
                    problems.append(f"{path.name}:{number} claims a shared commit with untracked runs "
                                    f"{unresolved}")
                counterparts = [runs[r][:7] for r in run_stamps if r in runs]
                counterparts += [t for t in tokens if len(t) == 7 and not t.isdigit()]
                if named and not counterparts:
                    # This is the shape the clause was written for: a sentence that says "the same commit"
                    # next to a leg and nothing to compare it with. Left alone it reads as a true pairing
                    # forever, even when the leg is two days and four commits out of date.
                    problems.append(f"{path.name}:{number} claims a shared commit naming only "
                                    f"{leg_stamps or tokens}; no run stamp or commit token in the same "
                                    "sentence, so the pairing is unfalsifiable as written")
                    continue
                if counterparts and not named:
                    problems.append(f"{path.name}:{number} claims a shared commit but names no leg whose "
                                    "report can be read")
                    continue
                if not named and not counterparts:
                    problems.append(f"{path.name}:{number} claims a shared commit but names nothing that "
                                    "can be checked")
                    continue
                if len(set(named + counterparts)) > 1:
                    problems.append(f"{path.name}:{number} says '同一 commit' about values that differ: "
                                    f"{sorted(set(named + counterparts))}")
    return problems


def test_every_same_commit_claim_matches_the_run_it_names():
    runs = {r["stamp"]: r["commit"] for r in archived_runs()}
    problems = pairing_problems(runs, leg_commits(), [p for p in DOCUMENTS if p.exists()])
    assert not problems, " | ".join(problems)


def test_the_same_commit_clause_fires_on_a_stale_leg_pointer():
    """The exact defect this round found: the head of the checklist named a two-day-old leg as the
    certified run's own record, and no guard could see it because another cell owned that same line."""
    runs = {"20260928T084412Z": "1dbf99e" + "0" * 33}
    legs = {"20260927T174714Z": "2f25fe5" + "0" * 33}
    line = ("权威运行是 `acceptance-20260928T084412Z`，浏览器验收另见 "
            "`release-evidence/browser-a11y-20260927T174714Z/report.json`，同一 commit")
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "CHECKLIST.md"
        face.write_text(line + "\n", encoding="utf-8")
        problems = pairing_problems(runs, legs, [face])
    assert any("2f25fe5" in p and "1dbf99e" in p for p in problems), (
        f"a leg from another tree was accepted: {problems}")
    honest = {"20260928T085606Z": "1dbf99e" + "0" * 33}
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "CHECKLIST.md"
        face.write_text(line.replace("20260927T174714Z", "20260928T085606Z") + "\n", encoding="utf-8")
        assert pairing_problems(runs, honest, [face]) == [], "the clause cannot read an honest pairing"
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "NOTES.md"
        face.write_text("浏览器侧另留两条发现记录：`browser-a11y-20260927T174714Z` 与 "
                        "`acceptance-20260928T084412Z` 各说各的\n", encoding="utf-8")
        assert pairing_problems(runs, legs, [face]) == [], (
            "the clause paired a line that never claims a shared commit")


# ------------------------------------------------------------------ an authority line cites its own leg
CLAUSE_SPLIT = re.compile(r"[，,；;、]")


def authority_pointer_problems(paths, authority_stamp, leg_owner, legs_on_disk):
    """Every a11y leg cited on a face's authority line has to belong to the run that line is about.

    The scope is the physical LINE for deciding *whether a line is an authority line* -- the changelog keeps
    its browser pointer in the last `；`-split clause of one giant parenthetical, and a sentence-scoped first
    version of this clause stayed silent while the pointer named a leg four commits back. The scope for
    *deciding who owns a cited leg* is the CLAUSE: an authority line enumerates eight to sixteen runs by
    design, so letting any leg whose owner appears somewhere on the line pass would wave through exactly the
    stale pointer this clause exists to catch (measured: swapping the authority's own leg for the leg of
    `20260926T093142Z` returned no problem at all under line-wide membership).

    Three shapes are accepted for a leg on an authority line: it pairs to the authority; it pairs to another
    run that the same *clause* names (a face comparing two authorities, which `RELEASE_CHECKLIST.md:5` does);
    or the reader pairs it to nothing because the report predates the row window. The third is counted and
    capped, and it is only granted when that report is actually on disk -- a cited path with no report behind
    it is a separate violation, because "nothing pairs to it" and "there is nothing to pair" read identically
    otherwise.
    """
    problems, checked, unattributable, per_face = [], 0, 0, {}
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if authority_stamp not in set(RUN_STAMP.findall(line)):
                continue
            for clause in CLAUSE_SPLIT.split(line):
                named = set(RUN_STAMP.findall(clause))
                for leg in dict.fromkeys(LEG_STAMP.findall(clause)):
                    checked += 1
                    per_face[path.name] = per_face.get(path.name, 0) + 1
                    if leg not in legs_on_disk:
                        problems.append(f"{path.name}:{number} cites browser-a11y-{leg}, which has no "
                                        "report.json in the archive -- nothing can be compared against it")
                    elif (owner := leg_owner(leg)) is None:
                        unattributable += 1
                    elif owner != authority_stamp and owner not in named:
                        problems.append(f"{path.name}:{number} cites browser-a11y-{leg}, which pairs to "
                                        f"acceptance-{owner}, in a clause of a line whose authority is "
                                        f"acceptance-{authority_stamp}")
    return problems, checked, unattributable, per_face


def leg_owners():
    """Invert the reader's pairing: for every report on disk, which run's window does it belong to."""
    reader = reader_module()
    owners = {}
    for run in reader.runs():
        paired = reader.browser_pair(run)
        if paired is not None:
            owners[paired.parent.name.removeprefix("browser-a11y-")] = run["stamp"]
    return owners


def legs_on_disk():
    return {p.parent.name.removeprefix("browser-a11y-") for p in (ROOT / "release-evidence").glob(
        "browser-a11y-*/report.json")}


def test_every_authority_line_cites_the_authoritys_own_leg():
    owners = leg_owners()
    certified = authority(archived_runs())["stamp"]
    problems, checked, unattributable, per_face = authority_pointer_problems(
        [p for p in DOCUMENTS if p.exists()], certified, owners.get, legs_on_disk())
    assert not problems, " | ".join(problems)
    assert checked >= 4, (f"the clause read {checked} citations on authority lines; the faces have stopped "
                          "naming a run and a leg together, so it now proves nothing")
    # A face whose authority line stops naming legs would drop out of `per_face` silently, and the clause
    # would still be green on the faces that remain. Coverage is therefore pinned per face -- over the four
    # faces that stamp a browser-report pointer (CODE_WALKTHROUGH cites no leg, so it is not in scope).
    missing = sorted({p.name for p in FACES} - set(per_face))
    assert not missing, f"{missing} name no a11y leg on their authority line; the clause is blind there"
    assert unattributable <= 1, (f"{unattributable} legs cited on authority lines cannot be attributed to "
                                 "any run; that exemption was granted for the pre-window reports only")


def test_the_pointer_clause_fires_on_the_real_changelog_sentence():
    """Two live tampers of the shipped changelog, one per way a pointer can be wrong.

    The synthetic fixture that came with the first version of this clause passed while the real face was
    still invisible to it, so the control tampers the face itself -- and it now exercises both the leg whose
    owner is named nowhere on the line *and* the leg whose owner is enumerated further along the same line,
    because line-wide membership let the second one through.
    """
    owners = leg_owners()
    certified = authority(archived_runs())["stamp"]
    text = (ROOT / "docs/CHANGELOG_COST500.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if certified in l)
    named = set(RUN_STAMP.findall(line)) | {certified}
    assert certified in named, "the changelog's authority line no longer names the certified run"
    certified_leg = next((leg for leg, run in owners.items() if run == certified), None)
    assert certified_leg is not None, f"nothing pairs to acceptance-{certified}"
    elsewhere = next((leg for leg, run in owners.items() if run in named - {certified}), None)
    unlisted = next((leg for leg, run in owners.items() if run not in named), None)
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "CHANGELOG.md"
        assert text.count(certified_leg) == 1, (
            f"the swap below would hit {text.count(certified_leg)} occurrences, not one")
        face.write_text(text, encoding="utf-8")
        assert authority_pointer_problems([face], certified, owners.get, legs_on_disk())[0] == [], \
            "the shipped face is red"
        for victim, why in ((unlisted, "pairs to a run the line never names"),
                            (elsewhere, "pairs to a run enumerated further along the same line")):
            if victim is None:
                continue
            face.write_text(text.replace(certified_leg, victim), encoding="utf-8")
            problems, checked, _, _ = authority_pointer_problems(
                [face], certified, owners.get, legs_on_disk())
            assert checked >= 1 and any(victim in p and certified in p for p in problems), (
                f"swapping the authority's leg for browser-a11y-{victim} ({why}) did not fire: {problems}")


def test_the_pointer_clause_reads_the_two_accepted_shapes():
    owners = {"20260928T113020Z": "20260928T111210Z", "20260926T094118Z": "20260926T093142Z"}
    disk = {"20260928T113020Z", "20260926T094118Z", "20260926T074554Z"}
    certified = "20260928T111210Z"
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "NOTES.md"
        # A comparison of two authorities on one line: both legs are attributed, so neither is a problem.
        face.write_text("权威运行 acceptance-20260928T111210Z 的报告 `browser-a11y-20260928T113020Z`，"
                        "上一轮 acceptance-20260926T093142Z 的报告 `browser-a11y-20260926T094118Z`\n",
                        encoding="utf-8")
        assert authority_pointer_problems([face], certified, owners.get, disk)[:3] == ([], 2, 0), (
            "the clause refuted a line that attributes every leg it names")
        # A pre-window report: nothing pairs to it, so it is counted as unattributable, not as a violation.
        face.write_text("权威运行 acceptance-20260928T111210Z 另见旧记录 `browser-a11y-20260926T074554Z`\n",
                        encoding="utf-8")
        assert authority_pointer_problems([face], certified, owners.get, disk)[:3] == ([], 1, 1), (
            "the clause either fired on, or silently dropped, an unattributable leg")
        # A cited path with no report.json behind it: that is not "pre-window", it is unverifiable.
        face.write_text("权威运行 acceptance-20260928T111210Z 另见 `browser-a11y-19990101T000000Z`\n",
                        encoding="utf-8")
        problems, checked, unattr, _ = authority_pointer_problems([face], certified, owners.get, disk)
        assert checked == 1 and unattr == 0 and any("no report.json in the archive" in p for p in problems), (
            problems)
        # A leg whose owner the line never names: that is the stale pointer.
        face.write_text("权威运行 acceptance-20260928T111210Z 的浏览器结果 "
                        "`browser-a11y-20260926T094118Z`\n", encoding="utf-8")
        problems, checked, _, _ = authority_pointer_problems([face], certified, owners.get, disk)
        assert checked == 1 and any("20260926T094118Z" in p for p in problems), problems
        # And the shape line-wide membership used to accept: the owner is named on the line, but in a
        # different clause than the leg, so the leg is still cited against the wrong authority.
        face.write_text("权威运行 acceptance-20260928T111210Z 的报告 `browser-a11y-20260926T094118Z`，"
                        "上一轮是 acceptance-20260926T093142Z\n", encoding="utf-8")
        problems, checked, _, _ = authority_pointer_problems([face], certified, owners.get, disk)
    assert checked == 1 and any("20260926T094118Z" in p for p in problems), (
        f"membership by line, not clause, let the stale pointer through: {problems}")


# ------------------------------------------------------------------ the runbook's step table keeps its shape
def md_cells(line):
    """The cell count of a Markdown table row, with escaped `\\|` protected.

    Counting raw pipes is not the same measurement: a row that carries an escaped pipe inside a cell has the
    header's pipe count while losing a real column, and a row whose cell contains a bare pipe has one more
    pipe than the header while still holding its columns. The clause is about columns, so it counts columns.
    """
    return len(line.replace("\\|", "\x00").split("|")) - 2


def table_shape_problems(path):
    """Every data row of every `| # |` table must carry its header's column count.

    The runbook is the face an operator reads while the chain is running, and its rows are written by the
    same hand that edits them: a `|` left inside a cell splits one row into five columns, which renders as a
    row whose 判据 column has gone missing. Found live this round in the row this session had just widened.
    Every header is walked, not just the first -- policing only the first table would leave a broken row in
    the next one invisible.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    problems, rows, tables = [], 0, 0
    for header, line in enumerate(lines):
        if not line.startswith("| # |"):
            continue
        tables += 1
        width = md_cells(line)
        for i in range(header + 2, len(lines)):
            if not lines[i].startswith("|"):
                break
            rows += 1
            if md_cells(lines[i]) != width:
                problems.append(f"{path.name}:{i + 1} has {md_cells(lines[i])} cells where its header has "
                                f"{width} -- a stray `|` inside a cell splits the row")
    if not tables:
        return [f"{path.name}: no `| # |` header, so the step table's shape cannot be checked"], 0
    return problems, rows


STEP_TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([a-z0-9-]+)\s*\|")


def table_order_problems(path, run_rows):
    """The step table's names and order must be the certified run's own row list.

    The shape clause proves the table is well-formed; it says nothing about whether it still describes the
    pipeline. A step renamed, inserted or reordered lands in SUMMARY, and this compares the table against
    those rows so the operator's map cannot drift from the machine's schedule.
    """
    table = [(number, name) for number, name in
             (STEP_TABLE_ROW.match(l).groups() for l in path.read_text(encoding="utf-8").splitlines()
              if STEP_TABLE_ROW.match(l))]
    problems = []
    if [n for _, n in table] != run_rows:
        problems.append(f"{path.name}: step table {table[:3]}… does not match the run's rows "
                        f"{list(zip(map(str, range(1, len(run_rows) + 1)), run_rows))[:3]}…")
    return problems, len(table)


def certified_step_rows():
    """The certified run's step names, in the order the pipeline wrote them."""
    certified = authority(archived_runs())
    text = (ROOT / "release-evidence" / f"acceptance-{certified['stamp']}" / "SUMMARY.txt").read_text(
        encoding="utf-8", errors="replace")
    return [name for name, _ in STEP_ROW.findall(text.split("STEP | RESULT", 1)[-1])]


def test_the_runbook_step_table_keeps_one_shape():
    problems, rows = table_shape_problems(RUNBOOK)
    assert not problems, " | ".join(problems)
    assert rows == 23, f"the `| # |` tables read {rows} data rows; the runbook has one 23-row step table"
    problems, counted = table_order_problems(RUNBOOK, certified_step_rows())
    assert not problems, " | ".join(problems)
    assert counted == 23, f"the step table lists {counted} numbered rows; the chain has 23 steps"


def test_a_stray_pipe_in_the_step_table_fires_the_shape_clause():
    text = RUNBOOK.read_text(encoding="utf-8")
    needle = "`python scripts/browser_a11y.py` → 取 UTC 秒两次"
    assert text.count(needle) == 1, "the control's anchor has moved"
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "RUNBOOK.md"
        face.write_text(text.replace(needle, "`python scripts/browser_a11y.py` | → 取 UTC 秒两次"),
                        encoding="utf-8")
        problems, rows = table_shape_problems(face)
    assert rows == 23 and any("cells where its header has" in p for p in problems), problems


def test_the_shape_clause_counts_columns_and_not_pipes():
    """The two shapes a raw pipe count gets wrong in opposite directions, plus the second table."""
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "T.md"
        # A bare pipe inside a cell: an extra column the pipe count alone can hide.
        face.write_text("| # | a | b |\n|---|---|---|\n| 3 | has|inner | only-two-cells-actually-three |\n",
                        encoding="utf-8")
        problems, rows = table_shape_problems(face)
        assert rows == 1 and any("cells where its header has" in p for p in problems), problems
        # An escaped pipe is content, not a delimiter: this row is well-formed and must stay quiet.
        face.write_text("| # | a | b |\n|---|---|---|\n| 3 | a \\| b | c |\n", encoding="utf-8")
        assert table_shape_problems(face) == ([], 1), "the clause read an escaped pipe as a column"
        # Every `| # |` table is policed, not just the first: the fault lives in the second one here.
        face.write_text("| # | a | b |\n|---|---|---|\n| 1 | ok | ok |\n\n"
                        "| # | a | b |\n|---|---|---|\n| 1 | lost a column\n", encoding="utf-8")
        problems, rows = table_shape_problems(face)
        assert rows == 2 and any(":7" in p for p in problems), (
            f"only the first table was walked: {problems} / rows={rows}")


def test_a_renamed_step_in_the_table_fires_the_order_clause():
    rows = certified_step_rows()
    assert len(rows) == 23, f"the certified run reported {len(rows)} rows; the chain has 23"
    text = RUNBOOK.read_text(encoding="utf-8")
    needle = f"| 22 | browser-a11y |"
    assert text.count(needle) == 1, "the order control's anchor has moved"
    with tempfile.TemporaryDirectory() as tmp:
        face = pathlib.Path(tmp) / "RUNBOOK.md"
        face.write_text(text.replace(needle, "| 22 | browser-leg-renamed |"), encoding="utf-8")
        problems, counted = table_order_problems(face, rows)
    assert counted == 23 and any("does not match the run's rows" in p for p in problems), problems


# ----------------------------------------------------------------------------------- browser-leg quotes
# The state lists a certified leg recorded, quoted in prose. Resolved through the pairing rule
# (`stamp_release_faces.browser_pair`), never "newest directory", because quoting a different leg's counts is
# exactly what this table has to be able to see.
LEG_QUOTES = (
    ("docs/TEST_REPORT.md", "走查覆盖的面板状态分四组各自计数", r"`team_states` (\d+) 个", ("team_states",)),
    ("docs/TEST_REPORT.md", "走查覆盖的面板状态分四组各自计数", r"`privacy_states` (\d+) 个", ("privacy_states",)),
    ("docs/TEST_REPORT.md", "走查覆盖的面板状态分四组各自计数", r"`second_factor_states` (\d+) 个", ("second_factor_states",)),
    ("docs/TEST_REPORT.md", "走查覆盖的面板状态分四组各自计数", r"`roster_states` (\d+) 个", ("roster_states",)),
    ("docs/RELEASE_CHECKLIST.md", "个记录是两条走查带来的",
     r"另有 (\d+) 个记录是两条走查带来的 (\d+) 个状态", ("walk_records", "walk_states")),
)


def leg_figures(report):
    """The lengths the certified leg itself recorded, plus the two counts prose derives from them.

    `walk_records` is counted off the scans the leg actually produced rather than multiplied out: the product
    form keeps reading 32 even if a leg walks both panels in one viewport only, which is the difference
    between a measured figure and an assumed one.
    """
    team = len(report["team_states"])
    privacy = len(report["privacy_states"])
    walked = set(report["team_states"]) | set(report["privacy_states"])
    records = sum(1 for scan in report["scans"] if scan.get("label") in walked)
    return {"team_states": team,
            "privacy_states": privacy,
            "roster_states": len(report["roster_states"]),
            "second_factor_states": len(report["second_factor_states"]),
            "walk_states": team + privacy,
            "walk_records": records}


def certified_leg_report():
    reader = reader_module()
    certified = authority(archived_runs())["stamp"]
    for run in reader.runs():
        if run["stamp"] == certified:
            paired = reader.browser_pair(run)
            assert paired is not None, f"acceptance-{certified} pairs to no browser leg"
            return json.loads(paired.read_text(encoding="utf-8"))
    raise AssertionError(f"acceptance-{certified} is not in the reader's run list")


def leg_problems(root, figures, rows=LEG_QUOTES):
    problems = []
    for rel, marker, pattern, keys in rows:
        path = root / rel
        if not path.exists():
            problems.append(f"{rel} is missing, so its quoted leg figures cannot be checked")
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        hits = [i for i, line in enumerate(lines) if marker in line]
        if len(hits) != 1:
            problems.append(f"{rel}: marker {marker!r} selects {len(hits)} lines, expected 1")
            continue
        found = list(re.finditer(pattern, lines[hits[0]]))
        if len(found) != 1:
            problems.append(f"{rel}: {pattern!r} matched {len(found)} times, expected 1")
            continue
        for value, key in zip(found[0].groups(), keys):
            if int(value) != figures[key]:
                problems.append(f"{rel}: says {key}={value}, the certified leg recorded {figures[key]}")
    return problems


def test_the_browser_leg_state_counts_match_the_certified_report():
    figures = leg_figures(certified_leg_report())
    problems = leg_problems(ROOT, figures)
    assert not problems, " | ".join(problems)
    assert figures["team_states"] and figures["walk_records"], (
        f"the certified leg recorded no states at all: {figures}")


def test_a_stale_leg_figure_is_named():
    """Per-row plant, so no row of this table can be silently blind."""
    report = certified_leg_report()
    figures = leg_figures(report)
    faces = sorted({rel for rel, _m, _p, _k in LEG_QUOTES})
    for number, (rel, marker, pattern, keys) in enumerate(LEG_QUOTES):
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if marker in line)
        found = re.search(pattern, lines[index])
        assert found, f"row {number} ({rel}, {marker!r}): its pattern matches nothing on its own line"
        stale = str(int(found.group(1)) + 3)
        tampered = (lines[:index] + [lines[index][:found.start(1)] + stale + lines[index][found.end(1):]]
                    + lines[index + 1:])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for face in faces:
                target = root / face
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / face).read_bytes())
            (root / rel).write_text("".join(tampered), encoding="utf-8")
            problems = leg_problems(root, figures)
        named = [p for p in problems if rel in p and f"{keys[0]}={stale}" in p]
        assert named, f"row {number} ({rel}, {marker!r}) did not name the planted stale figure: {problems}"
