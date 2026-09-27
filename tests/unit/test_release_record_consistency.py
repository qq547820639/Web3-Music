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
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
STEP_ROW = re.compile(r"^([a-z0-9-]+) \| (\w+)", re.MULTILINE)
COMMIT = re.compile(r"^git_commit=(\w{7})", re.MULTILINE)
SHORT = re.compile(r"`(\w{7})`")


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
            "rows": len(rows),
            "fails": sum(1 for _, result in rows if result == "FAIL"),
            "host_load": host.group(1) if host else None,
            "cpus": cpus.group(1) if cpus else None,
        })
    if not runs:
        raise SystemExit("no tracked acceptance evidence: the guard would be comparing prose to nothing")
    return runs


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
    newest = max(archived_runs(), key=lambda r: r["stamp"])
    assert paired, "no line in the status file pairs a run with its host readings"
    assert any(stamp == newest["stamp"] for stamp, _, _ in paired), \
        f"the certified run {newest['stamp']} is not paired with its readings: {paired}"


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
    runs = archived_runs()
    stale = mutated_status_line("**20 steps PASS", "**19 steps PASS")
    problems = problems_for(STATUS, runs, stale)
    assert any("says 19 steps pass" in p for p in problems), problems


def test_the_host_clause_fires_on_the_fourth_face():
    runs = archived_runs()
    stale = mutated_status_line('host_load="6.02 15.30 13.43"', 'host_load="22.05 22.50 23.10"')
    problems = problems_for(STATUS, runs, stale)
    assert any("says host load" in p for p in problems), problems


def test_the_repeat_figure_clause_fires_on_a_stale_count():
    """「同样 W 行绿了 M 次」 is the figure a reader takes as the size of the reproducible set.

    It read 12 while seven archived runs were 20 rows wide -- the same kind of drift as a stale commit
    list, in the one clause of the sentence that was never recomputed when the archive grew.
    """
    runs = archived_runs()
    assert problems_for(CHECKLIST, runs) == []
    stale = mutated_line(CHECKLIST, "同样 20 行绿了 7 次", "同样 20 行绿了 12 次")
    problems = problems_for(CHECKLIST, runs, stale)
    assert any("went green 12 times, the archive has 7" in p for p in problems), problems
    # ...and the clause must stay quiet when the two numbers describe the same archive differently.
    other = mutated_line(CHECKLIST, "同样 20 行绿了 7 次", "同样 19 行绿了 1 次")
    assert problems_for(CHECKLIST, runs, other) == [], problems_for(CHECKLIST, runs, other)
