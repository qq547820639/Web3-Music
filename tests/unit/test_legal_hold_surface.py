"""Denominator guard for `legal_hold`: it pins WHERE the rights-preservation flag is read, not WHAT a
hold blocks.

The behaviour half lives elsewhere and is written against a live stack (scripts/report_drill.py, plus
the drill being authored in parallel). This file needs no database, no network and no container, and it
answers only one question: has the set of places that read the flag changed? A new reader -- a seventh
SQL predicate, a fourth view, a router that starts checking the column, a contract that grows the field
-- turns this file red, and the failure message tells the author to teach the drill about the new
reader instead of letting the new reader go untested.

Measured baseline this file encodes (every location re-opened by hand before writing the assertion):

  * two carriers, nowhere else:
      db/migrations/002_creation_asset_market_os.sql  the only column, inside CREATE TABLE moderation_cases
      db/migrations/001_production_candidate.sql      the only enum member, song_projects.status CHECK
  * eight SQL reads: 002 (marketplace_offers_public's two filters, reserve_marketplace_offer's two
    refusals), 004 and 009 (prepare_brand_award's two refusals each -- 009 is the live body);
  * python/js: services/api/app/routers/assets.py, services/api/app/domain/commerce.py,
    services/admin/admin.js, services/web/app.js; the marketplace's three assert_offer_rights call sites
    in services/api/app/routers/market.py read the flag only *through* commerce.assert_offer_rights, so
    they carry no literal and are pinned separately;
  * sixteen application touches in routers/assets.py and domain/commerce.py, three in the frontends,
    four scripts, three acceptance specs, and the contract copies under shared/contracts. The live
    drill (scripts/hold_drill.py) is exempted from the per-line census -- it tests the readers instead
    of being one -- but only as a named file set, so a new script still shows up as an addition.

Every census below is compared symmetrically, so a reader that *disappears* fails as loudly as one that
appears. Every pattern is fed one sample that must match and one that must not, in this file, because a
needle that silently matches nothing reads exactly like a clean tree -- test_the_census_sees_a_new_reader
then re-runs the real predicate against a planted read to prove the whole guard has teeth.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/migrations"

# The four directories the application itself lives in, plus the drill/operator scripts.
APP_DIRS = ("services/api", "services/admin", "services/web")
SCRIPT_DIR = "scripts"
ACCEPTANCE_DIR = "services/acceptance"
CONTRACT_DIR = "shared/contracts"

# ------------------------------------------------------------------ the flag's two spellings ----
# The identifier itself. Word boundaries on both sides, because `legal_hold` inside
# `legal_hold_reason` would otherwise be counted as the same reader.
LEGAL_HOLD_TOKEN = re.compile(r"(?<![0-9A-Za-z_])legal_hold(?![0-9A-Za-z_])")

# The bare word. It is deliberately *not* the flag: in this repository an unqualified `hold` is the
# money path (`credit_holds`, `hold_id`, `hold_status`) or English prose ("they hold cross-tenant
# authority"), so a substring search for `hold` would fire on all of it. Requiring a non-word
# character on each side excludes `credit_holds`, `hold_id`, `threshold` and `household` alike.
HOLD_WORD = re.compile(r"(?<![0-9A-Za-z_])hold(?![0-9A-Za-z_])")
WORD_CHARS = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")

# Carrier 1: the column. Deliberately only the shape `legal_hold <boolean type>` at the start of a line
# inside a CREATE TABLE, so that it cannot swallow a read written on its own line.
HOLD_COLUMN_DEFINITION = re.compile(r"^[ \t]*legal_hold[ \t]+(?:boolean|bool)\b", re.MULTILINE)
# Carrier 1's other possible spelling, pinned to zero: a later ALTER TABLE that puts the column on a
# second table would double the carriers without matching the definition regex above.
HOLD_COLUMN_ADDED = re.compile(
    r"ADD[ \t]+COLUMN[ \t]+(?:IF[ \t]+NOT[ \t]+EXISTS[ \t]+)?legal_hold\b", re.IGNORECASE)
# Carrier 2: the enum member. It is a *value* of song_projects.status, not a column, and the CHECK
# clause around it is what distinguishes it from a read that compares the status.
PROJECT_STATUS_ENUM_MEMBER = re.compile(
    r"CHECK[ \t]*\([ \t]*status[ \t]+IN[ \t]*\([^)]*'legal_hold'[^)]*\)", re.IGNORECASE)

CARRIER_PATTERNS = (HOLD_COLUMN_DEFINITION, HOLD_COLUMN_ADDED, PROJECT_STATUS_ENUM_MEMBER)

# The migrations by repo-relative path, spelled out because the baseline locations below name them and
# a reader that starts looking like a carrier (or the reverse) must be reported as both a lost reader
# and a gained carrier rather than silently moving.
MIG_002 = "db/migrations/002_creation_asset_market_os.sql"
MIG_004 = "db/migrations/004_brand_award_commercial_flow.sql"
MIG_009 = "db/migrations/009_prepare_brand_award_ambiguous_column.sql"
MIG_001 = "db/migrations/001_production_candidate.sql"
MIG_021 = "db/migrations/021_public_rights_report.sql"

# The eight SQL reads. Two statements each contribute *two* locations, not one: inside
# marketplace_offers_public the manifest read (568) and the case-table read (574) are two separate
# predicates with separate semantics (a JSON key filter and an EXISTS filter), and ditto for
# reserve_marketplace_offer's two refusals. Collapsing per statement would hide the loss of one half.
BASELINE_SQL_READERS = frozenset({
    (MIG_002, 568),   # marketplace_offers_public: COALESCE((r.manifest->>'legal_hold')::boolean,false)=false
    (MIG_002, 574),   # marketplace_offers_public: NOT EXISTS (... (c.legal_hold OR c.status IN (...)))
    (MIG_002, 675),   # reserve_marketplace_offer: the manifest refusal's condition
    (MIG_002, 680),   # reserve_marketplace_offer: the moderation-case refusal's condition
    (MIG_004, 25),    # prepare_brand_award (superseded body): manifest condition
    (MIG_004, 30),    # prepare_brand_award (superseded body): moderation-case condition
    (MIG_009, 26),    # prepare_brand_award (LIVE body): manifest condition
    (MIG_009, 31),    # prepare_brand_award (LIVE body): moderation-case condition
})

# What the application code touches. Keyed (repo-relative posix path, 1-based line).
BASELINE_APP_TOUCHES = frozenset({
    # services/api/app/routers/assets.py -- three request models carry the field, one list query reads
    # it as a display column, the reviewer write blocks capabilities, and the case routes write both
    # the column and the project-status cascade.
    ("services/api/app/routers/assets.py", 31),    # class RightsReview: legal_hold: bool = False
    ("services/api/app/routers/assets.py", 42),    # class CaseCreate
    ("services/api/app/routers/assets.py", 48),    # class CaseUpdate
    ("services/api/app/routers/assets.py", 77),    # EXISTS(...) AS legal_hold -- display column
    ("services/api/app/routers/assets.py", 174),   # if body.legal_hold: -> blocks capability reasons
    ("services/api/app/routers/assets.py", 204),   # the manifest JSON body it writes
    ("services/api/app/routers/assets.py", 225),   # audit payload of the issued manifest
    ("services/api/app/routers/assets.py", 243),   # INSERT INTO moderation_cases(... legal_hold ...)
    ("services/api/app/routers/assets.py", 244),   # that INSERT's bound value
    ("services/api/app/routers/assets.py", 247),   # if body.legal_hold and subject_type == "project"
    ("services/api/app/routers/assets.py", 248),   # UPDATE song_projects SET status='legal_hold'
    ("services/api/app/routers/assets.py", 257),   # UPDATE moderation_cases SET ... legal_hold=%s
    ("services/api/app/routers/assets.py", 258),   # that UPDATE's bound value
    ("services/api/app/routers/assets.py", 264),   # next_status = "legal_hold" if body.legal_hold
    ("services/api/app/routers/assets.py", 266),   # audit payload of the case update
    # services/api/app/domain/commerce.py -- the one place Python refuses a hold.
    ("services/api/app/domain/commerce.py", 48),   # assert_offer_rights: manifest.get("legal_hold")
    # The two frontends only *display* it, and each display is a promise a user can see.
    ("services/admin/admin.js", 402),              # the case list's "LEGAL HOLD" cell
    ("services/web/app.js", 750),                   # the project status tab 法务冻结 / legal_hold
    ("services/web/app.js", 1370),                  # a manifest payload the browser sends with the field
})

# scripts/ is its own bucket: report_drill.py and reconcile_market.py legitimately *set* the flag in
# fixtures, and architecture-audit.py keeps it in a needle list. A new hit here is a new drill site or
# a new tool, which is a different conversation from a new application reader.
BASELINE_SCRIPT_TOUCHES = frozenset({
    ("scripts/architecture-audit.py", 60),
    # 87 / 273 rather than 86 / 272: `import metric_line as metrics` went in above each hit when the
    # operator tools started emitting their own readings, which is a shift of the same needle, not a new
    # reader. A new *reader* would belong to the application bucket and need a drill assertion.
    ("scripts/reconcile_market.py", 87),
    ("scripts/report_drill.py", 273),
    ("scripts/reservation_race.py", 110),
})

# The live drill, exempted from the per-line census on purpose. It *tests* the readers; it is not one
# of them, and it is being written while this file runs: pinning its line numbers would fire on every
# keystroke in the drill while telling nobody anything about the denominator. What stays pinned is the
# exemption list itself -- a new script or drill that mentions the flag still turns the bucket red, and
# a drill that stops mentioning it at all fails too (see the assertions in test 3).
LIVE_DRILL_FILES = frozenset({"scripts/hold_drill.py"})

# The acceptance specs set the flag in their own fixtures (they are the other half of "legitimately
# mentions it"). Outside the four directories the brief names, so pinned here rather than left blind.
BASELINE_ACCEPTANCE_TOUCHES = frozenset({
    ("services/acceptance/test_commercial.py", 77),
    ("services/acceptance/test_tenant_isolation.py", 167),
    ("services/acceptance/test_tenant_isolation.py", 257),
})

# The wire contract. openapi-v13 exists as both YAML and JSON, and the JSON copy is not a build
# artefact of the YAML one here -- both are checked in, so both are denominators.
BASELINE_CONTRACT_TOUCHES = frozenset({
    ("shared/contracts/openapi-v13.yaml", 4591),
    ("shared/contracts/openapi-v13.yaml", 4611),
    ("shared/contracts/openapi-v13.yaml", 5229),
    ("shared/contracts/openapi-v13.json", 8157),
    ("shared/contracts/openapi-v13.json", 8183),
    ("shared/contracts/openapi-v13.json", 9121),
    # design-reference schema: the flag survives as a member of the manifest status enum.
    ("shared/contracts/design-reference/rights-manifest-v2.schema.json", 200),
})

# commerce.assert_offer_rights is the only Python reader with teeth; market.py reaches it three times.
# Those three sites carry no literal, so the literal census cannot see them and this pins them by name.
ASSERT_OFFER_RIGHTS_SITE_COUNT = 3

RIGHTS_PY = "services/api/app/domain/rights.py"

# The measured refusal words, quoted verbatim from the migration text (they appear nowhere else: no
# prose document in docs/ quotes them, so the SQL is the only source and is pinned as-is).
RESERVE_MANIFEST_REFUSAL = "offer rights are not licensable"
RESERVE_CASE_REFUSAL = "asset is restricted"
AWARD_MANIFEST_REFUSAL = "submission rights are not licensable"
AWARD_CASE_REFUSAL = "submission asset is restricted"


# ------------------------------------------------------------------ helpers ----

def read(rel_or_abs: pathlib.Path) -> str:
    return pathlib.Path(rel_or_abs).read_text(encoding="utf-8")


def rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


def line_of(text: str, offset: int) -> int:
    """1-based line number, derived at run time; never stored, only reported."""
    return text.count("\n", 0, offset) + 1


def blank_sql_comments(text: str) -> str:
    """Replace `--` and block comments with spaces, keeping every byte count and newline.

    Byte-for-byte length preservation is what lets offsets found here be reported as line numbers in
    the original file. The string-literal skip matters: `'seed','hold','settle'` and a migration
    comment that contains an apostrophe both sit in real text here, and a `--` inside a literal would
    otherwise blank the rest of a line that carries a read.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                if text[j] == "\n":
                    break
                j += 1
            i = j
        else:
            i += 1
    return "".join(out)


