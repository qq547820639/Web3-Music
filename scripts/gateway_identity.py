#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给容量腿补一道身份门：这个端口真的归被测网关吗？

《低成本 500 并发》那份文档早就写过「压测客户端要搬到被测资源之外」，2026-09-28 照做时两次都被端口的
归属否掉：`127.0.0.1:8080` 属于另一个 compose 项目（`qqi-final-backend-1`，它的 /api/auth/login 要求
`body.userId`，本平台这条端点收的是 email/password）；换到 compose 自己报出来的 18080 之后，两支 500×2
读数里那 116 + 89 个 502 在被测栈的日志里一个都不存在（网关记了 1795 行 /api/projects、502 为 0），
而 `lsof` 显示除 Docker 的 `0.0.0.0:18080->80` 之外，还有一个 ssh 进程在 `*:18080` 上监听。端口号是
compose 给的，不等于流量真的到了它。

浏览器腿对同一件事早有对策：`scripts/browser_a11y.py` 的 `check_origins` 把首屏字节交回仓库里的
index.html 比对。本文件是它的容量侧同形物，三条判据合起来才叫「这个端口回答的就是本仓库」：

1. 该端口只有一个监听者（`lsof -nP -iTCP:<port> -sTCP:LISTEN`，去重到「命令+PID」）。
2. 它 `/openapi.json` 的路径表覆盖 `shared/contracts/openapi-v13.json` 声明的 90 条端点。
3. 它确实在本仓库自己的 compose 项目发布的 `gateway:80` 端口上（`docker compose port`）——回答本站的
   接口不等于就是本站：同一份镜像再起一个网关，前两条都会通过。

三条都是「读不到就判不通过」：看不见不等于清白。
两道已知下限（写在这里而不是藏在代码里）：①`lsof` 是以本用户身份读的，别人用户下的监听者可能根本不出现在清单里，
所以「一个认领者」是必要条件而非充分条件；②本站的 `requestfinished` 若因浏览器内部原因没落地，`outstanding`
会留一个正数——那会让一次本不该降级的 shortfall 被降级，因此第 3 轴与契约覆盖都必须在场，单靠 unsettled 一条不够。用法（宿主侧压测之前跑一次，非零退码就别开压）：

    published=$(docker compose -f docker-compose.yml -f docker-compose.capacity500.yml port gateway 80 | cut -d: -f2)
    python3 scripts/gateway_identity.py --base-url "http://127.0.0.1:$published" --port "$published"
    python3 scripts/gateway_identity.py --self-test
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "shared/contracts/openapi-v13.json"


def contract_paths(contract: dict | None = None) -> set[str]:
    """The endpoint set this repository claims to serve, read from the tracked contract."""
    if contract is None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return set(contract.get("paths") or {})


def listener_claims(lsof_text: str) -> list[str]:
    """One label per distinct claimant of the port, from `lsof -iTCP:<port> -sTCP:LISTEN` output.

    Header line dropped, rows deduplicated on command+pid: a single process holding both the IPv4 and the
    IPv6 socket is one claimant, not two, and calling it two would refuse a clean port.
    """
    rows = [line.split() for line in lsof_text.splitlines()[1:] if line.strip()]
    return sorted({f"{parts[0]}({parts[1]})" for parts in rows if len(parts) >= 2})


def served_paths(body: str) -> set[str] | None:
    """The path set of whatever answered /openapi.json, or None when it is not an OpenAPI document."""
    try:
        doc = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(doc, dict) or "paths" not in doc:
        return None
    return set(doc.get("paths") or {})


def published_ports(compose_files: list[str]) -> list[str] | None:
    """The host ports THIS repository's compose project publishes for gateway:80, or None when unreadable.

    Answering our contract is not yet proof of being *our* station: measured 2026-09-28 23:06, a second
    container started from this project's own gateway image on its own network, published on host :19085,
    served /openapi.json with all 90 paths and had a single `lsof` claimant -- axes 1 and 2 both let it
    through. The tie an accidental clone cannot copy is the one the project itself reports: which port it
    published. So the tested port has to be that one, and axis 3 is what refuses the clone (rc=1, message
    naming the port it was looking for).
    """
    cmd = ["docker", "compose"]
    for f in compose_files:
        cmd += ["-f", f]
    cmd += ["port", "gateway", "80"]
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if done.returncode != 0:
        return None
    return sorted({line.rsplit(":", 1)[-1].strip() for line in done.stdout.splitlines() if line.strip()})


