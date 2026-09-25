"""Static guards on the export's table list, which is interpolated into SQL.

The live coverage check (census against information_schema) is scripts/erasure_drill.py's job and
needs a stack. These three need no database and catch the things the drill cannot see from
outside: a triple that would blow up the unpack, a name that would land inside an f-string
SELECT, and a duplicated key that would silently overwrite rows in the export payload.
"""
import ast
import pathlib
import re

MAIN = pathlib.Path(__file__).resolve().parents[2] / "services/api/app/main.py"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def module():
    return ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))


def literal_assigned(name):
    for node in module().body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a module-level literal assignment in main.py")


def test_personal_tables_are_string_triples():
    for entry in literal_assigned("PERSONAL_TABLES"):
        assert isinstance(entry, tuple) and len(entry) == 3, entry
        assert all(isinstance(part, str) for part in entry), entry


def test_names_interpolated_into_sql_are_bare_identifiers():
    # The query is f"SELECT * FROM {table} WHERE {tenant}=%s AND {column}::text=%s", so anything
    # the regex rejects is not a column name but a fragment of SQL.
    for table, column, tenant in literal_assigned("PERSONAL_TABLES"):
        for name in (table, column, tenant):
            assert IDENTIFIER.match(name), f"{name!r} is not a safe identifier"


def test_no_table_column_pair_is_listed_twice():
    keys = [(table, column) for table, column, _ in literal_assigned("PERSONAL_TABLES")]
    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"duplicated keys would overwrite each other in the export payload: {sorted(duplicates)}"


def test_export_coverage_derives_from_the_table_list_and_names_the_rest():
    for node in module().body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXPORT_COVERAGE" for t in node.targets):
            break
    else:
        raise AssertionError("EXPORT_COVERAGE not found in main.py")
    # Shape: tuple(f"{table}.{column}" for ... in PERSONAL_TABLES) + (literal tail).
    value = node.value
    assert isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add), ast.dump(value)
    derived, tail = value.left, [e.value for e in value.right.elts]
    assert isinstance(derived, ast.Call) and isinstance(derived.args[0], ast.GeneratorExp), \
        "coverage must be built from PERSONAL_TABLES rather than restated by hand"
    # The literal tail is what makes the four separately-exported stores checkable by the drill.
    assert {"users.id", "auth_sessions.user_id", "user_preferences.user_id",
            "product_events.user_id", "workspace_members.user_id"} <= set(tail), tail
