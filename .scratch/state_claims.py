
# The four browser state lists and the two derived walk counts were the last readings the record itself
# called "仍是手抄": prose that quotes `team_states` / `privacy_states` / `second_factor_states` /
# `roaster_states` lengths while nothing recomputed them. They are re-derivable now that the certified run's
# `report.json` is tracked, so each claim gets a reader here instead of another hand-counted sentence. The
# leg is resolved through the pairing rule (`stamp_release_faces.browser_pair`), not by "newest directory",
# because a face quoting the wrong leg's counts is exactly the failure this clause has to be able to see.
STATE_CLAIM = (
    ("team_states", re.compile(r"`team_states` (\d+) 个"), lambda rep: len(rep["team_states"])),
    ("privacy_states", re.compile(r"`privacy_states` (\d+) 个"), lambda rep: len(rep["privacy_states"])),
    ("second_factor_states", re.compile(r"`second_factor_states` (\d+) 个"),
     lambda rep: len(rep["second_factor_states"])),
    ("roster_states", re.compile(r"`roster_states` (\d+) 个"), lambda rep: len(rep["roster_states"])),
    ("walk states", re.compile(r"两条走查带来的 (\d+) 个状态"),
     lambda rep: len(rep["team_states"]) + len(rep["privacy_states"])),
    ("walk records", re.compile(r"另有 (\d+) 个记录是两条走查带来的"),
     lambda rep: 2 * (len(rep["team_states"]) + len(rep["privacy_states"]))),
)


def certified_browser_report():
    import json
    reader = reader_module()
    certified = authority(archived_runs())["stamp"]
    for run in reader.runs():
        if run["stamp"] != certified:
            continue
        paired = reader.browser_pair(run)
        if paired is None:
            raise AssertionError(f"acceptance-{certified} pairs to no browser leg; the state clause has "
                                 "nothing to read")
        return json.loads(paired.read_text(encoding="utf-8"))
    raise AssertionError(f"acceptance-{certified} is not in the reader's run list")


def state_claim_problems(paths, report, scope=None):
    problems = []
    for name, pattern, want in STATE_CLAIM:
        if scope is not None and name not in scope:
            continue
        seen = 0
        target = want(report)
        for path in paths:
            for found in pattern.finditer(path.read_text(encoding="utf-8")):
                seen += 1
                if int(found.group(1)) != target:
                    problems.append(f"{path.name} states {name} as {found.group(1)}; the certified leg "
                                    f"reports {target}")
        if not seen:
            problems.append(f"{name}: no document states it; the clause is blind on its own denominator")
    return problems


def test_the_browser_state_counts_match_the_certified_leg():
    report = certified_browser_report()
    problems = state_claim_problems([p for p in DOCUMENTS if p.exists()], report)
    assert not problems, "\n".join(problems)


def test_each_state_claim_fires_on_its_own_number(tmp_path):
    """Per-claim minimal pair, so widening the table cannot leave a silent row behind.

    A new row that never matches is indistinguishable from a new row that agrees: it produces readings, not
    reds. Each claim therefore gets its own retraction -- the real sentence green, the same sentence with
    that claim's own digit moved red.
    """
    report = certified_browser_report()
    docs = [p for p in DOCUMENTS if p.exists()]
    for name, pattern, want in STATE_CLAIM:
        line = next(l for p in docs for l in p.read_text(encoding="utf-8").splitlines() if pattern.search(l))
        fixture = tmp_path / f"{name.replace('_', '-')}.md"
        fixture.write_text(line + "\n", encoding="utf-8")
        assert state_claim_problems([fixture], report, scope=[name]) == [], name
        moved = pattern.sub(lambda m: m.group(0).replace(m.group(1), str(want(report) + 1)), line, count=1)
        fixture.write_text(moved + "\n", encoding="utf-8")
        red = state_claim_problems([fixture], report, scope=[name])
        assert len(red) == 1 and f"reports {want(report)}" in red[0], (name, red)
