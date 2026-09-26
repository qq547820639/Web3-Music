"""Which identity tables are protected by the database, and which only by privileges.

The G9-5 zero-leakage claim in the release record is carried by two different mechanisms, and the
difference is invisible from the outside: a row that RLS hides and a row that a never-issued GRANT
keeps unwritable both come back absent. This census pins which is which, from the migrations
themselves, so that a new cross-tenant read of an identity table cannot be added as though the
database were still the backstop:

  * db/migrations/001_production_candidate.sql enables and forces row level security over the sixteen
    business tables and writes policies for them. `users`, `workspaces` and `workspace_members` are
    not among them;
  * 001 grants music_app SELECT on all three of those, so reads are answered by whatever statement the
    application writes -- the isolation of a read is therefore the isolation of its WHERE clause;
  * writes to the trio are blocked by privileges that were never granted, which is the same wall as
    RLS for a `UPDATE ... WHERE` that matches nothing (it raises nothing either), so the guard for
    those writes is 017's and 018's SECURITY DEFINER functions, tested live in scripts/member_drill.py.

Each guard below ships with the sample that must make it fire.
"""
import pathlib
import re

import ast

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/migrations"
API = ROOT / "services/api"

# The three tables that hold who a person is and what they belong to, as opposed to what they made.
IDENTITY_TABLES = {"users", "workspaces", "workspace_members"}

ENABLE_RLS = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:[a-z_]+\.)?([a-z_]+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                        re.IGNORECASE)
FORCE_RLS = re.compile(r"ALTER\s+TABLE\s+(?:[a-z_]+\.)?([a-z_]+)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", re.IGNORECASE)
CREATE_POLICY = re.compile(r"CREATE\s+POLICY\s+[^;]+?ON\s+(?:[a-z_]+\.)?([a-z_]+)", re.IGNORECASE | re.DOTALL)
# The migrations spell these without the TABLE keyword (`GRANT SELECT ON users,workspaces TO music_app`),
# and the table list is identifiers separated by commas -- that shape is what keeps
# `GRANT USAGE ON SCHEMA public` and `ON ALL SEQUENCES IN SCHEMA public` out of the census.
GRANT_SELECT = re.compile(r"GRANT\s+([^;]+?)\s+ON\s+([a-z_]+(?:\s*,\s*[a-z_]+)*)\s+TO\s+([a-z_]+)",
                         re.IGNORECASE)

# A read of an identity table that carries no parameter at all answers for every tenant at once.
RAW_READ = re.compile(r"\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+(?:[a-z_]+\.)?"
                      r"(users|workspaces|workspace_members)\b", re.IGNORECASE)


def migration_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql")))


def rls_enabled_tables(text: str) -> set[str]:
    return set(ENABLE_RLS.findall(text))


def rls_policied_tables(text: str) -> set[str]:
    return set(CREATE_POLICY.findall(text))


def identity_literals(source: str) -> list[str]:
    """Every string literal in a module that reads an identity table, whitespace-flattened.

    Literals, not lines: the API writes SQL across several lines, and a line-based scan splits
    `FROM workspace_members m JOIN users u` from the `WHERE ... = %s` that scopes it three lines
    further down -- measured, it reported three of the twelve reads as unscoped when they are not.
    """
    tree = ast.parse(source)
    return [" ".join(node.value.split()) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and RAW_READ.search(node.value)]


def grants_by_table(text: str) -> dict[tuple[str, str], set[str]]:
    """(table, role) -> the privileges that migration hands out, as one map over every GRANT line."""
    found: dict[tuple[str, str], set[str]] = {}
    for match in GRANT_SELECT.finditer(text):
        privileges, tables, role = match.groups()
        for table in re.split(r"[,\s]+", tables.strip()):
            if not table:
                continue
            bucket = found.setdefault((table.lower(), role.lower()), set())
            bucket.update(word.strip().lower() for word in privileges.split(",") if word.strip())
    return found


def unparameterised(literals: list[str]) -> list[str]:
    """Reads that name an identity table without a single bound value in them."""
    offenders = []
    for text in literals:
        if "%s" in text or "$1" in text or "{" in text:
            continue                     # bound, or interpolated by the caller's f-string
        if re.search(r"\b(WHERE|USING)\b", text, re.IGNORECASE):
            continue
        offenders.append(text)
    return offenders


# ------------------------------------------------------------------ the census ----

