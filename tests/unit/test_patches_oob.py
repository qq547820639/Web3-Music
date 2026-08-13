"""Regression tests for the JSON Patch out-of-bounds fix (A1).

Before the fix, an out-of-range array index raised IndexError/ValueError and
surfaced as a 500. After the fix, every one of these paths must raise
``PatchError`` (mapped to 409 in the HTTP layer) instead.
"""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "services/api/app/domain"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


patches = _load("patches_oob_under_test", DOMAIN / "patches.py")


def test_replace_out_of_bounds_raises_patch_error():
    doc = {"sections": ["intro", "verse"]}
    try:
        patches.apply_patch(doc, [{"op": "replace", "path": "/sections/5", "value": "x"}])
    except patches.PatchError:
        return
    raise AssertionError("expected PatchError for replace at out-of-bounds index")


def test_add_out_of_bounds_raises_patch_error():
    doc = {"sections": ["a"]}
    try:
        patches.apply_patch(doc, [{"op": "add", "path": "/sections/3", "value": "x"}])
    except patches.PatchError:
        return
    raise AssertionError("expected PatchError for add beyond array length")


def test_remove_out_of_bounds_raises_patch_error():
    doc = {"sections": ["a"]}
    try:
        patches.apply_patch(doc, [{"op": "remove", "path": "/sections/3"}])
    except patches.PatchError:
        return
    raise AssertionError("expected PatchError for remove at out-of-bounds index")


def test_get_pointer_out_of_bounds_raises_patch_error():
    doc = {"sections": ["a"]}
    try:
        patches.get_pointer(doc, "/sections/9")
    except patches.PatchError:
        return
    raise AssertionError("expected PatchError for get_pointer at out-of-bounds index")


def test_append_via_dash_still_works():
    doc = {"sections": ["a"]}
    out = patches.apply_patch(doc, [{"op": "add", "path": "/sections/-", "value": "b"}])
    assert out["sections"] == ["a", "b"]


def test_add_at_end_index_still_works():
    doc = {"sections": ["a"]}
    out = patches.apply_patch(doc, [{"op": "add", "path": "/sections/1", "value": "b"}])
    assert out["sections"] == ["a", "b"]