def word_run_at(text: str, start: int, end: int) -> str:
    """The whole word a match sits in -- how `credit_holds` is told apart from a bare `hold`."""
    s = start
    while s > 0 and text[s - 1] in WORD_CHARS:
        s -= 1
    e = end
    while e < len(text) and text[e] in WORD_CHARS:
        e += 1
    return text[s:e]


def migration_texts(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Every migration's raw text keyed by repo-relative path, with in-memory replacements applied.

    The override is what lets the planted-reader control below run the *same* predicate over a mutated
    tree without writing to disk.
    """
    texts = {rel(p): read(p) for p in sorted(MIGRATIONS.glob("*.sql"))}
    for key, value in (overrides or {}).items():
        assert key in texts, f"{key} is not a migration in this tree: {sorted(texts)}"
        texts[key] = value
    return texts


def carrier_spans(stripped: str) -> list[tuple[int, int]]:
    return [m.span() for rx in CARRIER_PATTERNS for m in rx.finditer(stripped)]


def sql_reader_locations(texts: dict[str, str]) -> set[tuple[str, int]]:
    """THE predicate. A SQL read of the flag is a `legal_hold` identifier that is comment code, is not
    inside a carrier clause, and therefore is somebody looking the flag up.

    Used verbatim by both the census test and the planted-reader control. Changing what counts as a
    read here changes both, which is the point.
    """
    found: set[tuple[str, int]] = set()
    for path, raw in texts.items():
        stripped = blank_sql_comments(raw)
        carriers = carrier_spans(stripped)
        for match in LEGAL_HOLD_TOKEN.finditer(stripped):
            if any(begin <= match.start() < finish for begin, finish in carriers):
                continue
            found.add((path, line_of(stripped, match.start())))
    return found


def create_table_block(text: str, table: str) -> str:
    """The `CREATE TABLE <table> (...)` statement, found by name and not by line."""
    stripped = blank_sql_comments(text)
    head = re.search(r"CREATE[ \t]+TABLE[ \t]+(?:IF[ \t]+NOT[ \t]+EXISTS[ \t]+)?"
                     rf"(?:[a-z_]+\.)?{table}\b", stripped, re.IGNORECASE)
    assert head, f"no CREATE TABLE {table} in this text"
    end = stripped.find(");", head.start())
    assert end > head.start(), f"CREATE TABLE {table} is never closed"
    return stripped[head.start():end + 2]


def view_definition(text: str, name: str) -> str:
    """The `CREATE [OR REPLACE] VIEW <name> ... ;` statement, found by name and not by line.

    The end is the first `;` that closes the statement, which is also the anchor the planted-reader
    control below injects into -- so the control injects into a statement this helper says exists.
    """
    stripped = blank_sql_comments(text)
    head = re.search(r"CREATE[ \t]+(?:OR[ \t]+REPLACE[ \t]+)?VIEW[ \t]+(?:[a-z_]+\.)?"
                     + re.escape(name) + r"\b", stripped, re.IGNORECASE)
    assert head, f"no CREATE VIEW {name} in this text"
    end = stripped.find(";", head.start())
    assert end > head.start(), f"CREATE VIEW {name} is never closed"
    return stripped[head.start():end + 1]


def function_body(text: str, name: str) -> str | None:
    """A plpgsql function's `CREATE OR REPLACE ... $$;` text, found by name and not by line."""
    stripped = blank_sql_comments(text)
    head = re.search(r"CREATE[ \t]+OR[ \t]+REPLACE[ \t]+FUNCTION[ \t]+" + re.escape(name) + r"\b",
                     stripped, re.IGNORECASE)
    if head is None:
        return None
    end = stripped.find("$$;", head.start())
    assert end > head.start(), f"{name} is defined but has no body terminator"
    return stripped[head.start():end + 3]


def function_definitions(texts: dict[str, str], name: str) -> dict[str, str]:
    """Every migration that (re)defines `name`, so the replacement chain is visible as a set."""
    found = {}
    for path, raw in texts.items():
        body = function_body(raw, name)
        if body is not None:
            found[path] = body
    return found


def refusals_after_each_read(body: str) -> list[tuple[int, str | None]]:
    """For every read inside a function body, the first RAISE EXCEPTION in the enclosing IF block.

    The window is the body text from the read to the next `END IF;`, which is the refusal the read
    actually guards. Reads outside an IF (a view's WHERE clause) yield None and are the filter case,
    not the refusal case -- this is why the view contributes nothing here.
    """
    found = []
    for match in LEGAL_HOLD_TOKEN.finditer(body):
        stop = body.find("END IF;", match.end())
        window = body[match.end():stop + 7] if stop >= 0 else body[match.end():]
        raised = re.search(r"RAISE[ \t]+EXCEPTION[ \t]+'([^']*)'", window, re.IGNORECASE)
        found.append((line_of(body, match.start()), raised.group(1) if raised else None))
    return found


def source_texts(dirs: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for directory in dirs:
        base = ROOT / directory
        assert base.is_dir(), f"{directory} is not a directory in this tree"
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts or ".venv" in path.parts:
                continue
            try:
                found[rel(path)] = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    assert found, "the source walk found no files at all, so no negative reading below means anything"
    return found


def token_lines(text: str) -> set[int]:
    return {line_of(text, m.start()) for m in LEGAL_HOLD_TOKEN.finditer(text)}


def python_function_source(source: str, name: str) -> str:
    """A def's own text, found by name and read to the next same-or-shallower line.

    Used to state that `rights.py`'s capability reader is hold-blind: the claim has to be about that
    function, and a line number would make the claim about a position in a file instead.
    """
    lines = source.splitlines(keepends=True)
    start = None
    indent = ""
    for index, line in enumerate(lines):
        found = re.match(r"^(\s*)def\s+" + re.escape(name) + r"\s*\(", line)
        if found:
            start, indent = index, found.group(1)
            break
    assert start is not None, f"no def {name} in this source"
    collected = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith((indent + " ", indent + "\t")):
            break
        collected.append(line)
    return "".join(collected)


def file_locations(texts: dict[str, str]) -> set[tuple[str, int]]:
    return {(path, line) for path, text in texts.items() for line in token_lines(text)}


def describe(locations: set[tuple[str, int]]) -> str:
    return " | ".join(f"{path}:{line}" for path, line in sorted(locations)) or "(none)"


def symmetric(actual: set, expected: frozenset, label: str, action: str) -> None:
    added, removed = actual - expected, expected - actual
    assert not added and not removed, (
        f"{label} no longer matches the measured baseline.\n"
        f"  readers ADDED that this file does not know ({len(added)}): {describe(added)}\n"
        f"  readers REMOVED that this file still expects ({len(removed)}): {describe(removed)}\n"
        f"  Do this: {action}"
    )


TEACH_THE_DRILL = (
    "a place that reads legal_hold has changed identity. If you ADDED a reader, this platform now "
    "freezes something new: teach the live drill (scripts/report_drill.py and its companion test) to "
    "assert what the new reader blocks, then add the location here with a comment saying what it "
    "guards. If you REMOVED one, the drill must stop asserting it blocked anything, and the carrier "
    "count test above must still pass. Do not just widen the baseline set to make this green."
)


# ------------------------------------------------------------------ 1. the two carriers ----

def test_the_flag_is_still_written_in_exactly_two_carriers():
    """One column and one enum member, no more and no less -- counts are exact, not `>=`.

    A hold can only be carried in this schema as moderation_cases' boolean column or as a value of
    song_projects.status. A third carrier means a third table can now be frozen, which no drill covers.
    """
    # In-file proof that both carrier patterns fire, and on what they must not fire.
    firing = "CREATE TABLE t (\n  legal_hold boolean NOT NULL DEFAULT false,\n);\n"
    assert len(HOLD_COLUMN_DEFINITION.findall(firing)) == 1, "the column carrier pattern matches nothing"
    assert not HOLD_COLUMN_DEFINITION.findall("SELECT legal_hold FROM t WHERE c.legal_hold;"), \
        "the column carrier pattern matches a read -- the census below would then lose readers"
    enum_firing = "status text NOT NULL CHECK(status IN ('active','archived','legal_hold')),"
    assert len(PROJECT_STATUS_ENUM_MEMBER.findall(enum_firing)) == 1, "the enum carrier matches nothing"
    assert not PROJECT_STATUS_ENUM_MEMBER.findall("UPDATE song_projects SET status='legal_hold'"), \
        "the enum carrier matches a write of the status, which is a reader-side event"
    alter_firing = "ALTER TABLE asset_offers ADD COLUMN legal_hold boolean NOT NULL DEFAULT false;"
    assert len(HOLD_COLUMN_ADDED.findall(alter_firing)) == 1, "the ADD COLUMN carrier matches nothing"
    assert not HOLD_COLUMN_ADDED.findall("ALTER TABLE asset_offers ADD COLUMN status text;")

    texts = migration_texts()
    stripped = {path: blank_sql_comments(raw) for path, raw in texts.items()}

    columns = {(path, line_of(stripped[path], m.start()))
               for path in stripped for m in HOLD_COLUMN_DEFINITION.finditer(stripped[path])}
    assert len(columns) == 1, (
        f"the column carrier is now spelled {len(columns)} times: {describe(columns)}. There must be "
        "exactly one (moderation_cases). A second boolean column means a second table holds freezes; "
        "either fold it into moderation_cases or teach the drill about the new carrier."
    )
    added = {(path, line_of(stripped[path], m.start()))
             for path in stripped for m in HOLD_COLUMN_ADDED.finditer(stripped[path])}
    assert not added, f"a migration now ADDs a legal_hold column: {describe(added)} -- second carrier"

    column_path, column_line = next(iter(columns))
    assert column_path == MIG_002, f"the column carrier moved from 002 to {column_path}"
    column_text = read(ROOT / column_path)
    block = create_table_block(column_text, "moderation_cases")
    assert LEGAL_HOLD_TOKEN.search(block), "the one legal_hold column is not inside moderation_cases"
    assert "legal_hold" in block and "case_type" in block, \
        "the CREATE TABLE moderation_cases extraction returned a stub, so containment proves nothing"
    definition_line = column_text.splitlines()[column_line - 1].strip()
    assert definition_line == "legal_hold boolean NOT NULL DEFAULT false,", (
        f"the carrier's shape changed at {column_path}:{column_line}: {definition_line!r}. "
        "`NOT NULL DEFAULT false` is what makes an absent case read as un-frozen; if that changed, "
        "the drill's premise changed with it."
    )

    enums = {(path, line_of(stripped[path], m.start()))
             for path in stripped for m in PROJECT_STATUS_ENUM_MEMBER.finditer(stripped[path])}
    assert len(enums) == 1, (
        f"the status enum carrier is now spelled {len(enums)} times: {describe(enums)}. Exactly one "
        "was measured (song_projects.status in 001); a second means a second table's status can say "
        "legal_hold and nothing in the drill reads that table."
    )
    enum_path, _ = next(iter(enums))
    assert enum_path == MIG_001, f"the enum carrier moved from 001 to {enum_path}"
    enum_block = create_table_block(read(ROOT / enum_path), "song_projects")
    assert PROJECT_STATUS_ENUM_MEMBER.search(enum_block), \
        "the only status enum carrying legal_hold is not song_projects.status"


# ------------------------------------------------------------------ 2. the reader census ----

def test_the_reader_set_is_the_measured_one():
    """The eight SQL reads of the flag, as (file, line) -- compared symmetrically.

    This is the denominator the live drill measures its numerator against. It is a census, not a
    behaviour claim: nothing here says a hold blocks anything.
    """
    actual = sql_reader_locations(migration_texts())
    assert actual, (
        "the SQL reader census found no read of legal_hold anywhere. Either the flag has stopped "
        "being consulted in SQL entirely, or the predicate has stopped seeing it -- re-run "
        "test_the_census_sees_a_new_reader before believing a green here."
    )
    symmetric(actual, BASELINE_SQL_READERS, "SQL reads of legal_hold", TEACH_THE_DRILL)


def test_the_census_sees_a_new_reader():
    """Must-fire control. Without it, the census above is indistinguishable from a file read.

    The same predicate, fed the same real 002 text plus one planted read, must report exactly that one
    location and nothing else -- in both directions.
    """
    pristine = migration_texts()
    baseline = sql_reader_locations(pristine)
    original = pristine[MIG_002]

    # Non-vacuity of the injection anchors: both targets are real statements in the tree, and neither
    # currently reads the flag -- otherwise the "addition" below could be an already-counted line.
    anchor_view = view_definition(original, "brand_briefs_public")
    assert not sql_reader_locations({MIG_002: anchor_view}), (
        "brand_briefs_public already reads the flag, so injecting into it no longer proves the "
        "census can see a new reader -- pick a statement that is clean today"
    )
    assert function_body(original, "zz_hold_probe") is None, \
        "002 already defines zz_hold_probe, so the planted function is not new"

    # (a) planted read inside an existing CREATE VIEW's WHERE clause, written on the anchor's own line
    # so it cannot shift any later location: the census must report exactly one addition and no loss.
    anchor = "WHERE b.status='open';"
    anchor_line = line_of(original, original.index(anchor))
    marker_a = "hz_probe_case"
    same_line = (f"WHERE b.status='open' AND NOT EXISTS "
                 f"(SELECT 1 FROM moderation_cases {marker_a} WHERE {marker_a}.legal_hold);")
    patched_view = original.replace(anchor, same_line, 1)
    assert patched_view != original, "the injection anchor matched nothing, so nothing was planted"
    texts_a = migration_texts({MIG_002: patched_view})
    expected_line_a = line_of(texts_a[MIG_002], texts_a[MIG_002].index(marker_a))
    assert expected_line_a == anchor_line, "the planted read moved lines, so (a) is not a clean control"
    found_a = sql_reader_locations(texts_a)
    assert found_a - baseline == {(MIG_002, expected_line_a)}, (
        f"the census did not report the planted view filter as the only addition "
        f"(planted line {expected_line_a}, additions were {describe(found_a - baseline)})"
    )
    assert baseline - found_a == set(), "the injection made real readers vanish"

    # (b) planted read in a brand-new function body appended at the end of the file.
    marker_b = "zz_probe_case"
    patched_new = (original + "\nCREATE OR REPLACE FUNCTION zz_hold_probe(t uuid) RETURNS boolean\n"
                   "LANGUAGE plpgsql AS $$\nBEGIN\n"
                   f"  RETURN EXISTS(SELECT 1 FROM moderation_cases {marker_b} WHERE {marker_b}.legal_hold);\n"
                   "END $$;\n")
    texts_b = migration_texts({MIG_002: patched_new})
    expected_line_b = line_of(texts_b[MIG_002], texts_b[MIG_002].index(marker_b))
    found_b = sql_reader_locations(texts_b)
    assert found_b - baseline == {(MIG_002, expected_line_b)}, (
        f"the census did not report the planted function as the only addition "
        f"(planted line {expected_line_b}, additions were {describe(found_b - baseline)})"
    )
    assert baseline - found_b == set()

    # (c) the pristine tree reports neither planting, and the two controls are not confusable.
    assert baseline == BASELINE_SQL_READERS, "the control ran against a tree the census test also rejects"
    assert (MIG_002, expected_line_a) not in baseline and (MIG_002, expected_line_b) not in baseline
    assert expected_line_a != expected_line_b

    # (d) what a *real* edit looks like: an injection that adds a line shifts every later location in
    # the same file, so the census reports the planted read plus a move, not just an addition. Pinned
    # here so nobody reads that pair of "removed" lines as a deleted reader.
    shifted = original.replace(anchor, "WHERE b.status='open'\n  " + same_line[len("WHERE b.status='open' "):], 1)
    texts_d = migration_texts({MIG_002: shifted})
    planted_d = line_of(texts_d[MIG_002], texts_d[MIG_002].index(marker_a))
    found_d = sql_reader_locations(texts_d)
    after_planted = sorted(line for path, line in baseline
                           if path == MIG_002 and line > planted_d)
    assert found_d - baseline == {(MIG_002, planted_d)} | {(MIG_002, line + 1) for line in after_planted}
    assert baseline - found_d == {(MIG_002, line) for line in after_planted}, (
        "an inserted line no longer shifts the later locations, so this census is not keyed on the "
        "source position it claims to be -- re-read sql_reader_locations before trusting test 2"
    )

    # (e) the comment strip is what keeps 021's prose mention out of the census -- both polarities.
    assert LEGAL_HOLD_TOKEN.search(read(ROOT / MIG_021)), \
        "021 no longer mentions the flag in prose, so the stripping control below proves nothing"
    assert not sql_reader_locations({MIG_021: read(ROOT / MIG_021)}), (
        "a migration header comment is being counted as a reader; strip_sql_comments has stopped "
        "working and every count above is inflated"
    )
    assert len(blank_sql_comments("a\n-- b\n c\n")) == len("a\n-- b\n c\n"), \
        "comment blanking no longer preserves length, so the reported line numbers are wrong"


# ------------------------------------------------------------------ 3. python / js / scripts ----

def test_no_python_or_js_file_reads_the_flag_outside_the_known_set():
    """The application's and the scripts' touch set, each compared symmetrically and separately.

    scripts/ is its own bucket on purpose: report_drill.py and reconcile_market.py set the flag in
    fixtures and architecture-audit.py keeps it in a needle list, which is a different question from
    "does the platform freeze this capability".
    """
    # Pattern self-test on text this file owns.
    assert file_locations({"x.py": 'legal_hold = True\ncolumns = ["credit_holds"]\n'}) == {("x.py", 1)}, \
        "the identifier scan matches nothing, so every absence below is theatre"
    assert file_locations({"x.js": "credit_holds\nhold_status\nthreshold\nhousehold\n"}) == set()

    # The comparison itself has teeth: one element out of place must flip it, in both directions.
    planted = set(BASELINE_APP_TOUCHES) | {("services/api/app/routers/market.py", 500)}
    for probe, expected in ((planted, BASELINE_APP_TOUCHES), (BASELINE_APP_TOUCHES, planted)):
        fired = False
        try:
            symmetric(probe, frozenset(expected), "probe", "probe")
        except AssertionError:
            fired = True
        assert fired, "symmetric() does not fire on a one-element difference: every set test is theatre"

    texts = source_texts(APP_DIRS + (SCRIPT_DIR, ACCEPTANCE_DIR))
    scanned = {rel(ROOT / RIGHTS_PY)}
    assert scanned <= set(texts), f"{RIGHTS_PY} was not scanned -- the walk skipped a directory"

    app_found: set[tuple[str, int]] = set()
    scripts_found: set[tuple[str, int]] = set()
    acceptance_found: set[tuple[str, int]] = set()
    drill_found: set[tuple[str, int]] = set()
    for path, text in texts.items():
        locations = {(path, line) for line in token_lines(text)}
        if path in LIVE_DRILL_FILES:
            drill_found |= locations          # exempted from the line census, pinned as a file set
        elif path.startswith(SCRIPT_DIR + "/"):
            scripts_found |= locations
        elif path.startswith(ACCEPTANCE_DIR + "/"):
            acceptance_found |= locations
        else:
            app_found |= locations

    symmetric(app_found, BASELINE_APP_TOUCHES, "application (services/api, services/admin, services/web) touches",
              TEACH_THE_DRILL)
    symmetric(scripts_found, BASELINE_SCRIPT_TOUCHES, "scripts/ touches",
              "scripts/ is the operator tooling: a new hit here means a new fixture that sets the flag "
              "or a new audit needle. Say which, and if it is a new *reader*, it belongs with the "
              "application bucket and the drill needs a new assertion. If it is a new drill file, add "
              "it to LIVE_DRILL_FILES and say why it is a tester rather than a reader.")
    symmetric(acceptance_found, BASELINE_ACCEPTANCE_TOUCHES, "services/acceptance touches",
              "the acceptance specs set the flag in their fixtures. A new hit there is a new "
              "hold-related scenario: name it in the comment beside the set above, and if the spec "
              "reads rather than writes the flag it belongs to the application bucket.")

    # The exemption has to be earning something: the live drill must actually mention the flag, and it
    # must be the only thing the exemption covers. An empty drill is how a numerator silently dies.
    assert drill_found, (
        f"{sorted(LIVE_DRILL_FILES)} no longer mentions legal_hold at all. Either the drill stopped "
        "testing the hold (the whole point of this denominator) or it moved -- re-read it before "
        "touching this list."
    )
    assert {path for path, _ in drill_found} == set(LIVE_DRILL_FILES), \
        f"the drill exemption covered {sorted({path for path, _ in drill_found})}"
    for path in sorted(LIVE_DRILL_FILES):
        assert (ROOT / path).is_file(), f"{path} is exempted but does not exist -- stale exemption"

    # Only .py/.js may carry the flag inside those directories. A reader in a template or a shell
    # script would be invisible to a .py/.js-only glob, so the scan above is extension-agnostic and
    # this line says what it found.
    by_suffix = {(path, pathlib.Path(path).suffix)
                 for path, _ in app_found | scripts_found | acceptance_found | drill_found}
    wrong = {item for item in by_suffix if item[1] not in (".py", ".js", ".mjs")}
    assert not wrong, f"the flag is now read from a non-Python/JS source file: {sorted(wrong)}"

    # The three marketplace refusals reach the flag only through commerce.assert_offer_rights, so the
    # literal census above cannot see them. Pinned by name.
    market = read(ROOT / "services/api/app/routers/market.py")
    sites = {line_of(market, m.start()) for m in re.finditer(r"assert_offer_rights\s*\(", market)}
    assert len(sites) == ASSERT_OFFER_RIGHTS_SITE_COUNT, (
        f"market.py now calls commerce.assert_offer_rights {len(sites)} times (measured "
        f"{ASSERT_OFFER_RIGHTS_SITE_COUNT}: {describe(sites)}). Each added call site is another "
        "endpoint that refuses a hold; the drill must exercise it, and this count must be raised here."
    )
    assert not LEGAL_HOLD_TOKEN.search(market), (
        "market.py now spells the flag itself instead of delegating to assert_offer_rights; that is a "
        "new reader -- add it to the application bucket and teach the drill."
    )
    # Same scanner, planted reader, real file text: the bucket census does fire when a router starts
    # naming the flag. Without this, the negative check above could be reading nothing.
    probe_text = market + "if legal_hold:  # planted-probe\n"
    planted_line = line_of(probe_text, probe_text.index("# planted-probe"))
    assert token_lines(probe_text) - token_lines(market) == {planted_line}, (
        "a planted read in a real router's text was not seen by the scanner that produced every "
        "bucket above"
    )

    # The Python-side refusal words, pinned because the live drill matches on them
    # (`"legal hold" in str(body).lower()` against the 409 from POST /marketplace/offers). Re-word the
    # refusal and that assertion in scripts/hold_drill.py goes silently false-green, so both move together.
    commerce = read(ROOT / "services/api/app/domain/commerce.py")
    assert "raise ValueError(\"asset is restricted or under legal hold\")" in commerce, (
        "commerce.assert_offer_rights no longer refuses in the measured words. The SQL refusals are "
        "pinned in test_the_two_public_refusals_carry_their_documented_words; this is the Python half "
        "of the same promise, and the drill reads it verbatim."
    )


# ------------------------------------------------------------------ 4. which body is live ----

def test_the_live_body_of_prepare_brand_award_is_the_009_redefinition():
    """A reader who opens only 004 gets a superseded body; 009 is the one the database runs.

    No pg_proc here (there is no database in this test). The claim is made from file text: 009 replaces
    the same function name, carries 004's hold predicate unchanged, and additionally fixes the ambiguous
    column reference -- and 004 is proven to be the superseded one because it still has that ambiguity.
    """
    texts = migration_texts()
    defs = function_definitions(texts, "prepare_brand_award")
    assert set(defs) == {MIG_004, MIG_009}, (
        f"prepare_brand_award is now defined by {sorted(defs)}, not the measured 004 + 009 pair. "
        "Whichever file is newest is the live body: re-read it, decide whether the hold predicate "
        "moved with the replacement, and update both this test and the drill."
    )
    assert sorted(defs)[-1] == MIG_009, "009 is no longer the last definition by filename order"

    body_004, body_009 = defs[MIG_004], defs[MIG_009]
    assert body_009 != body_004, "the two bodies are now identical, so the replacement proves nothing"

    # 009 replaces 004's function: same name, same signature, CREATE OR REPLACE.
    signature = re.compile(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+prepare_brand_award\s*\(\s*target_submission\s+uuid\s*\)",
                           re.IGNORECASE)
    assert signature.search(body_004) and signature.search(body_009), (
        "prepare_brand_award's signature changed shape; the replacement link between 004 and 009 has "
        "to be re-read by hand before this test can claim 009 supersedes 004"
    )

    # The ambiguity fix, both polarities, fed the two real bodies.
    ambiguous = re.compile(r"FROM\s+rights_manifests\s+WHERE\s+asset_snapshot_id\s*=", re.IGNORECASE)
    qualified = re.compile(r"FROM\s+rights_manifests\s+WHERE\s+rights_manifests\.asset_snapshot_id\s*=",
                           re.IGNORECASE)
    assert ambiguous.search(body_004) and not ambiguous.search(body_009), (
        "004 no longer has the unqualified reference, so 009's reason to exist is gone -- re-read the "
        "chain: either 009 is dead weight or 004 has been edited into something else"
    )
    assert qualified.search(body_009) and not qualified.search(body_004), (
        "009 no longer carries the qualification fix (rights_manifests.asset_snapshot_id=...). "
        "If a newer file replaced it, that file is now the live body and this test plus the drill "
        "must point at it."
    )

    # And both bodies still carry the hold predicate -- 004 is superseded, not hold-free.
    for name, body in (("004", body_004), ("009", body_009)):
        reads = LEGAL_HOLD_TOKEN.findall(body)
        assert len(reads) == 2, (
            f"prepare_brand_award in {name} now reads the flag {len(reads)} times (measured 2: the "
            "manifest key and the moderation_cases EXISTS filter). The award path's freeze semantics "
            "changed; re-read the body and update the drill's award assertions."
        )
    assert sql_reader_locations({MIG_009: read(ROOT / MIG_009)}) == {(MIG_009, 26), (MIG_009, 31)}, \
        "the census no longer sees the live award reads where this test says they are"


# ------------------------------------------------------------------ 5. the refusal words ----

def test_the_two_public_refusals_carry_their_documented_words():
    """The words a held asset is refused in, one assertion per string, quoted verbatim.

    These are the two public refusals: the marketplace reservation (002's reserve_marketplace_offer)
    and the brand award (prepare_brand_award, whose live body is 009 and whose superseded body is 004).
    Each refusal is taken from the IF block that its own hold read guards, so a message that moved to a
    different predicate shows up here rather than silently passing.
    """
    reserve = function_body(read(ROOT / MIG_002), "reserve_marketplace_offer")
    assert reserve is not None, "reserve_marketplace_offer is gone from 002 -- re-read this test"
    award_009 = function_body(read(ROOT / MIG_009), "prepare_brand_award")
    award_004 = function_body(read(ROOT / MIG_004), "prepare_brand_award")

    reserve_reads = refusals_after_each_read(reserve)
    assert len(reserve_reads) == 2, f"expected 2 hold reads in 002's reserve, got {reserve_reads}"
    reserve_words = [word for _, word in reserve_reads]
    assert all(reserve_words), (
        f"a hold read in reserve_marketplace_offer guards no RAISE EXCEPTION any more: {reserve_words}. "
        "It became a silent filter, which is a behaviour change the drill has to be told about."
    )

    # One assertion per string, verbatim.
    assert RESERVE_MANIFEST_REFUSAL in reserve_words, (
        f"the marketplace's manifest refusal no longer reads '{RESERVE_MANIFEST_REFUSAL}' "
        f"(it now reads {reserve_words}). Anything that matches on this sentence -- the drill, "
        "the frontend, an error mapping -- has to be updated with it, or the wording restored."
    )
    assert RESERVE_CASE_REFUSAL in reserve_words, (
        f"the marketplace's moderation-case refusal no longer reads '{RESERVE_CASE_REFUSAL}' "
        f"(it now reads {reserve_words}). Same instruction: update the matcher, not this string."
    )

    for label, body in (("009/live", award_009), ("004/superseded", award_004)):
        assert body is not None, f"prepare_brand_award is gone from {label}"
        words = [word for _, word in refusals_after_each_read(body)]
        assert len(words) == 2 and all(words), \
            f"expected 2 hold-guarded refusals in prepare_brand_award {label}, read {words}"
        assert AWARD_MANIFEST_REFUSAL in words, (
            f"prepare_brand_award ({label}) no longer refuses the manifest hold with "
            f"'{AWARD_MANIFEST_REFUSAL}' (read {words})"
        )
        assert AWARD_CASE_REFUSAL in words, (
            f"prepare_brand_award ({label}) no longer refuses the case hold with "
            f"'{AWARD_CASE_REFUSAL}' (read {words})"
        )

    # The reader that is NOT a refusal, pinned so the window heuristic is known to distinguish them: a
    # read with no enclosing IF block is still *seen* but carries no words (None), and a read inside one
    # carries that IF's own words. Both samples are text this file owns, not the tree's.
    assert refusals_after_each_read("SELECT 1 FROM t WHERE c.legal_hold;") == [(1, None)], \
        "a read with no RAISE should be seen but wordless -- a change here means the window slipped"
    assert refusals_after_each_read(
        "IF (SELECT c.legal_hold FROM moderation_cases c) THEN RAISE EXCEPTION 'nope'; END IF;"
    ) == [(1, "nope")], "the refusal extractor stopped following the hold read into its IF block"


# ------------------------------------------------------------------ 6. the doors it does not guard ----

def test_the_flag_is_absent_from_the_doors_it_does_not_guard():
    """Erasure and download/export do not consult the hold, and rights.py does not name it at all.

    `legal_hold` freezes rights *decisions*. A subject's right to be forgotten and the export of their
    own data are not rights decisions, and neither is the capability reader in rights.py: the moment the
    flag appears in one of those bodies, an erased account or a download stops working for a reason the
    drill has never asserted. Pinned as absence.
    """
    texts = migration_texts()
    erasers = function_definitions(texts, "erase_user_identity")
    assert len(erasers) >= 2, (
        f"only {len(erasers)} migration(s) define erase_user_identity; the live body is a later "
        "replacement (014, 016) and if the reader stopped following the chain it is clearing a dead body"
    )
    for path, body in sorted(erasers.items()):
        # Non-vacuity first: an empty extraction would make every absence below meaningless.
        assert "RAISE EXCEPTION 'account not found'" in body, \
            f"{path}: the erase_user_identity extraction returned a stub, so this check read nothing"
        assert not LEGAL_HOLD_TOKEN.search(body), (
            f"erase_user_identity in {path} now reads legal_hold. Erasure is not a rights decision and "
            "no drill asserts a hold blocks it -- either remove the read or write the drill assertion "
            "and register the location in the census above."
        )
        offenders = [(line_of(body, m.start()), body[max(0, m.start() - 40):m.end() + 40])
                     for m in HOLD_WORD.finditer(body)]
        assert not offenders, (
            f"erase_user_identity in {path} now contains the bare word `hold` outside comments at "
            f"{offenders}. Prose inside the body was stripped, so this is code: check whether it is "
            "the flag under another name before anything else."
        )

    rights = read(ROOT / RIGHTS_PY)
    assert "hold" not in rights.lower(), (
        f"{RIGHTS_PY} now contains 'hold' in some casing. This module is the capability reader that is "
        "supposed to be hold-blind; if the flag belongs here, the download/export door is now guarded "
        "and this test's premise (and the drill) must be rewritten, not just this string."
    )
    allowed = python_function_source(rights, "allowed")
    assert '"capabilities"' in allowed, \
        f"{RIGHTS_PY}: the allowed() extraction returned a stub, so this check read nothing"
    assert not LEGAL_HOLD_TOKEN.search(allowed) and not HOLD_WORD.search(allowed), \
        f"{RIGHTS_PY}'s allowed() now consults a hold"

    # The word-boundary control, on the real file that carries the decoy spelling.
    decoy = read(ROOT / MIG_001)
    decoy_stripped = blank_sql_comments(decoy)
    assert "credit_holds" in decoy_stripped, \
        f"{MIG_001} no longer declares credit_holds, so this control proves nothing about the boundary"
    runs = sorted({word_run_at(decoy_stripped, m.start(), m.end())
                   for m in HOLD_WORD.finditer(decoy_stripped)})
    assert runs and runs == ["hold"], (
        f"the bare-word scan matched {runs} in {MIG_001}; the boundary choice has drifted and the "
        "absence assertions above are now reading the wrong words"
    )
    assert "credit_holds" not in runs, "credit_holds leaked into the matched set"
    assert LEGAL_HOLD_TOKEN.search(decoy_stripped), \
        f"{MIG_001} no longer carries the enum member, so it is the wrong file to test the census on"


# ------------------------------------------------------------------ 7. contract copies ----

def test_the_contract_copies_are_the_measured_ones():
    """shared/contracts spells the flag seven times across three files, compared symmetrically.

    The contracts are what an outside integrator reads. A field added to openapi without a reader in
    SQL or Python promises a freeze the platform does not implement; a field removed silently un-promises
    one it does. Both directions fail here.
    """
    texts = source_texts((CONTRACT_DIR,))
    found = {key for key in file_locations(texts) if key[0].startswith(CONTRACT_DIR)}
    assert found, (
        "not one contract file spells legal_hold, so the scan below clears nothing. Either the "
        "contracts stopped carrying the field at all (a promise removed) or this scan stopped reading "
        "them -- check source_texts() before believing it."
    )
    symmetric(found, BASELINE_CONTRACT_TOUCHES, "contract copies of legal_hold",
              "a contract that grew or lost the field has to be re-generated from the router that "
              "serves it, and the drill has to be told whether the promise changed. Do not edit these "
              "line numbers to make the count match: contracts/openapi-v13.yaml and .json are two "
              "checked-in copies and both belong in this denominator.")
    yaml_sites = sorted(line for path, line in found if path.endswith(".yaml"))
    json_sites = sorted(line for path, line in found if path.endswith(".json")
                        and path.endswith("openapi-v13.json"))
    assert len(yaml_sites) == 3 and len(json_sites) == 3, (
        f"the two openapi copies no longer carry the same number of the field "
        f"(yaml {yaml_sites}, json {json_sites}) -- openapi-v13.yaml and openapi-v13.json are the same "
        "contract in two spellings, so a field that appeared in only one of them is a contract bug "
        "rather than a new reader."
    )
    design = read(ROOT / "shared/contracts/design-reference/rights-manifest-v2.schema.json")
    # The schema spells the member as a JSON string, so the token has quote characters on both sides.
    assert len(LEGAL_HOLD_TOKEN.findall(design)) == 1, (
        "the design-reference rights manifest schema now names the flag "
        f"{len(LEGAL_HOLD_TOKEN.findall(design))} times (measured once, as a member of the status "
        "enum). A second spelling there is a second promise about what a hold is."
    )