def test_no_migration_names_the_trio_in_a_security_statement():
    """The migrations never write `ALTER TABLE users ENABLE ROW LEVEL SECURITY` or a policy for the trio.

    The honest limit of this guard, stated where someone will read it: 001 turns RLS on through a plpgsql
    loop over an array of table names (`EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl)`),
    which no static reader can resolve -- measured, the regex sees five literal ALTER statements while the
    live database reports many more relations with relrowsecurity, and the migrations keep adding covered
    tables. So this test is a negative check with a declared blind spot, and the figures that carry weight
    are the live ones in scripts/member_drill.py, which reads pg_class, pg_policies and
    information_schema.role_table_grants directly.
    """
    text = migration_text()
    assert rls_enabled_tables(text), "the reader found no ENABLE statement at all, so it cannot clear anything"
    assert not (rls_enabled_tables(text) & IDENTITY_TABLES)
    assert not (rls_policied_tables(text) & IDENTITY_TABLES)


def test_identity_tables_have_no_policies_at_all():
    policied = rls_policied_tables(migration_text())
    assert policied - IDENTITY_TABLES, "the policy reader found nothing, so it cannot clear the trio either"
    hit = sorted(policied & IDENTITY_TABLES)
    assert not hit, f"a policy now exists for {hit} -- see the note above"


def test_the_rls_reader_fires_on_a_policy_for_an_identity_table():
    synthetic = ("ALTER TABLE users ENABLE ROW LEVEL SECURITY;\n"
                 "CREATE POLICY self_read ON users FOR SELECT USING (id = current_user_id());\n")
    assert rls_enabled_tables(synthetic) == {"users"}
    assert rls_policied_tables(synthetic) == {"users"}
    assert not (rls_enabled_tables(migration_text()) & IDENTITY_TABLES)


def test_001_hands_the_application_role_select_on_the_trio():
    grants = grants_by_table(migration_text())
    for table in sorted(IDENTITY_TABLES):
        held = grants.get((table, "music_app"), set())
        assert held, f"music_app holds no table grant on {table} -- re-read this test's premise"
        assert held <= {"select"}, f"music_app can now write {table} directly: {sorted(held)}"


def test_the_grant_reader_fires_on_a_write_privilege():
    synthetic = ("GRANT SELECT ON users, workspaces TO music_app;\n"
                 "GRANT SELECT, UPDATE ON workspace_members TO music_app;\n"
                 "GRANT USAGE ON SCHEMA public TO music_app,music_worker;\n"
                 "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO music_app;\n")
    grants = grants_by_table(synthetic)
    assert grants[("workspace_members", "music_app")] == {"select", "update"}
    assert grants[("users", "music_app")] == {"select"}
    assert grants[("workspaces", "music_app")] == {"select"}
    assert not [key for key in grants if "schema" in key[0] or "public" in key[0] or "all" in key[0]], sorted(grants)


def test_every_raw_identity_read_in_the_api_carries_a_bound_value():
    """The one property that keeps the trio isolated while it has no policies: each statement names the
    caller's own id (or the session's workspace), so there is no such thing as an unscoped read."""
    offenders = []
    seen = 0
    for path in sorted(API.rglob("*.py")):
        literals = identity_literals(path.read_text(encoding="utf-8"))
        seen += len(literals)
        for text in unparameterised(literals):
            offenders.append(f"{path.relative_to(ROOT)}: {text}")
    assert seen >= 9, f"only {seen} identity reads were found at all, so this census is looking at nothing"
    assert not offenders, "identity reads with no bound value: " + " | ".join(offenders[:6])


def test_the_unscoped_read_detector_fires_on_a_platform_wide_select():
    scoped = 'cur.execute("SELECT id FROM users WHERE status = %s", ("active",))'
    wide = 'cur.execute("SELECT id, email FROM users")'
    split = ('cur.execute("""SELECT m.role FROM workspace_members m\n'
             '    JOIN users u ON u.id = m.user_id\n'
             '    WHERE m.workspace_id = %s""")')
    assert not unparameterised(identity_literals(scoped))
    assert unparameterised(identity_literals(wide)) == ["SELECT id, email FROM users"]
    # Why the unit of the scan is a literal and not a line: the line that names workspace_members
    # carries no predicate at all, so a line-based census reports three of this repository's twelve
    # identity reads as unscoped. The flattened literal has the WHERE clause in it, and that is the
    # statement the database actually sees.
    first_line = identity_literals(split)[0].split(" JOIN ")[0]
    assert "workspace_members" in first_line and "%s" not in first_line
    assert not unparameterised(identity_literals(split))
