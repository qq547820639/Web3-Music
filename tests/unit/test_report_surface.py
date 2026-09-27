"""Static guards for the public rights intake: one vocabulary written three times, four doors, two answers.

Two defect classes, both silent when they ship:

1. **A split vocabulary.** The same word list is spelled three times on purpose. The Python constants at
   ``services/api/app/main.py:1002-1004`` are what the endpoint validates a body against before the
   database is asked; the CHECK constraints at
   ``db/migrations/021_public_rights_report.sql:43,47,54`` are the table's own independent backstop; and
   the ``SECURITY DEFINER`` function repeats both refusal lists in its body (``021:120-125``) because it
   wants to answer 22023 before it writes anything. Nothing in the repository compares any two of them. A
   round that adds a value to one copy and not the others ships either a 422 the table would never have
   caused, or -- the quiet direction -- a value the endpoint waves through and that dies inside the CHECK
   or the function predicate on the way in, which is exactly the 500 the handler comment at
   ``main.py:1071-1075`` says the pre-check exists to dodge.

2. **A door whose authority is not what it is documented to be.** ``POST /api/reports`` and
   ``POST /api/reports/status`` are public *by design* -- a rights holder is by definition not a member
   of the workspace they file against (``main.py:995-1001``) -- so a guard added there "for safety"
   silently closes the intake. The two ``/api/moderation/reports`` doors are the opposite: they are the
   only path on which a reporter's name and address leave the database, so a dropped
   ``require_platform_admin`` there, or one quietly downgraded to ``get_actor`` (any logged-in user),
   hands a stranger the whole filing list.

Why the live drill is not enough. ``scripts/report_drill.py`` is the real proof and it does measure both
refusals, but it needs the stack up (httpx against ``API_BASE_URL``, and ``docker compose exec ... psql``)
so it is not the gate that runs on a pull request, and its notice fixture sends one subject type --
``'asset'``, plus the deliberate ``'spaceship'`` refusal -- and one ground, ``'copyright'``
(``scripts/report_drill.py:143-148,235``). A value that lives on only one of the two sides is never
touched by it, in either direction. What a no-stack test can decide is the wiring the drill leans on: that
the two lists are the same set, that each door carries the dependency it is documented to carry, and that
an omitted attestation cannot be read as consent.

Every detector below ships with a mutation that must make it fire, and the same test asserts the untouched
real text is quiet -- a guard nobody has seen report anything is indistinguishable from a guard that
cannot. The mutations are applied to in-memory copies of the real text: nothing on disk is rewritten here,
and ``mutate`` refuses to be a no-op, so an injected difference is never mistaken for a passing comparison.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "services/api/app/main.py"
MIGRATION = ROOT / "db/migrations/021_public_rights_report.sql"
MAIN_SRC = MAIN.read_text(encoding="utf-8")
MIG_SRC = MIGRATION.read_text(encoding="utf-8")

PUBLIC_DOORS = ("file_rights_report", "rights_report_status")
STAFF_DOORS = ("moderation_report_queue", "moderation_report_link")
ADMIN_GUARD = "require_platform_admin"
BODY_MODEL = "RightsReportBody"
ATTEST_CONSTANT = "REPORT_ATTESTATIONS"

# (the constant the endpoint validates against, the column whose CHECK carries the same list)
VOCABULARIES = (("REPORT_SUBJECT_TYPES", "subject_type"), ("REPORT_GROUNDS", "grounds"))

# ------------------------------------------------------------------ the two text faces ----

# `CHECK (col IN ('a','b'))`. Anchored on the constraint, because the migration spells these very same
# two lists again inside `file_rights_report`'s `= ANY (ARRAY[...])` predicates -- a reader that matched
# a bare value list would be comparing whichever copy it happened to reach first.
CHECK_IN = re.compile(r"CHECK\s*\(\s*(?P<col>[a-z_]+)\s+IN\s*\(\s*(?P<vals>[^)]*)\)")
# The attestation rule is a containment CHECK, not an IN list, and only the real one carries the
# `::text[]` cast -- the header comment at 021:21 names the same array without it.
ATTEST_CHECK = re.compile(
    r"CHECK\s*\(\s*attested_statements\s*@>\s*ARRAY\s*\[(?P<vals>[^\]]*)\]::text\s*\[\]")


def quoted_values(blob: str) -> set[str]:
    """Every single-quoted token of a SQL value list, with the list's shape checked first.

    The count is checked against the commas: a token written without quotes would be dropped silently,
    and a guard that silently drops one entry reads identical to a guard that never saw the list.
    """
    if not blob.strip():
        return set()
    values = re.findall(r"'([^']*)'", blob)
    assert len(values) == blob.count(",") + 1, \
        f"cannot parse every entry of {blob!r}: {len(values)} quoted, {blob.count(',') + 1} expected"
    return set(values)


def check_columns(sql: str) -> set[str]:
    """Every column this migration restricts with an `IN` CHECK -- the denominator of the parity guard."""
    return {m.group("col") for m in CHECK_IN.finditer(sql)}


def check_list_blob(sql: str, col: str) -> str:
    hits = [m for m in CHECK_IN.finditer(sql) if m.group("col") == col]
    assert len(hits) == 1, f"expected exactly one CHECK IN list for {col}, saw {len(hits)}"
    return hits[0].group("vals")


def sql_check_list(sql: str, col: str) -> set[str]:
    return quoted_values(check_list_blob(sql, col))


def first_sql_value(sql: str, col: str) -> str:
    """The first value of that column's list *in the order the migration writes it*.

    The mutation fixtures anchor on this rather than picking one alphabetically, so they keep pointing
    at a real entry after someone reorders the constraint.
    """
    head = re.match(r"\s*'([^']*)'", check_list_blob(sql, col))
    assert head, f"no quoted first entry in the CHECK list for {col}"
    return head.group(1)


def sql_attestations(sql: str) -> set[str]:
    hits = ATTEST_CHECK.findall(sql)
    assert len(hits) == 1, f"expected exactly one attestation containment CHECK, saw {len(hits)}"
    return quoted_values(hits[0])


# The intake function repeats both lists as its own predicates (021:120-125), because the table's CHECK
# is only reached on the way in and the function wants to refuse with 22023 before it writes anything.
# That is a third copy of the same vocabulary, so it is compared with the same yardstick.
ANY_ARRAY = re.compile(r"p_(?P<col>subject_type|grounds)\s*=\s*ANY\s*\(\s*ARRAY\s*\[(?P<vals>[^\]]*)\]")
# The resolver maps only the workspace-owned subject types; `user` and `other` deliberately have no
# branch (they resolve to no workspace and the report waits for a human), so this side is a subset, not
# an equality -- and a branch for a type the vocabulary does not allow is the defect worth catching.
RESOLVES = re.compile(r"WHEN\s+'([^']*)'\s+THEN\s+\(SELECT\s+workspace_id")


def sql_any_list(sql: str, col: str) -> set[str]:
    hits = [m for m in ANY_ARRAY.finditer(sql) if m.group("col") == col]
    assert len(hits) == 1, f"expected exactly one `p_{col} = ANY (ARRAY[...])` predicate, saw {len(hits)}"
    return quoted_values(hits[0].group("vals"))


def first_any_value(sql: str, col: str) -> str:
    """The first value of that predicate's array, in the order the migration writes it."""
    hits = [m for m in ANY_ARRAY.finditer(sql) if m.group("col") == col]
    assert len(hits) == 1, f"expected exactly one predicate for {col}, saw {len(hits)}"
    head = re.match(r"'([^']*)'", hits[0].group("vals"))
    assert head, f"the ARRAY list for {col} does not start with a quoted value"
    return head.group(1)


