#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一行机器可读的读数：由算出这个数的那一步自己写下。

为什么需要它：《测试报告》与状态文档里还有两类数是手抄的——两条延迟序列
（`provider-regression-100` 与 `generic-rest-roundtrip` 的 p50/p95）和各演练的 `N/N`。
抄不准的原因是结构性的：读数只活在逐步日志里，而 `.gitignore` 把这些日志挡在树外，
盖章器够不着它们，能引用的只剩"上一次有人把终端里的数敲进文书"那一份。

这一行就是那件够得着的东西：程序在算出数的那一刻打一行 `metric k=v ...`，
`run_step` 把它连同步骤名一起抄进在册的 `SUMMARY.txt`（`metrics <步骤名> k=v ...`），
盖章器从此有出处。两种前缀是故意分开的——`metric` 只在日志里，`metrics` 只在 SUMMARY 里，
所以解析不会把第一步的日志当成第二步的表。

缺席不是零：没有这一行的步骤，读数是"不存在"，不是 0。这一条不在这里实现，
而是由盖章侧的哨兵守着（`stamp_release_faces` 只有显式读到键才交值，读不到就整轮拒写），
`self_test` 里另有一支专门证明"没有行"与"值为空的行"不是一回事。
"""
from __future__ import annotations

import json
import re

PRODUCER_PREFIX = "metric "
SUMMARY_PREFIX = "metrics "

PAIR = re.compile(r'([a-z][a-z0-9_]*)=("[^"]*"|[^\s"]+)')
BAD_STEP = re.compile(r"[\s=\"']")


def _render(key: str, value) -> str:
    """One `k=v` pair, quoted only when the value could otherwise be read as two fields."""
    if isinstance(value, bool):
        text = "1" if value else "0"
    else:
        text = str(value)
    if text == "" or re.search(r"\s", text) or '"' in text or "=" in text:
        return f"{key}={json.dumps(text, ensure_ascii=False)}"
    return f"{key}={text}"


def format(step: str | None, **kv) -> str:
    """The line itself: `metric k=v` for a producer, `metrics <step> k=v` for the SUMMARY."""
    if not kv:
        raise ValueError("an empty metrics line reads as 'this step measured nothing', which is a claim "
                         "of its own; emit the keys you have, or emit nothing and let the step be absent")
    if step is None:
        return PRODUCER_PREFIX + " ".join(_render(k, kv[k]) for k in sorted(kv))
    if BAD_STEP.search(step):
        raise ValueError(f"a step name written into SUMMARY may not contain spaces, quotes or '=': {step!r}")
    return SUMMARY_PREFIX + step + " " + " ".join(_render(k, kv[k]) for k in sorted(kv))


def emit(**kv) -> str:
    """Print the producer's line (the chain prefixes it with the step name)."""
    line = format(None, **kv)
    print(line, flush=True)
    return line


def parse(line: str) -> tuple[str | None, dict]:
    """Split a SUMMARY `metrics` line into (step, readings); (None, {}) for anything else."""
    stripped = (line or "").strip()
    if not stripped.startswith(SUMMARY_PREFIX):
        return None, {}
    rest = stripped[len(SUMMARY_PREFIX):]
    step, _, body = rest.partition(" ")
    readings = {}
    for key, raw in PAIR.findall(body):
        readings[key] = json.loads(raw) if raw.startswith('"') else raw
    return step, readings


def parse_producer(line: str) -> dict:
    """Split a producer's `metric` line from a step log; {} when the line is not one."""
    stripped = (line or "").strip()
    if not stripped.startswith(PRODUCER_PREFIX) or stripped.startswith(SUMMARY_PREFIX):
        return {}
    return {key: (json.loads(raw) if raw.startswith('"') else raw)
            for key, raw in PAIR.findall(stripped[len(PRODUCER_PREFIX):])}


def self_test() -> int:
    arms = []
    line = format("lease-contention", jobs=8, workers=2, credits="160.0")
    arms.append((f"the rendered line round-trips ({line})",
                 parse(line) == ("lease-contention", {"jobs": "8", "workers": "2", "credits": "160.0"})))
    quoted = format("s", note="2 workspaces, 2 assets byte-identical")
    arms.append(("a value with a comma or space survives quoting",
                 parse(quoted)[1]["note"] == "2 workspaces, 2 assets byte-identical"))
    arms.append(("keys are sorted, so the same readings render byte-identically",
                 format("s", b=2, a=1) == format("s", a=1, b=2)))
    arms.append(("bool renders as 1/0 rather than Python's True/False",
                 parse(format("s", fresh=True))[1]["fresh"] == "1"))
    arms.append(("an empty reading set is refused, not written as a bare prefix",
                 _raises(lambda: format("s")) is ValueError))
    arms.append(("a step name that would break the table is refused",
                 _raises(lambda: format("two words", a=1)) is ValueError))
    producer = f"{PRODUCER_PREFIX}a=1"
    arms.append(("a producer line is not read as a summary line",
                 parse(producer) == (None, {}) and parse_producer(producer) == {"a": "1"}))
    arms.append(("absent is not zero: no line parses to nothing rather than to 0",
                 parse("provider regression: 100/100 completed") == (None, {})))
    width = max(len(name) for name, _ in arms)
    bad = 0
    for name, ok in arms:
        print(f"  {'OK  ' if ok else 'FAIL'} {name:<{width}}")
        bad += 0 if ok else 1
    print(f"metrics self-test: {len(arms)} arms, failures: {'none' if not bad else bad}")
    return 0 if not bad else 1


def _raises(call):
    try:
        call()
    except Exception as error:  # noqa: BLE001 -- the arm's whole point is the exception's type
        return type(error)
    return None


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        sys.exit(self_test())
    print(__doc__)
    sys.exit(0)
