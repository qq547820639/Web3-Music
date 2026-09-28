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


def test_the_clean_pair_passes(gate):
    full = gate.contract_paths(CONTRACT)
    assert gate.judge(["dockerd(1)"], full, full) == [], (
        "唯一的监听者加上与本站一致的接口表被拒了，这道门会在每次正常压测上误红")


def test_a_second_claimant_on_the_port_is_named(gate):
    full = gate.contract_paths(CONTRACT)
    problems = gate.judge(["dockerd(1)", "ssh(9)"], full, full)
    assert any("2 个监听者" in p and "ssh(9)" in p for p in problems), (
        f"两个认领者被读成了清白：{problems}")


def test_an_unreadable_listener_axis_is_refused_not_passed(gate):
    full = gate.contract_paths(CONTRACT)
    assert any("看不见不等于清白" in p for p in gate.judge(None, full, full)), (
        "读不到监听者清单被当成通过——那正是把「看不见」折成「清白」的形状")


def test_a_foreign_endpoint_set_is_caught_by_the_contract(gate):
    full = gate.contract_paths(CONTRACT)
    problems = gate.judge(["dockerd(1)"], {"/api/0", "/health"}, full)
    assert any("缺了本仓库契约里的 6 条端点" in p for p in problems), (
        f"端口上站着别的服务而判据说不出：{problems}")


def test_a_non_openapi_body_is_no_reading(gate):
    assert gate.served_paths("<html>404</html>") is None
    assert gate.served_paths(json.dumps({"info": {"title": "x"}})) is None


def test_extra_endpoints_on_the_serving_side_do_not_fire_this_gate(gate):
    """This gate answers "is this our station", not "has the contract drifted" -- that is the contract test's job."""
    full = gate.contract_paths(CONTRACT)
    assert gate.judge(["dockerd(1)"], full | {"/gateway-health"}, full) == []


def test_claimants_are_deduplicated_per_process_not_per_socket(gate):
    listing = ("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
               "docker  1   u  1u IPv4  0xa   0t0  TCP 127.0.0.1:18080 (LISTEN)\n"
               "docker  1   u  2u IPv6  0xb   0t0  TCP [::1]:18080 (LISTEN)\n")
    assert gate.listener_claims(listing) == ["docker(1)"], gate.listener_claims(listing)


def test_the_contract_denominator_is_the_tracked_file(gate):
    """90 is what the release record quotes for this contract; the gate must read the same source."""
    paths = gate.contract_paths()
    tracked = json.loads((ROOT / "shared/contracts/openapi-v13.json").read_text(encoding="utf-8"))
    assert paths == set(tracked["paths"]), "身份门的契约分母不是仓库里那份契约文件"
    assert len(paths) == len(tracked["paths"])