def judge(claims: list[str] | None, served: set[str] | None, contract: set[str],
          port: str | None = None, published: list[str] | None = None) -> list[str]:
    """Every way this port fails to be the measured gateway, as sentences.

    `claims=None` means the listener reading could not be taken at all (no lsof, or it errored): that is a
    refusal, not a pass -- the whole point of the gate is that an unattributed port makes the reading
    worthless, and "we could not look" must not buy a green.
    """
    problems = []
    if claims is None:
        problems.append("读不到该端口的监听者清单（lsof 不可用或报错），身份门无法放行——看不见不等于清白")
    elif len(claims) != 1:
        problems.append(f"端口上有 {len(claims)} 个监听者（{', '.join(claims) or '一个都没有'}），"
                        "压测流量不保证到达被测网关，读数不可归因")
    if published is None:
        problems.append("读不到本项目为 gateway:80 发布的端口（docker compose port 失败），无法确认这个端口"
                        "归本仓库所有，身份门不放行")
    elif port is not None and str(port) not in published:
        problems.append(f"被压的端口 :{port} 不在本仓库 compose 项目为 gateway:80 发布的端口里"
                        f"（发布的是 {', '.join(published)}），那可能是同一份镜像起的另一个网关在回答本站接口")
    if served is None:
        problems.append("这个端口没有回答出一份 OpenAPI 路径表，无法与本仓库的契约比对，身份门不放行")
    else:
        missing = contract - served
        if missing:
            sample = ", ".join(sorted(missing)[:3])
            problems.append(f"这个端口回答的接口表缺了本仓库契约里的 {len(missing)} 条端点"
                            f"（如 {sample}；比对 shared/contracts/openapi-v13.json 的 "
                            f"{len(contract)} 条），它不是本站")
    return problems


def fetch(url: str, timeout: float = 15.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def read_claims(port: int) -> list[str] | None:
    try:
        done = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                              capture_output=True, text=True)
    except OSError:
        # No lsof on this host. That is a refusal with a reason (the judge treats None as "no reading"),
        # not a traceback an operator would otherwise read as the measurement itself crashing.
        return None
    # lsof exits 1 when nothing matches: that is a reading (zero claimants), not a broken instrument.
    if done.returncode not in (0, 1):
        return None
    return listener_claims(done.stdout)


def self_test() -> int:
    contract = {"paths": {f"/api/{i}": {} for i in range(6)}}
    full = contract_paths(contract)
    arms = []
    arms.append(("a clean port answering this contract, on a port this project publishes, passes",
                 judge(["docker(1)"], full, full, "18080", ["18080"]) == []))
    arms.append(("two claimants on one port are named and refused",
                 any("2 个监听者" in p for p in judge(["docker(1)", "ssh(9)"], full, full,
                                                     "18080", ["18080"]))))
    arms.append(("no listener at all is refused too",
                 any("一个都没有" in p for p in judge([], full, full, "18080", ["18080"]))))
    arms.append(("a listener reading that could not be taken is refused, not passed",
                 any("看不见不等于清白" in p for p in judge(None, full, full))))
    arms.append(("a foreign service is caught by its own endpoint set",
                 any("缺了本仓库契约里的" in p
                     for p in judge(["docker(1)"], {"/health"}, full, "18080", ["18080"]))))
    arms.append(("extra endpoints on the serving side do not fire this gate",
                 judge(["docker(1)"], full | {"/gateway-only"}, full, "18080", ["18080"]) == []))
    arms.append(("a body that is not an OpenAPI document is refused as no reading",
                 any("没有回答出一份 OpenAPI 路径表" in p
                     for p in judge(["docker(1)"], served_paths("<html>nope</html>"), full))))
    arms.append(("the real contract is read from the tracked file, not typed in",
                 len(contract_paths()) == 90 and "/api/workspace/invitations" in contract_paths()))
    arms.append(("a port this project does not publish is refused even when it answers our contract",
                 any("不在本仓库 compose 项目" in p for p in
                     judge(["docker(1)"], full, full, port="8080", published=["18080"]))))
    arms.append(("the project's own published port satisfies the third axis, and no reading refuses",
                 judge(["docker(1)"], full, full, port="18080", published=["18080"]) == []
                 and bool(judge(["docker(1)"], full, full, port="18080", published=None))))
    bad = 0
    width = max(len(name) for name, _ in arms)
    for name, ok in arms:
        print(f"  {'OK  ' if ok else 'FAIL'} {name:<{width}}")
        bad += 0 if ok else 1
    print(f"self-test: {len(arms)} arms, failures: {'none' if not bad else bad}")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="the origin the client will actually hit, e.g. http://127.0.0.1:18080")
    ap.add_argument("--port", type=int, help="the published host port whose listeners must be unique")
    ap.add_argument("--compose-files", default="docker-compose.yml",
                    help="comma-separated -f list used to ask THIS project which port it published")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.base_url:
        raise SystemExit("--base-url is required (or run --self-test)")
    served = served_paths(fetch(args.base_url.rstrip("/") + "/openapi.json"))
    claims = read_claims(args.port) if args.port else None
    if not args.port:
        print("# --port was not given, so the listener axis has no reading; the gate refuses on that axis",
              file=sys.stderr)
    published = published_ports([f.strip() for f in args.compose_files.split(",") if f.strip()])
    problems = judge(claims, served, contract_paths(), str(args.port) if args.port else None, published)
    print(f"identity check: {args.base_url} port={args.port if args.port else '?'} "
          f"claimants={claims if claims is not None else 'NO-READING'} "
          f"endpoints={len(served) if served is not None else 'NO-DOCUMENT'} "
          f"published-by-this-project={','.join(published) if published else 'NO-READING'}")
    for problem in problems:
        print(f"::error title=gateway-identity::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
