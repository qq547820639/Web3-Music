"""Per-row arms for the contract-quote table, and retirement of the parallel matcher.

The reviewer reproduced that a table with 16 rows but a single tampered row (CONTRACT_QUOTES[0]) leaves 15
rows unproven; and that the parallel matcher in test_reference_doc_figures.py judged history because it
globbed every docs/*.md. Both are fixed here: every row gets its own must-fire, and the duplicate matcher
is deleted so each figure has exactly one derivation and one owner.
"""
import pathlib
import sys

TESTS = pathlib.Path("tests/unit/test_release_record_consistency.py")
FIGURES = pathlib.Path("tests/unit/test_reference_doc_figures.py")

NEW_ARM = '''def test_a_stale_contract_figure_is_named():
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
            for source in faces + [MATRIX, OPENAPI]:
                target = root / source
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / source).read_bytes())
            problems = contract_problems(root, figures)
        named = [p for p in problems if rel in p and f"{keys[0]}={stale}" in p]
        assert named, f"row {number} ({rel}, {marker!r}) did not name the planted stale figure: {problems}"
'''

OLD_ARM_START = "def test_a_stale_contract_figure_is_named():"
OLD_ARM_END = "def test_the_contract_quoting_census_is_not_vacuous():"

t = TESTS.read_text(encoding="utf-8")
start = t.index(OLD_ARM_START)
end = t.index(OLD_ARM_END)
t = t[:start] + NEW_ARM + "\n\n" + t[end:]
TESTS.write_text(t, encoding="utf-8")
print("arm test rewritten per-row; file lines:", len(t.splitlines()))

f = FIGURES.read_text(encoding="utf-8")
cut_from = "# The authority matrix is derived by `scripts/authority_matrix.py` into a tracked JSON"
cut_to = "# Unambiguous citations"
if cut_from in f and cut_to in f:
    i = f.index(cut_from)
    j = f.index(cut_to)
    removed = f[i:j]
    f = f[:i] + f[j:]
    FIGURES.write_text(f, encoding="utf-8")
    print("retired parallel matcher:", len(removed.splitlines()), "lines removed;",
          "ROUTE_CLAIM still referenced:", "ROUTE_CLAIM" in f, "FAMILIES still referenced:", "FAMILIES" in f)
else:
    print("ABORT: retirement anchors not found", cut_from in f, cut_to in f)
    sys.exit(1)
