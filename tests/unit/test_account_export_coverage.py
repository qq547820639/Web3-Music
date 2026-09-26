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
    # The literal tail is what makes the separately-exported stores checkable by the drill, and a
    # starred element is not a string: read it as what it is rather than letting it slip through a
    # set comparison against ast nodes (which is exactly how this assertion survived the first time
    # the tail grew a derived part).
    literals = [element.value for element in value.right.elts if isinstance(element, ast.Constant)]
    starred = [element for element in value.right.elts if isinstance(element, ast.Starred)]
    assert {"auth_sessions.user_id", "user_preferences.user_id", "product_events.user_id",
            "workspace_members.user_id"} <= set(literals), literals
    assert "users.id" in literals or any("users.id" in str(node) for node in starred), literals
    assert len(starred) == 1, "the users.* coverage entries must be derived, not restated by hand"
    names = [node.id for node in ast.walk(starred[0]) if isinstance(node, ast.Name)]
    assert "USER_ACCOUNT_COLUMNS" in names, ast.dump(starred[0])


def test_account_columns_are_bare_identifiers_and_do_not_repeat_the_key():
    """They are joined straight into the account SELECT, and 'id' is already listed separately."""
    columns = literal_assigned("USER_ACCOUNT_COLUMNS")
    assert all(isinstance(name, str) and IDENTIFIER.match(name) for name in columns), columns
    assert "id" not in columns, "users.id is its own coverage entry"
    assert len(columns) == len(set(columns)), "a repeated column would appear twice in the SELECT"
    # The second factor is personal data about the account; the export naming it is the fix for a
    # subject-access response that omitted 015's six columns because nothing demanded them.
    assert any(name.startswith("mfa_") for name in columns), columns
