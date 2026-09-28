#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""容量腿身份门的常驻用例：判据必须在两条极性上都会开火，且在"读不到"时不放行。

`scripts/gateway_identity.py` 是 2026-09-28 那两次作废读数换来的东西（8080 上站着别人的容器、网关发布端口
被一个 ssh 监听共占，两支 500×2 里的 205 个 502 在被测栈自己的日志里不存在）。这里钉的是判决函数本身，
不碰网络：把"端口只有一个认领者"与"接口表就是本仓库那份契约"两轴的正反两侧都固定下来。
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("gateway_identity_under_test",
                                                 ROOT / "scripts/gateway_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = {"paths": {f"/api/{i}": {} for i in range(7)}}


CLEAN = (["dockerd(1)"], "18080", ["18080"])


def test_the_clean_pair_passes(gate):
    """The three axes together: one claimant, our contract, and this project's own published port."""
    full = gate.contract_paths(CONTRACT)
    claims, port, published = CLEAN
    assert gate.judge(claims, full, full, port, published) == [], (
        "唯一的监听者、与本站一致的接口表、再加上本项目发布的端口，仍被拒了，这道门会在每次正常压测上误红")


def test_a_second_claimant_on_the_port_is_named(gate):
    full = gate.contract_paths(CONTRACT)
    problems = gate.judge(["dockerd(1)", "ssh(9)"], full, full, "18080", ["18080"])
    assert any("2 个监听者" in p and "ssh(9)" in p for p in problems), (
        f"两个认领者被读成了清白：{problems}")


def test_an_unreadable_listener_axis_is_refused_not_passed(gate):
    full = gate.contract_paths(CONTRACT)
    assert any("看不见不等于清白" in p for p in gate.judge(None, full, full, "18080", ["18080"])), (
        "读不到监听者清单被当成通过——那正是把「看不见」折成「清白」的形状")


def test_a_foreign_endpoint_set_is_caught_by_the_contract(gate):
    full = gate.contract_paths(CONTRACT)
    problems = gate.judge(["dockerd(1)"], {"/api/0", "/health"}, full, "18080", ["18080"])
    assert any("缺了本仓库契约里的 6 条端点" in p for p in problems), (
        f"端口上站着别的服务而判据说不出：{problems}")


def test_a_non_openapi_body_is_no_reading(gate):
    assert gate.served_paths("<html>404</html>") is None
    assert gate.served_paths(json.dumps({"info": {"title": "x"}})) is None


def test_extra_endpoints_on_the_serving_side_do_not_fire_this_gate(gate):
    """This gate answers "is this our station", not "has the contract drifted" -- that is the contract test's job."""
    full = gate.contract_paths(CONTRACT)
    assert gate.judge(["dockerd(1)"], full | {"/gateway-health"}, full, "18080", ["18080"]) == []


def test_claimants_are_deduplicated_per_process_not_per_socket(gate):
    listing = ("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
               "docker  1   u  1u IPv4  0xa   0t0  TCP 127.0.0.1:18080 (LISTEN)\n"
               "docker  1   u  2u IPv6  0xb   0t0  TCP [::1]:18080 (LISTEN)\n")
    assert gate.listener_claims(listing) == ["docker(1)"], gate.listener_claims(listing)


def test_a_port_the_project_does_not_publish_is_refused(gate):
    """Answering our contract is not yet being our station: a clone of the same image answers identically.

    The third axis is what the two-claimant and contract readings cannot see -- it asks the repository's own
    compose invocation which host port it published for gateway:80, and refuses anything else. A failed
    reading of that answer is a refusal too, never a pass.
    """
    full = gate.contract_paths(CONTRACT)
    foreign = gate.judge(["dockerd(1)"], full, full, "8080", ["18080"])
    assert any("不在本仓库 compose 项目" in p for p in foreign), (
        f"8080 不是本项目发布的端口却被放行：{foreign}")
    unreadable = gate.judge(["dockerd(1)"], full, full, "18080", None)
    assert any("归本仓库所有" in p for p in unreadable), (
        f"读不到本项目发布的端口被当成了清白：{unreadable}")
    assert gate.judge(["dockerd(1)"], full, full, "18080", ["18080"]) == []


def test_the_contract_denominator_is_the_tracked_file(gate):
    """90 is what the release record quotes for this contract; the gate must read the same source."""
    paths = gate.contract_paths()
    tracked = json.loads((ROOT / "shared/contracts/openapi-v13.json").read_text(encoding="utf-8"))
    assert paths == set(tracked["paths"]), "身份门的契约分母不是仓库里那份契约文件"
    assert len(paths) == len(tracked["paths"])

def test_a_missing_lsof_is_a_clean_refusal_not_a_traceback(gate, monkeypatch):
    """The host may not have lsof at all; the gate has to say so with a reason, not crash.

    A traceback in a certification log reads as "the instrument broke mid-measurement", which is a
    different claim from "this axis has no reading, so nothing is released" -- the second one is what the
    judge already implements, and this pins that the reader reaches it.
    """
    def raiser(*args, **kwargs):
        raise OSError("lsof: command not found")

    monkeypatch.setattr(gate.subprocess, "run", raiser)
    assert gate.read_claims(18080) is None
    assert any("读不到" in p2 or "本项目" in p2 for p2 in
               gate.judge(None, gate.contract_paths(CONTRACT), gate.contract_paths(CONTRACT), "18080", ["18080"])), (
        "the refusal sentence does not name the missing reading, so a red would be unattributable")
