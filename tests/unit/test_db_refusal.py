"""What a refusal from the database becomes on the wire, and which doors are obliged to translate it.

Why this is here rather than only in the live drill: the mapper lived in ``app.main``, and when it
moved to ``app.common`` the function came across and its ``HTTPException`` import did not. That module
uses ``from __future__ import annotations``, so the return annotation was never evaluated -- the file
imported, 108 routes registered, the unit ladder and the container build both went green, and the
first caller who was actually refused received a 500 whose only trace was
``NameError: name 'HTTPException' is not defined`` in the container log. The live drill
(``scripts/report_drill.py``, "and only once: a second link is refused with the database's own
reason") caught it. What this file adds is the same catch with no stack: it calls the function.

The mappings are checked by calling, not by reading, for exactly that reason -- an AST reader would
have found a perfectly well-formed body. The last two tests are the wiring the mapping depends on:
the endpoints that run a mutating migration function have to catch ``psycopg2.Error`` itself, and not
one subclass of it, which is the narrower thing that let a 23505 "linked once" walk past into a 500.
"""
import ast
import pathlib
import sys
from types import SimpleNamespace

import psycopg2
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

from app.common import _db_refusal  # noqa: E402

MAIN = ROOT / "services/api/app/main.py"
MARKET = ROOT / "services/api/app/routers/market.py"
COMMON = ROOT / "services/api/app/common.py"


def refused(code, message: str, constraint: str | None = None) -> psycopg2.Error:
    """A real ``psycopg2.Error`` carrying one chosen SQLSTATE.

    ``pgcode`` is read-only on an instance and ``None`` on a hand-built one -- libpq fills it when it
    raises -- so the code goes on the subclass, which is how psycopg2's own ``errors`` classes carry
    theirs. Probed on this machine rather than assumed: ``psycopg2.errors.CheckViolation('m').pgcode``
    is None, while ``class E(psycopg2.Error): pgcode = '23514'`` reads back '23514'.
    """
    namespace = {"pgcode": code}
    if constraint is not None:
        namespace["diag"] = SimpleNamespace(constraint_name=constraint)
    return type("Refused", (psycopg2.Error,), namespace)(message)


@pytest.mark.parametrize("code, expected", [
    ("22023", 422),   # a bad enumeration, RAISE'd by the function itself
    ("P0002", 404),   # "that row does not exist"
    ("42501", 403),   # "you are not the role that may"
    ("23514", 422),   # a CHECK the request model did not predict
    ("23505", 409),   # "this has already settled" -- and the default for anything unlisted
])
def test_a_refusal_becomes_the_status_its_code_means(code, expected):
    status = _db_refusal(refused(code, f"refused ({code})")).status_code
    assert status == expected, f"{code} answered {status}, expected {expected}"


def test_a_code_that_means_nothing_is_not_flattened_into_a_refusal():
    """An unexpected SQLSTATE stays whatever the caller said faults are, so a bug cannot hide as a 409."""
    assert _db_refusal(refused("53300", "too many connections"), default=503).status_code == 503
    assert _db_refusal(refused(None, "connection already closed")).status_code == 409


def test_a_check_violation_names_the_constraint_and_not_the_table():
    """The constraint names are ours to publish (021 chose them); the table behind them is not."""
    name = "report_work_identification_present"
    exc = refused("23514", f'new row for relation "rights_reports" violates check constraint "{name}"',
                  constraint=name)
    detail = _db_refusal(exc).detail
    assert isinstance(detail, dict) and detail["constraint"] == name, detail
    assert "rights_reports" not in str(detail), f"the response leaked the table: {detail}"


def function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def psycopg2_handlers(fn: ast.AST) -> list[ast.ExceptHandler]:
    """Every handler in `fn` that names something under the psycopg2 module."""
    out = []
    for handler in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.type is not None]:
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        for node in types:
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id == "psycopg2":
                out.append(handler)
                break
    return out


def names_base_error(handler: ast.ExceptHandler) -> bool:
    """True only for `psycopg2.Error` itself.

    Matching "Error" as a substring would read ``psycopg2.IntegrityError`` as the base class, and the
    guard would then pass on the very shape that is the defect -- so this compares the attribute name.
    """
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Attribute) and n.attr == "Error" for n in types)


def reports_the_refusal(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_db_refusal"
               for c in ast.walk(handler))


# Each of these runs a SECURITY DEFINER function that writes, so each can be refused by the database
# for a reason the caller can act on. The last is the pre-existing one that started this: the
# brand-award door let a RAISE reach the browser as a 500.
GUARDED = [(MAIN, "file_rights_report"), (MAIN, "moderation_report_link"), (MARKET, "award_submission")]


@pytest.mark.parametrize("path, name", GUARDED)
def test_the_door_that_writes_catches_the_base_error_and_reports_it(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = function(tree, name)
    if body is None:
        pytest.fail(f"{path.name} no longer defines {name}() -- update GUARDED rather than lose the door")
    catching = psycopg2_handlers(body)
    base = [h for h in catching if names_base_error(h)]
    assert base, (f"{name()} catches {[ast.unparse(h.type) for h in catching] or 'nothing'} -- with no "
                  f"psycopg2.Error a database refusal reaches the client as a 500")
    assert any(reports_the_refusal(h) for h in base), \
        f"{name()} catches psycopg2 errors but never calls the mapper, so it answers them with its own words"


def test_the_mapper_is_reachable_from_both_doors_without_a_cycle():
    """``routers/market.py`` cannot import from ``main``: main imports the router, so the shared helper
    has to live in ``common`` -- and this is the assertion that notices if it moves back."""
    for path, label in ((MAIN, "main.py"), (MARKET, "routers/market.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names if alias.name == "_db_refusal"]
        assert imported == ["_db_refusal"], f"{label} does not import the mapper"
        back = [n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[-1] == "main"]
        assert not back, f"{label} imports {back} -- that is the cycle the move was meant to break"
    defined = [n.name for n in ast.walk(ast.parse(COMMON.read_text(encoding="utf-8")))
               if isinstance(n, ast.FunctionDef)]
    assert "_db_refusal" in defined, "the mapper no longer lives in app/common.py"
