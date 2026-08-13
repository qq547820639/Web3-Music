from copy import deepcopy
from typing import Any

class PatchError(ValueError):
    pass


def _parts(pointer: str):
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"invalid JSON pointer: {pointer}")
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]


def touches_locked(path: str, locked_paths: list[str]) -> bool:
    for locked in locked_paths:
        if path == locked or path.startswith(locked.rstrip("/") + "/") or locked.startswith(path.rstrip("/") + "/"):
            return True
    return False


def get_pointer(doc: Any, pointer: str):
    cur = doc
    for part in _parts(pointer):
        try:
            if isinstance(cur, list):
                cur = cur[int(part)]
            else:
                cur = cur[part]
        except (IndexError, ValueError, KeyError, TypeError) as exc:
            raise PatchError(f"path not found: {pointer}") from exc
    return cur


def apply_patch(document: dict, operations: list[dict], locked_paths: list[str] | None = None) -> dict:
    locked_paths = locked_paths or []
    result = deepcopy(document)
    for op in operations:
        action = op.get("op")
        path = op.get("path")
        if action not in {"add", "replace", "remove"} or not isinstance(path, str):
            raise PatchError("only add/replace/remove with a JSON pointer path are supported")
        if touches_locked(path, locked_paths):
            raise PatchError(f"path is locked: {path}")
        parts = _parts(path)
        if not parts:
            if action == "remove":
                raise PatchError("cannot remove document root")
            if not isinstance(op.get("value"), dict):
                raise PatchError("root replacement must be an object")
            result = deepcopy(op["value"])
            continue
        parent = result
        for part in parts[:-1]:
            try:
                if isinstance(parent, list):
                    parent = parent[int(part)]
                else:
                    if part not in parent:
                        if action == "add":
                            parent[part] = {}
                        else:
                            raise PatchError(f"path not found: {path}")
                    parent = parent[part]
            except PatchError:
                raise
            except (IndexError, ValueError, KeyError, TypeError) as exc:
                raise PatchError(f"path not found: {path}") from exc
        key = parts[-1]
        if isinstance(parent, list):
            try:
                if key == "-" and action == "add":
                    parent.append(deepcopy(op.get("value")))
                else:
                    index = int(key)
                    if action == "remove":
                        parent.pop(index)
                    elif action == "replace":
                        parent[index] = deepcopy(op.get("value"))
                    else:
                        if index > len(parent):
                            raise PatchError(f"invalid array index for path: {path}")
                        parent.insert(index, deepcopy(op.get("value")))
            except PatchError:
                raise
            except (IndexError, ValueError, TypeError) as exc:
                raise PatchError(f"invalid array index for path: {path}") from exc
        else:
            if action == "remove":
                if key not in parent:
                    raise PatchError(f"path not found: {path}")
                del parent[key]
            elif action == "replace":
                if key not in parent:
                    raise PatchError(f"path not found: {path}")
                parent[key] = deepcopy(op.get("value"))
            else:
                parent[key] = deepcopy(op.get("value"))
    return result
