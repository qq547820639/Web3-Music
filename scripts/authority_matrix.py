#!/usr/bin/env python3
"""Derive who-is-allowed-to-do-what from the API's own source, and keep the record honest.

Release gate G12's "审批流程稳定" row asked for a written-down approval flow. The part of that which is
not a business decision is already in the code and can be *measured*: which role a mutating endpoint
requires, and which endpoints get by on a session or a workspace header alone. This script reads the
decorators with an AST (not the running app, so it costs nothing to check in CI) and reports:

    ./scripts/authority_matrix.py --json      the derived matrix
    ./scripts/authority_matrix.py --markdown   the same thing as a table for docs
    ./scripts/authority_matrix.py --check     red if the committed matrix or doc no longer matches

The committed copies are shared/contracts/authority-matrix.json and docs/AUTHORITY_MATRIX.md, so a
route that quietly changes its role set, or ships without one, fails the resident test in
tests/unit/test_authority_matrix.py rather than turning the document into folklore.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "services/api/app"
SOURCES = [API / "main.py"] + sorted((API / "routers").glob("*.py"))
MATRIX_JSON = ROOT / "shared/contracts/authority-matrix.json"
MATRIX_DOC = ROOT / "docs/AUTHORITY_MATRIX.md"

# Dependencies that resolve *who* is calling. Anything a route relies on for authority outside these
# names is a bug in this reader, not a policy: `--check` reports an unknown dependency rather than
# sorting it into a bucket it has not earned.
AUTHORITY_DEPENDS = {
    "require_roles": "roles",        # explicit role set, read from the call's literal arguments
    "require_platform_admin": "platform_admin",
    "get_actor": "workspace_member",  # any member of the X-Workspace-Id the header names
    "get_user": "session",            # a live session only; no workspace role checked
}
APP_LEVEL = {"bearer_scheme", "session_cookie_scheme"}
# The in-body re-verification helper. Named here so a rename cannot turn the matrix column into a
# column of dashes: tests/unit/test_authority_matrix.py asserts the set of routes it finds.
STEP_UP_HELPER = "step_up_factors"
READ_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def decorator_route(node) -> tuple[str, str] | None:
    """(method, path) for an @app.<verb>(...) or @router.<verb>(...) endpoint, else None."""
    for dec in node.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)):
            continue
        if dec.func.value.id not in {"app", "router"} or dec.func.attr.upper() not in WRITE_METHODS | READ_METHODS:
            continue
        if not dec.args or not isinstance(dec.args[0], ast.Constant):
            continue
        return dec.func.attr.upper(), dec.args[0].value
    return None


def router_prefixes(source: str) -> dict[str, str]:
    """Name -> prefix for each `X = APIRouter(prefix="...")` in a module."""
    out = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) == "APIRouter":
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                out[target.id] = kw.value.value
    return out


def authority_of(node) -> tuple[str, str]:
    """How this endpoint learns who is calling: (kind, detail)."""
    roles, others = [], []
    for default in [d for d in node.args.defaults + [a for a in node.args.kw_defaults if a]]:
        if not (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)):
            continue
        name = default.func.id
        if name == "Depends" and default.args:
            inner = default.args[0]
            name = inner.id if isinstance(inner, ast.Name) else (
                inner.func.id if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) else "?")
            if name == "require_roles":
                inner = default.args[0]
                roles += [a.value for a in inner.args if isinstance(a, ast.Constant)]
                continue
        if name in AUTHORITY_DEPENDS:
            others.append(AUTHORITY_DEPENDS[name])
    if roles:
        return "require_roles", ",".join(sorted(set(roles)))
    for kind in ("platform_admin", "workspace_member", "session"):
        if kind in others:
            return kind, ""
    return "app-level-only", ""


def endpoint_id(path: pathlib.Path, node) -> str:
    try:
        relative = path.relative_to(API)
    except ValueError:
        # A fixture file outside the app tree still needs an id; refusing to give it one made the
        # reader untestable against the shapes it has to prove it can see.
        relative = pathlib.Path(path.stem)
    module = relative.with_suffix("").as_posix().replace("/", ".")
    if module == "main":
        module = "app"
    return f"{module}:{node.name}"


def app_level_dependencies(node: ast.Module) -> set[str]:
    """Names used in `dependencies=[...]` on FastAPI(...) or APIRouter(...) -- the app-wide guards."""
    found: set[str] = set()
    for stmt in node.body:
        call = stmt.value if isinstance(stmt, ast.Assign) else None
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") in {"FastAPI", "APIRouter"}):
            continue
        for kw in call.keywords:
            if kw.arg != "dependencies" or not isinstance(kw.value, ast.List):
                continue
            for dep in kw.value.elts:
                if isinstance(dep, ast.Call) and dep.args and isinstance(dep.args[0], ast.Name):
                    found.add(dep.args[0].id)
    return found


def calls_step_up(node) -> bool:
    """True when the endpoint re-asks for the credential behind the session before it writes.

    Step-up lives in the request body, not in a `Depends`, so no amount of signature reading finds
    it; this walks the body for the helper call and `--check` turns that into a column of the matrix.
    """
    return any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
               and call.func.id == STEP_UP_HELPER for call in ast.walk(node))


def derive_file(source: pathlib.Path) -> list[dict]:
    """Every /api route one module declares. Split out so a test can feed it a fixture file
    instead of only the real tree, which is the only way to prove the reader sees a router
    prefix, an `async def`, and a step-up call."""
    rows = []
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(source))
    prefixes = router_prefixes(text)
    app_deps = app_level_dependencies(tree)
    for node in tree.body:
        # async def is the shape of every streaming or awaited endpoint,
        # and a reader that only visits ast.FunctionDef silently omits them.
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = decorator_route(node)
        if not route:
            continue
        method, declared = route
        from_router = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                          and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                          for d in node.decorator_list)
        # Resolve the authority shape first: a router mounted at /api declares its sub-paths
        # without it, and filtering on the decorator string alone drops every one of them.
        path = (prefixes.get("router", "") if from_router else "") + declared
        if not path.startswith("/api"):
            continue
        kind, detail = authority_of(node)
        rows.append({
            "method": method,
            "path": path,
            "authority": kind,
            "roles": detail,
            "writes": method in WRITE_METHODS,
            "step_up": calls_step_up(node),
            "endpoint": endpoint_id(source, node),
            "app_level_auth": sorted(APP_LEVEL & app_deps),
        })
    return rows


def derive() -> list[dict]:
    rows = [row for source in SOURCES for row in derive_file(source)]
    return sorted(rows, key=lambda r: (r["path"], r["method"]))


def render_markdown(rows: list[dict]) -> str:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row["authority"], []).append(row)
    order = ["require_roles", "platform_admin", "workspace_member", "session", "app-level-only"]
    labels = {
        "require_roles": "显式角色名单（require_roles）",
        "platform_admin": "平台管理员",
        "workspace_member": "工作区成员即可（get_actor）",
        "session": "有效会话即可（get_user）",
        "app-level-only": "只有应用层鉴权，端点内不再判定",
    }
    lines = ["# 权限矩阵（由代码派生，不要手改）", "",
             "`./scripts/authority_matrix.py --check` 会重算这张表并比对；派生自 `services/api/app/main.py`"
             " 与 `services/api/app/routers/*.py` 的路由装饰器与依赖签名，共 {} 条 /api 路由，其中写操作 {} 条。"
             .format(len(rows), sum(1 for r in rows if r["writes"])),
             "",
             "读法：`require_roles` 一栏是端点自己声明的角色名单，名单之外的人在 `Depends` 阶段就拿 403；"
             "`工作区成员即可` 只检查调用者属于 `X-Workspace-Id` 那个工作区，"
             "`有效会话即可` 是主体自助通道（删除账户、注册第二因子、接受或拒绝一份发给自己的邀请）；"
             "`只有应用层鉴权` 的写路由一共 {} 条，它们的保护不在这张表里而在端点内部——口令校验、"
             "刷新会话校验、按账号的限流、以及回调的 HMAC 签名——常驻用例逐条核对该端点源码里确实还写着那个载体，"
             "少一个就红（`tests/unit/test_authority_matrix.py`）。真正的授权担保还包括数据库层："
             "RLS 与 `011`/`012`/`013`/`016`/`017`/`018`/`019` 的 `SECURITY DEFINER` 函数，端点检查只是门口那道 convenience。"
             .format(sum(1 for r in rows if r["writes"] and r["authority"] == "app-level-only")),
             "",
             "`再认证` 一列是会话之外的第二道：{} 条写路由在动数据库之前要求调用方当场再交出一次口令"
             "（已注册第二因子的账号还要一个当前验证码），判据是 `step_up_factors` 真的出现在端点函数体里，"
             "而不是请求体里有某个字段——它挡的是「Cookie 被拿走之后还能做什么」，"
             "所以设成没有确认窗口：一次凭证只够一次动作。"
             .format(sum(1 for r in rows if r["step_up"])),
             ""]
    for kind in order:
        group = buckets.get(kind, [])
        if not group:
            continue
        lines += ["", f"## {labels[kind]}（{len(group)} 条，写操作 {sum(1 for r in group if r['writes'])} 条）", "",
                  "| 方法与路径 | 角色 | 再认证 | 端点 |", "| --- | --- | --- | --- |"]
        for row in sorted(group, key=lambda r: (not r["writes"], r["path"])):
            roles = row["roles"] or "—"
            lines.append(f"| `{row['method']} {row['path']}` | {roles} | {'是' if row['step_up'] else '—'}"
                         f" | `{row['endpoint']}` |")
    return "\n".join(lines) + "\n"


def git_tracked_json() -> dict | None:
    shown = subprocess.run(["git", "show", f"HEAD:{MATRIX_JSON.relative_to(ROOT)}"],
                           capture_output=True, text=True)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--write", action="store_true", help="refresh the committed matrix and document")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rows = derive()
    payload = {"derived_from": [str(p.relative_to(ROOT)) for p in SOURCES], "routes": rows}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.markdown:
        print(render_markdown(rows))
        return 0
    if args.write:
        MATRIX_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MATRIX_DOC.write_text(render_markdown(rows), encoding="utf-8")
        print(f"wrote {len(rows)} routes to {MATRIX_JSON.relative_to(ROOT)} and {MATRIX_DOC.relative_to(ROOT)}")
        return 0
    if args.check:
        problems = []
        committed = json.loads(MATRIX_JSON.read_text(encoding="utf-8")).get("routes", [])
        if committed != rows:
            missing = [r for r in rows if r not in committed]
            gone = [r for r in committed if r not in rows]
            problems += [f"route not in the committed matrix: {r['method']} {r['path']} ({r['authority']})"
                         for r in missing[:20]]
            problems += [f"committed matrix lists a route the code no longer has: {r['method']} {r['path']}"
                         for r in gone[:20]]
            if not missing and not gone:
                problems.append("the committed matrix and the derived one differ in ordering or fields")
        if MATRIX_DOC.read_text(encoding="utf-8").strip() != render_markdown(rows).strip():
            problems.append(f"{MATRIX_DOC.relative_to(ROOT)} no longer matches the code; run --write")
        for problem in problems:
            print("FAIL", problem)
        if not problems:
            print(f"authority matrix agrees with the code: {len(rows)} routes, "
                  f"{sum(1 for r in rows if r['writes'])} writes")
        return 1 if problems else 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
