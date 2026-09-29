"""Add the browser-leg state counts to the same marker-scoped discipline as the contract quotes.

The prose in `docs/TEST_REPORT.md:134` and `docs/RELEASE_CHECKLIST.md:44` states `team_states`,
`privacy_states`, `second_factor_states`, `roster_states` and the derived walk counts. Item 35 called those
"仍是手抄"; they are re-derivable now because the certified run's `report.json` is tracked, so they get rows
here. Deliberately NOT a whole-file regex sweep: the route-matcher lesson from this same round is that a
sweep judges soft-wrapped history as if it were a live claim.
"""
import pathlib

BLOCK = '''

# ----------------------------------------------------------------------------------- browser-leg quotes
# The state lists a certified leg recorded, quoted in prose. Resolved through the pairing rule
# (`stamp_release_faces.browser_pair`), never "newest directory", because quoting a different leg's counts is
# exactly what this table has to be able to see.
LEG_QUOTES = (
    ("docs/TEST_REPORT.md", "名册与邀请", r"`team_states` (\\d+) 个", ("team_states",)),
    ("docs/TEST_REPORT.md", "主体权利", r"`privacy_states` (\\d+) 个", ("privacy_states",)),
    ("docs/TEST_REPORT.md", "两步验证", r"`second_factor_states` (\\d+) 个", ("second_factor_states",)),
    ("docs/TEST_REPORT.md", "管理侧名册", r"`roster_states` (\\d+) 个", ("roster_states",)),
    ("docs/RELEASE_CHECKLIST.md", "个记录是两条走查带来的",
     r"另有 (\\d+) 个记录是两条走查带来的 (\\d+) 个状态", ("walk_records", "walk_states")),
)


def leg_figures(report):
    """The lengths the certified leg itself recorded, plus the two counts prose derives from them."""
    team = len(report["team_states"])
    privacy = len(report["privacy_states"])
    return {"team_states": team,
            "privacy_states": privacy,
            "roster_states": len(report["roster_states"]),
            "second_factor_states": len(report["second_factor_states"]),
            "walk_states": team + privacy,
            "walk_records": (team + privacy) * len({s.get("viewport") for s in report["scans"]})}


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
'''

path = pathlib.Path("tests/unit/test_release_record_consistency.py")
text = path.read_text(encoding="utf-8")
assert "LEG_QUOTES" not in text
assert "def test_the_browser_leg_state_counts" not in text
if not text.endswith("\n"):
    text += "\n"
path.write_text(text + BLOCK, encoding="utf-8")
print("appended leg-quote table; file now", len((text + BLOCK).splitlines()), "lines")
