"""clamd 的 INSTREAM 客户端：向一个扫描引擎要一份文件的判决，并带回答话的引擎自报名。

单独成模块有两个理由。其一，`worker.py` 在导入时就建 redis/boto3/provider 适配器，
一个只测 socket 协议的用例不该被迫起整条栈；其二，这条边界是 `media_assets.scan_status`
那格 'clean' 的唯一来源（见 db/migrations/020_media_scan_truth.sql），所以它得能被单独
证明会开火，而不是只在集成跑里顺带过一遍。

协议照 clamd 的形状来，不在这里重发明：命令以换行结束；`nINSTREAM` 之后按 4 字节大端
长度分块上传，0 长度块结束传输；应答一行，`stream ... OK` 或 `stream ... <签名名> FOUND`，
带 `n` 前缀时以 NUL 收尾。`VERSION` 给出引擎与签名库的自报名。
"""
from __future__ import annotations

import socket

CHUNK = 65536


class ScanUnavailable(RuntimeError):
    """引擎连不上或应答不完整：这是环境没到位，不是这份文件脏。"""


class ScanAmbiguous(RuntimeError):
    """引擎回的不是 OK 也不是 FOUND：宁可当作不可用，绝不默认放行。"""


def reply(sock) -> str:
    """读到一行应答为止；NUL 与换行都是 clamd 的结束符。"""
    buffer = b""
    while b"\n" not in buffer and b"\x00" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk
    if not buffer:
        raise ScanUnavailable("the scanner closed the connection without answering")
    return buffer.split(b"\x00", 1)[0].strip().decode("utf-8", "replace")


def command(host: str, port: int, payload: bytes, timeout: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
            return reply(sock)
    except OSError as exc:
        raise ScanUnavailable(f"{type(exc).__name__}: {exc}") from exc


def instream(host: str, port: int, path: str, timeout: float) -> str:
    """把文件按 INSTREAM 喂给引擎，拿回那一行判决。"""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b"nINSTREAM\n")
            with open(path, "rb") as handle:
                while chunk := handle.read(CHUNK):
                    sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall(b"\x00\x00\x00\x00")
            return reply(sock)
    except OSError as exc:
        raise ScanUnavailable(f"{type(exc).__name__}: {exc}") from exc


def scan(endpoint: str, path: str, timeout: float = 120.0) -> dict:
    """{"status": clean|infected, "engine": 自报名, "detail": 引擎原话}。

    endpoint 形如 host:port。空 endpoint 由调用方负责处理（这台部署没配引擎，
    资产记 unscanned），这里不接受，因为「没配」与「配了但没答」必须是两条路径。

    判决用的是白名单而不是黑名单。INSTREAM 的应答形状只有两种算结论：
    `stream(<id>) OK` 与 `stream(<id>) <签名名> FOUND`。其余一切——
    `INSTREAM size limit exceeded. ERROR`、`ERROR`、空应答、连接断——都不是「干净」，
    一律走异常。这里不能写成「不是 FOUND 就当干净」：那正是本模块要替掉的那个 bug 的形状
    （旧代码把 'clean' 当字面量写进库，见 db/migrations/020_media_scan_truth.sql 的头注释）。
    前缀 `stream` 也在判据里：少了它，任何以 OK 收尾的无关应答都会被读成放行。
    """
    host, _, raw_port = endpoint.rpartition(":")
    if not host or not raw_port.isdigit():
        raise ScanUnavailable(f"MEDIA_SCAN_ENDPOINT must be host:port, got {endpoint!r}")
    port = int(raw_port)
    answer = instream(host, port, path, timeout)
    engine = command(host, port, b"VERSION\n", timeout) or "clamd (unreported version)"
    if not answer.startswith("stream"):
        raise ScanAmbiguous(f"scanner answered {answer!r}, which is not an INSTREAM verdict at all")
    if answer.endswith(" OK"):
        return {"status": "clean", "engine": engine, "detail": answer}
    name, _, _ = answer.partition(" FOUND")
    if answer.endswith(" FOUND"):
        return {"status": "infected", "engine": engine, "detail": f"{name.strip()} FOUND"}
    raise ScanAmbiguous(f"scanner answered {answer!r}, which is neither a clean nor an infected verdict")