def sql_resolved_types(sql: str) -> set[str]:
    return set(RESOLVES.findall(sql))


def module_string_list(source: str, name: str) -> list[str]:
    """The strings bound to a module-level constant, in declaration order, however they are spelled."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            value = node.value
            assert isinstance(value, (ast.Tuple, ast.List, ast.Set)), \
                f"{name} is no longer a tuple/list/set literal: {ast.unparse(node)}"
            out = []
            for elt in value.elts:
                assert isinstance(elt, ast.Constant) and isinstance(elt.value, str), \
                    f"{name} holds a non-string entry: {ast.unparse(elt)}"
                out.append(elt.value)
            return out
    raise AssertionError(f"{name} is not a module-level assignment in main.py any more")


def module_string_set(source: str, name: str) -> set[str]:
    """A set, because parity is set equality -- the shipped constants being tuples is not the contract."""
    return set(module_string_list(source, name))


def parity_findings(py_values: set[str], sql_values: set[str], py_label: str, sql_label: str) -> list[str]:
    """Set equality, reported per direction: which side is missing which values."""
    out = []
    only_py = sorted(py_values - sql_values)
    only_sql = sorted(sql_values - py_values)
    if only_py:
        out.append(f"{sql_label} is missing {only_py} that {py_label} accepts")
    if only_sql:
        out.append(f"{py_label} is missing {only_sql} that {sql_label} allows")
    return out


def vocabulary_findings(main_src: str, sql: str, py_name: str, col: str) -> list[str]:
    return parity_findings(module_string_set(main_src, py_name), sql_check_list(sql, col),
                           f"main.py:{py_name}", f"021 CHECK ({col} IN ...)")


# ----------------------------------------------------------------------- the doors ----

def functions(source: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}


def function_node(source: str, name: str) -> ast.FunctionDef | None:
    return functions(source).get(name)


def params(fn: ast.FunctionDef) -> list[tuple[str, str, ast.expr | None]]:
    """(name, annotation, default node) for the real arguments of one ``ast.FunctionDef``.

    Positional defaults align to the *end* of ``posonlyargs + args`` by language rule, so the offset is
    computed rather than assumed: zipping from the front would hand the ``Depends`` to the wrong
    parameter the moment a handler carries any other defaulted argument, and
    ``test_the_parameter_reader_aligns_defaults_from_the_end`` is the control that would catch it.
    """
    plain = [*fn.args.posonlyargs, *fn.args.args]
    defaults = fn.args.defaults
    first = len(plain) - len(defaults)
    out = [(a.arg, ast.unparse(a.annotation) if a.annotation else "",
            defaults[i - first] if i >= first else None)
           for i, a in enumerate(plain)]
    out += [(a.arg, ast.unparse(a.annotation) if a.annotation else "", d)
            for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults)]
    return out


def dependency_name(default: ast.expr | None) -> str | None:
    """The callable inside ``Depends(...)`` as the handler spells it; None when the default is not one.

    Judged on the AST node, not on a text search for the word ``Depends``: a body or docstring that
    merely mentions the name would satisfy a text reader while guarding nothing.
    """
    if not isinstance(default, ast.Call):
        return None
    if ast.unparse(default.func).split(".")[-1] != "Depends":
        return None
    return ast.unparse(default.args[0]) if default.args else "<Depends with no callable>"


def door_dependencies(source: str, fn_name: str) -> list[str]:
    """What the reader actually saw on that handler -- the positive control that it is not blind."""
    fn = function_node(source, fn_name)
    assert fn is not None, f"{fn_name} is gone from main.py"
    return [dep for _name, _ann, dflt in params(fn) if (dep := dependency_name(dflt))]


def route_dependency_findings(fn: ast.FunctionDef) -> list[str]:
    """Router-level dependencies are the second way a door acquires an actor.

    A signature-only reader would watch ``@app.post(..., dependencies=[Depends(get_actor)])`` close a
    public intake and report the parameter list as clean.
    """
    out = []
    for dec in fn.decorator_list:
        for kw in getattr(dec, "keywords", []):
            if kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)) and kw.value.elts:
                out.append(f"{fn.name}: the route itself declares dependencies={ast.unparse(kw.value)}")
    return out


def door_findings(source: str, fn_name: str, expected_guard: str | None) -> list[str]:
    """Findings for one endpoint.

    ``expected_guard is None`` -> a public door: no ``Depends(...)`` parameter at all, because a session
    or an actor here is what stops an account-less rights holder from filing. Otherwise -> a staff door:
    every ``Depends`` it carries must be the named guard, so a downgrade to ``get_actor`` reads as a
    finding instead of a pass, and so does the guard going missing entirely.
    """
    fn = function_node(source, fn_name)
    if fn is None:
        return [f"{fn_name}: no such function in main.py, so this door census is out of date"]
    found = [(name, dep) for name, _ann, dflt in params(fn) if (dep := dependency_name(dflt))]
    out = route_dependency_findings(fn)
    if expected_guard is None:
        return out + [f"{fn_name} is a public door but takes {name} = Depends({dep})"
                      for name, dep in found]
    if not found:
        out.append(f"{fn_name} is a staff door with no Depends(...) parameter at all -- anyone can call it")
    out += [f"{fn_name}: parameter {name} guards on {dep}, not {expected_guard}"
            for name, dep in found if dep != expected_guard]
    return out


def edited_signature(fn: ast.FunctionDef, *, drop_guarded: bool = False,
                     downgrade_to: str | None = None, add_guarded: str | None = None) -> str:
    """A standalone, re-parseable copy of one real signature with exactly one in-memory edit applied.

    Rendered from the AST rather than poked at with string offsets, so reformatting the real handler (a
    wrapped line, a renamed local) cannot turn a control into a no-op that still reports green; and it
    raises if the requested edit found no ``Depends`` to edit.
    """
    pieces, edited = [], False
    for name, ann, dflt in params(fn):
        dep = dependency_name(dflt)
        if dep and drop_guarded:
            edited = True
            continue
        piece = f"{name}: {ann}" if ann else name
        if dflt is not None:
            if dep and downgrade_to:
                piece, edited = f"{piece} = Depends({downgrade_to})", True
            else:
                piece = f"{piece} = {ast.unparse(dflt)}"
        pieces.append(piece)
    if add_guarded:
        pieces.append(f"{add_guarded}: Actor = Depends(get_actor)")
        edited = True
    if (drop_guarded or downgrade_to or add_guarded) and not edited:
        raise AssertionError(f"{fn.name}: the requested edit found no Depends to edit")
    return f"def {fn.name}({', '.join(pieces)}):\n    ...\n"


def decorated_route(fn: ast.FunctionDef, dependencies: str) -> str:
    """The real route decorator with ``dependencies=[...]`` injected, as a standalone parseable copy."""
    assert fn.decorator_list, f"{fn.name} has no decorator to inject into"
    dec = ast.unparse(fn.decorator_list[0])
    assert dec.endswith(")"), f"cannot inject into an unparenthesised decorator: {dec}"
    return f"@{dec[:-1]}, dependencies=[{dependencies}])\ndef {fn.name}():\n    ...\n"


# ------------------------------------------------------------------ the request body ----

def is_ellipsis(node: ast.expr | None) -> bool:
    """`Field(...)` is pydantic for "required", and ast.unparse spells the constant as `...`."""
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def field_specs(source: str, cls: str) -> dict[str, dict[str, str | None]]:
    """Per annotated field of a pydantic model: its annotation, its declared default, its ``pattern=``.

    Two spellings are read because this module uses both: ``attest_good_faith: bool = False`` is a plain
    annotated default while ``reporter_email: str = Field(..., pattern=...)`` is a ``Field`` call. A
    reader that understood only ``Field(...)`` would report the two attestation booleans as absent.
    """
    node = next((n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.ClassDef) and n.name == cls), None)
    assert node is not None, f"{cls} is not defined in main.py any more"
    specs: dict[str, dict[str, str | None]] = {}
    for stmt in node.body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        default, pattern, value = "REQUIRED", None, stmt.value
        if isinstance(value, ast.Call) and ast.unparse(value.func).split(".")[-1] == "Field":
            if value.args and not is_ellipsis(value.args[0]):  # positional default; `Field(...)` is required
                default = ast.unparse(value.args[0])
            for kw in value.keywords:
                if kw.arg == "default":
                    default = "REQUIRED" if is_ellipsis(kw.value) else ast.unparse(kw.value)
                elif kw.arg == "pattern":
                    pattern = (kw.value.value if isinstance(kw.value, ast.Constant)
                               and isinstance(kw.value.value, str) else ast.unparse(kw.value))
        elif value is not None:
            default = ast.unparse(value)
        specs[stmt.target.id] = {"annotation": ast.unparse(stmt.annotation),
                                 "default": default, "pattern": pattern}
    return specs


def attestation_findings(specs: dict, statements: set[str]) -> list[str]:
    """Each statement the table demands needs a bool on the body whose declared default is False.

    The field names are derived from the vocabulary instead of hardcoded, so if 021's array gains a third
    statement this guard goes looking for its field rather than quietly ignoring it. The default is the
    whole point: with a default of True, a caller who simply omits the key has attested, and the
    handler's refusal at ``main.py:1059`` only means something while absence is False.
    """
    out = []
    for value in sorted(statements):
        name = f"attest_{value}"
        spec = specs.get(name)
        if spec is None:
            out.append(f"{name}: the table demands {value!r} but the intake body has no such field")
            continue
        if spec["annotation"] != "bool":
            out.append(f"{name}: annotated {spec['annotation']!r}, not bool")
        if spec["default"] != "False":
            out.append(f"{name}: default is {spec['default']!r}; an omitted statement must not read "
                       "as consent")
    return out


def pattern_findings(specs: dict, name: str, probes: list[tuple[str, bool]]) -> list[str]:
    """The declared ``pattern=`` must exist, be non-empty, and actually decide the probe cases.

    Judged by behaviour, not by spelling: a pattern that is present but unanchored satisfies "has a
    non-empty pattern" and still lets ``junk user@example.com junk`` through ``re.search`` -- which is
    the field the table then refuses with ``report_email_shape``, after the endpoint already said yes.
    """
    spec = specs.get(name)
    if spec is None:
        return [f"{name}: no such field on the intake body"]
    pattern = spec["pattern"]
    if not pattern:
        return [f"{name}: declares no pattern, so an address the table would reject is accepted here"]
    out = []
    for probe, should_match in probes:
        matched = bool(re.search(pattern, probe))
        if matched != should_match:
            out.append(f"{name}: pattern {pattern!r} matches {probe!r}={matched}, expected {should_match}")
    return out


# ---------------------------------------------------------------------- the controls ----

def mutate(text: str, old: str, new: str) -> str:
    """An in-memory mutation that refuses to be a no-op.

    Without the count check a stale anchor returns the untouched original, and the control "passes"
    while having injected nothing.
    """
    assert text.count(old) == 1, f"anchor is not unique (found {text.count(old)}): {old!r}"
    out = text.replace(old, new)
    assert out != text, f"mutation changed nothing: {old!r}"
    return out


EMAIL_PROBES = [("user@example.com", True), ("no-at-sign", False), ("junk user@example.com junk", False)]
LABELS = {py: f"021 CHECK ({col} IN ...)" for py, col in VOCABULARIES}
ATTEST_LABEL = "021 CHECK (attested_statements @> ARRAY[...])"


# ------------------------------------------------------------------ the real assertions ----

def test_the_migration_still_writes_the_lists_and_the_array_this_guard_reads():
    """Without this, a renamed column or a dropped cast makes every parity assertion below pass on emptiness.

    The set of IN-restricted columns is pinned, not merely its members: a third ``CHECK (col IN (...))``
    added to this migration has to be brought into the parity guard, and joining ``check_columns`` is the
    only way that would ever be noticed. The counts are pinned too, because "read seven, compared six"
    is the shape of a silent guard; when the vocabulary legitimately grows, these numbers move with it.
    """
    assert check_columns(MIG_SRC) == {"subject_type", "grounds"}, sorted(check_columns(MIG_SRC))
    assert len(sql_check_list(MIG_SRC, "subject_type")) == 7, sorted(sql_check_list(MIG_SRC, "subject_type"))
    assert len(sql_check_list(MIG_SRC, "grounds")) == 7, sorted(sql_check_list(MIG_SRC, "grounds"))
    assert len(sql_attestations(MIG_SRC)) == 2, sorted(sql_attestations(MIG_SRC))
    for py_name, col in VOCABULARIES:
        assert len(module_string_set(MAIN_SRC, py_name)) == len(sql_check_list(MIG_SRC, col)), py_name
    assert len(module_string_set(MAIN_SRC, ATTEST_CONSTANT)) == len(sql_attestations(MIG_SRC))


def test_the_endpoints_this_guard_censuses_are_the_endpoints_on_disk():
    """A door guard on a renamed handler is a guard on nothing, so the names come with their routes."""
    for name in (*PUBLIC_DOORS, *STAFF_DOORS):
        assert function_node(MAIN_SRC, name) is not None, f"{name} is no longer a module-level function"
    for path, name in (("/api/reports", "file_rights_report"),
                       ("/api/reports/status", "rights_report_status"),
                       ("/api/moderation/reports", "moderation_report_queue"),
                       ("/api/moderation/reports/{report_id}/link", "moderation_report_link")):
        assert f'"{path}"' in MAIN_SRC, f"the route {path} is gone; this census names {name}"


@pytest.mark.parametrize("py_name,col", VOCABULARIES)
def test_the_python_vocabulary_is_set_equal_to_the_migrations_check(py_name, col):
    findings = vocabulary_findings(MAIN_SRC, MIG_SRC, py_name, col)
    assert findings == [], "; ".join(findings)


@pytest.mark.parametrize("py_name,col", VOCABULARIES)
def test_the_python_vocabulary_is_set_equal_to_the_intake_functions_own_predicate(py_name, col):
    """The third copy: `file_rights_report` refuses before the table's CHECK is ever reached (021:120-125).

    A value added to the Python tuple and the table CHECK but not here comes back as a 22023 from inside
    the SECURITY DEFINER function -- which the handler turns into a refusal for a body it had just said
    yes to, the same shape the endpoint's own pre-check exists to prevent.
    """
    findings = parity_findings(module_string_set(MAIN_SRC, py_name), sql_any_list(MIG_SRC, col),
                               f"main.py:{py_name}", f"021 p_{col} = ANY (ARRAY[...])")
    assert findings == [], "; ".join(findings)


def test_the_subject_resolver_knows_no_subject_type_the_vocabulary_does_not_allow():
    """A `WHEN 'x'` branch for a type nothing else allows would query a table for a value no body can carry.

    The reverse direction is deliberately not asserted: `user` and `other` resolve to no workspace by
    design (021:96-108), which is why this is a subset test and not a second parity test.
    """
    allowed = sql_check_list(MIG_SRC, "subject_type")
    resolved = sql_resolved_types(MIG_SRC)
    assert resolved <= allowed, sorted(resolved - allowed)
    assert len(resolved) < len(allowed), \
        "every subject type now resolves to a workspace, so the manual-queue branch is dead"


def test_the_python_attestations_are_the_migrations_attestation_array():
    findings = parity_findings(module_string_set(MAIN_SRC, ATTEST_CONSTANT), sql_attestations(MIG_SRC),
                               f"main.py:{ATTEST_CONSTANT}", ATTEST_LABEL)
    assert findings == [], "; ".join(findings)


@pytest.mark.parametrize("name", PUBLIC_DOORS)
def test_the_public_intake_carries_no_dependency(name):
    """No session, no actor, no route-level dependency: this is the door an account-less person uses."""
    assert door_findings(MAIN_SRC, name, None) == [], door_findings(MAIN_SRC, name, None)
    assert door_dependencies(MAIN_SRC, name) == [], door_dependencies(MAIN_SRC, name)


@pytest.mark.parametrize("name", STAFF_DOORS)
def test_the_staff_doors_guard_on_the_platform_admin(name):
    assert door_findings(MAIN_SRC, name, ADMIN_GUARD) == [], door_findings(MAIN_SRC, name, ADMIN_GUARD)
    # The reader has to be able to see a Depends at all, or the two public-door assertions above are
    # quiet for the wrong reason. This is that positive control, read off the real source.
    assert door_dependencies(MAIN_SRC, name) == [ADMIN_GUARD], door_dependencies(MAIN_SRC, name)


def test_the_intake_body_defaults_both_statements_to_refusal():
    findings = attestation_findings(field_specs(MAIN_SRC, BODY_MODEL),
                                    module_string_set(MAIN_SRC, ATTEST_CONSTANT))
    assert findings == [], "; ".join(findings)


def test_the_intake_body_demands_an_address_shape_the_table_can_check():
    specs = field_specs(MAIN_SRC, BODY_MODEL)
    findings = pattern_findings(specs, "reporter_email", EMAIL_PROBES)
    assert findings == [], "; ".join(findings)


# ---------------------------------------------------------------------- the controls ----

@pytest.mark.parametrize("py_name,col", VOCABULARIES)
def test_the_parity_guard_fires_in_both_directions(py_name, col):
    """The SQL list loses its first entry, then the Python list loses and gains one -- each must be named.

    Each arm also checks *which side the message blames*, because a comparator that reports the
    difference against the wrong half is the one that gets a fix applied to the wrong file.
    """
    label = LABELS[py_name]
    first_sql = first_sql_value(MIG_SRC, col)
    sql_dropped = mutate(MIG_SRC, f"CHECK ({col} IN ('{first_sql}',", f"CHECK ({col} IN (")
    fired = vocabulary_findings(MAIN_SRC, sql_dropped, py_name, col)
    assert len(fired) == 1, fired
    assert fired[0] == f"{label} is missing ['{first_sql}'] that main.py:{py_name} accepts", fired[0]

    first_py = module_string_list(MAIN_SRC, py_name)[0]
    py_dropped = mutate(MAIN_SRC, f'{py_name} = ("{first_py}", ', f"{py_name} = (")
    fired = vocabulary_findings(py_dropped, MIG_SRC, py_name, col)
    assert len(fired) == 1, fired
    assert fired[0] == f"main.py:{py_name} is missing ['{first_py}'] that {label} allows", fired[0]

    gained = mutate(MAIN_SRC, f"{py_name} = (", f'{py_name} = ("zzz_unlisted", ')
    fired = vocabulary_findings(gained, MIG_SRC, py_name, col)
    assert any("zzz_unlisted" in f and f.startswith(f"{label} is missing") for f in fired), fired

    assert vocabulary_findings(MAIN_SRC, MIG_SRC, py_name, col) == [], "the untouched pair must be quiet"


@pytest.mark.parametrize("py_name,col", VOCABULARIES)
def test_the_predicate_guard_fires_when_the_intake_function_alone_loses_a_value(py_name, col):
    """The copy nobody thinks to update: the function's own `= ANY (ARRAY[...])` refusal list.

    The CHECK side stays quiet here, which is the point -- a guard that only compared main.py against the
    table constraint would call that pair a match while the write path still refuses the value.
    """
    label = f"021 p_{col} = ANY (ARRAY[...])"
    dropped = first_any_value(MIG_SRC, col)
    mutated = mutate(MIG_SRC, f"ARRAY['{dropped}',", "ARRAY[")
    fired = parity_findings(module_string_set(MAIN_SRC, py_name), sql_any_list(mutated, col),
                            f"main.py:{py_name}", label)
    assert fired == [f"{label} is missing ['{dropped}'] that main.py:{py_name} accepts"], fired
    assert sql_check_list(mutated, col) == module_string_set(MAIN_SRC, py_name), \
        "control assumes the table CHECK is untouched; if it is not, this arm proves nothing"

    # A value invented on the Python side is missing from the predicate too, and must be named as such.
    gained = mutate(MAIN_SRC, f"{py_name} = (", f'{py_name} = ("zzz_unlisted", ')
    fired = parity_findings(module_string_set(gained, py_name), sql_any_list(MIG_SRC, col),
                            f"main.py:{py_name}", label)
    assert any("zzz_unlisted" in f and f.startswith(f"{label} is missing") for f in fired), fired

    assert parity_findings(module_string_set(MAIN_SRC, py_name), sql_any_list(MIG_SRC, col),
                           f"main.py:{py_name}", label) == []


def test_the_resolver_guard_fires_on_a_branch_for_an_unlisted_subject_type():
    """`WHEN 'spaceship' THEN (SELECT workspace_id ...)` would query a table that does not exist."""
    planted = mutate(MIG_SRC, "    WHEN 'project'     THEN (SELECT workspace_id FROM song_projects",
                     "    WHEN 'spaceship'   THEN (SELECT workspace_id FROM spaceships)\n"
                     "    WHEN 'project'     THEN (SELECT workspace_id FROM song_projects")
    resolved, allowed = sql_resolved_types(planted), sql_check_list(planted, "subject_type")
    assert sorted(resolved - allowed) == ["spaceship"], sorted(resolved - allowed)
    assert not resolved <= allowed

    # The shipped resolver is a strict subset -- and if it stopped being one, the manual-queue branch
    # at 021:130 would already be dead code, which the guard says out loud rather than passing on.
    assert sql_resolved_types(MIG_SRC) <= sql_check_list(MIG_SRC, "subject_type")
    assert len(sql_resolved_types(MIG_SRC)) < len(sql_check_list(MIG_SRC, "subject_type"))


def test_the_attestation_parity_guard_fires_on_either_side():
    """Both halves of the 021:54 array are anchored on their shipped spelling.

    These anchors are fixtures, not assertions: if someone adds a third statement or drops the cast,
    ``mutate`` goes red with "anchor is not unique (found 0)" rather than injecting nothing and handing
    the comparison back as a clean pass.
    """
    py_label = f"main.py:{ATTEST_CONSTANT}"
    shipped = parity_findings(module_string_set(MAIN_SRC, ATTEST_CONSTANT), sql_attestations(MIG_SRC),
                              py_label, ATTEST_LABEL)

    sql_dropped = mutate(MIG_SRC, "ARRAY['good_faith','accuracy']::text[]", "ARRAY['good_faith']::text[]")
    fired = parity_findings(module_string_set(MAIN_SRC, ATTEST_CONSTANT), sql_attestations(sql_dropped),
                            py_label, ATTEST_LABEL)
    assert fired == [f"{ATTEST_LABEL} is missing ['accuracy'] that {py_label} accepts"], fired

    # The Python side loses one instead. The trailing comma on the replacement is not decoration:
    # `("accuracy")` is a parenthesised string, so a fixture that dropped it would fail in
    # `module_string_list` for the wrong reason rather than proving the guard's other direction.
    py_dropped = mutate(MAIN_SRC, f'{ATTEST_CONSTANT} = ("good_faith", "accuracy")',
                        f'{ATTEST_CONSTANT} = ("accuracy",)')
    fired = parity_findings(module_string_set(py_dropped, ATTEST_CONSTANT), sql_attestations(MIG_SRC),
                            py_label, ATTEST_LABEL)
    assert fired == [f"{py_label} is missing {['good_faith']} that {ATTEST_LABEL} allows"], fired

    assert shipped == []


def test_the_quoted_value_reader_fires_on_an_entry_it_could_not_parse():
    """If a value is ever spelled without quotes, the parity guard must not compare a shorter list.

    ``(copyright,'voice_likeness',...)`` has seven entries and six quoted tokens; taking the six and
    treating them as the whole list is exactly the always-green shape this refuses.
    """
    unquoted = mutate(MIG_SRC, "CHECK (grounds IN ('copyright',", "CHECK (grounds IN (copyright,")
    with pytest.raises(AssertionError, match="cannot parse every entry"):
        sql_check_list(unquoted, "grounds")
    assert len(sql_check_list(MIG_SRC, "grounds")) == 7


def test_the_door_guard_fires_when_a_public_door_gains_an_actor():
    """The defect in one line: the intake starts requiring somebody, and only the AST knows."""
    planted = edited_signature(function_node(MAIN_SRC, "file_rights_report"), add_guarded="actor")
    assert "Depends(get_actor)" in planted, planted
    fired = door_findings(planted, "file_rights_report", None)
    assert fired == ["file_rights_report is a public door but takes actor = Depends(get_actor)"], fired
    assert door_findings(MAIN_SRC, "file_rights_report", None) == []


def test_the_door_guard_fires_when_a_public_door_is_closed_by_the_router_instead():
    """No parameter changes at all, so a reader holding only the signature sees a clean door."""
    planted = decorated_route(function_node(MAIN_SRC, "file_rights_report"), "Depends(get_actor)")
    fired = door_findings(planted, "file_rights_report", None)
    assert len(fired) == 1 and "dependencies=[Depends(get_actor)]" in fired[0], fired
    assert door_findings(MAIN_SRC, "file_rights_report", None) == []


@pytest.mark.parametrize("name", STAFF_DOORS)
def test_the_door_guard_fires_when_a_staff_door_is_downgraded_or_stripped(name):
    """Both ways the queue stops being administrator-only, on copies rendered from the real signature."""
    real = function_node(MAIN_SRC, name)
    downgraded = edited_signature(real, downgrade_to="get_actor")
    assert ADMIN_GUARD not in downgraded, downgraded
    fired = door_findings(downgraded, name, ADMIN_GUARD)
    assert len(fired) == 1 and f"guards on get_actor, not {ADMIN_GUARD}" in fired[0], fired

    stripped = edited_signature(real, drop_guarded=True)
    assert "Depends" not in stripped, stripped
    fired = door_findings(stripped, name, ADMIN_GUARD)
    assert len(fired) == 1 and "no Depends(...) parameter at all" in fired[0], fired

    assert door_findings(MAIN_SRC, name, ADMIN_GUARD) == []


def test_the_attestation_default_guard_fires_when_silence_can_read_as_consent():
    order = module_string_list(MAIN_SRC, ATTEST_CONSTANT)
    statements, first, last = set(order), order[0], order[-1]
    assert len(order) >= 2, order

    # The consent hole itself: an omitted key arrives as True.
    flipped = mutate(MAIN_SRC, f"    attest_{first}: bool = False", f"    attest_{first}: bool = True")
    fired = attestation_findings(field_specs(flipped, BODY_MODEL), statements)
    assert len(fired) == 1 and fired[0].startswith(f"attest_{first}: default is 'True'"), fired

    # The same defect in the other spelling a pydantic model uses. A reader that understood only plain
    # defaults, or only Field(...) calls, makes one of these two arms invisible.
    fielded = mutate(MAIN_SRC, f"    attest_{last}: bool = False", f"    attest_{last}: bool = Field(True)")
    fired = attestation_findings(field_specs(fielded, BODY_MODEL), statements)
    assert len(fired) == 1 and fired[0].startswith(f"attest_{last}: default is 'True'"), fired

    # Required rather than defaulted False: not a consent hole, but a shape the handler at main.py:1059
    # cannot reach with `body.attest_*`, so it is reported as the drift it is instead of being waved at.
    required = mutate(MAIN_SRC, f"    attest_{first}: bool = False",
                      f"    attest_{first}: bool = Field(...)")
    fired = attestation_findings(field_specs(required, BODY_MODEL), statements)
    assert len(fired) == 1 and fired[0].startswith(f"attest_{first}: default is 'REQUIRED'"), fired

    # A non-bool annotation would let "no"/"" through and be truthy -- consent from a blank.
    untyped = mutate(MAIN_SRC, f"    attest_{first}: bool = False", f"    attest_{first}: str = ''")
    fired = attestation_findings(field_specs(untyped, BODY_MODEL), statements)
    assert any("not bool" in f for f in fired), fired

    # The vocabulary outgrew the model: the table demands a statement nothing on the body can carry.
    orphaned = mutate(MAIN_SRC, f"    attest_{last}: bool = False\n", "")
    fired = attestation_findings(field_specs(orphaned, BODY_MODEL), statements)
    assert fired == [f"attest_{last}: the table demands {last!r} but the intake body has no such field"], fired

    assert attestation_findings(field_specs(MAIN_SRC, BODY_MODEL), statements) == []


def test_the_email_pattern_guard_fires_on_a_missing_or_unanchored_pattern():
    """An unanchored pattern is present, non-empty, and useless: `re.search` finds it inside junk."""
    shipped = r'pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"'
    specs = field_specs(MAIN_SRC, BODY_MODEL)

    unanchored = mutate(MAIN_SRC, shipped, r'pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+"')
    fired = pattern_findings(field_specs(unanchored, BODY_MODEL), "reporter_email", EMAIL_PROBES)
    assert len(fired) == 1 and "junk user@example.com junk" in fired[0], fired

    removed = mutate(MAIN_SRC, ", " + shipped, "")
    fired = pattern_findings(field_specs(removed, BODY_MODEL), "reporter_email", EMAIL_PROBES)
    assert len(fired) == 1 and "declares no pattern" in fired[0], fired

    # An empty pattern is not "no pattern" to a reader that only checks whether the keyword is there:
    # it matches everything it is shown, the junk probe included.
    emptied = mutate(MAIN_SRC, shipped, 'pattern=""')
    fired = pattern_findings(field_specs(emptied, BODY_MODEL), "reporter_email", EMAIL_PROBES)
    assert len(fired) == 1 and "declares no pattern" in fired[0], fired

    absent = mutate(MAIN_SRC, '    reporter_email: str = Field(min_length=6, max_length=320, ',
                    "    reply_channel: str = Field(min_length=6, max_length=320, ")
    fired = pattern_findings(field_specs(absent, BODY_MODEL), "reporter_email", EMAIL_PROBES)
    assert fired == ["reporter_email: no such field on the intake body"], fired

    assert pattern_findings(specs, "reporter_email", EMAIL_PROBES) == []


def test_the_parameter_reader_aligns_defaults_from_the_end():
    """FastAPI puts the guard last; a front-aligned zip would name the wrong parameter as the doorkeeper.

    ``moderation_report_link`` is the real four-parameter case with one trailing default, and the planted
    copy adds two more defaults ahead of it -- the shape a front-aligned reader gets wrong. The last arm
    checks the attribute spelling of the same dependency, so the reader is not pinned to one import style.
    """
    seen = {name: dependency_name(dflt)
            for name, _ann, dflt in params(function_node(MAIN_SRC, "moderation_report_link"))}
    assert seen == {"report_id": None, "body": None, "request": None, "actor": ADMIN_GUARD}, seen

    three = functions("def moderation_report_link(report_id: str, body: dict = {},\n"
                      "                           request: Request = None,\n"
                      "                           actor: Actor = Depends(require_platform_admin)):\n"
                      "    ...\n")["moderation_report_link"]
    seen = {name: dependency_name(dflt) for name, _ann, dflt in params(three)}
    assert seen == {"report_id": None, "body": None, "request": None, "actor": ADMIN_GUARD}, seen

    # The attribute spelling of the same dependency: the reader must not be pinned to one import style,
    # and a public door closed that way has to be caught just as loudly.
    attributed = "def m(actor: Actor = fastapi.Depends(require_platform_admin)):\n    ...\n"
    assert door_dependencies(attributed, "m") == [ADMIN_GUARD], door_dependencies(attributed, "m")
    assert door_findings(attributed, "m", ADMIN_GUARD) == []
    assert door_findings(attributed, "m", None) == \
        ["m is a public door but takes actor = Depends(require_platform_admin)"]

    # ...and a plain default is not a dependency: the reader must not invent one.
    assert door_dependencies("def m(body: dict = {}):\n    ...\n", "m") == []
    # A body that merely mentions the word guards nothing.
    assert door_findings("def m(actor):\n    return Depends(require_platform_admin)\n", "m",
                         ADMIN_GUARD) == ["m is a staff door with no Depends(...) parameter at all "
                                         "-- anyone can call it"]
