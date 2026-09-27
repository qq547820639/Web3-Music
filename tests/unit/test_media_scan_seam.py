"""The scan seam is the only thing allowed to call a media asset clean, and it must be provable.

`db/migrations/020_media_scan_truth.sql` exists because `store_media` used to write the literal
'clean' into `media_assets.scan_status` -- the same column the media endpoint read as an assurance --
with no scanner anywhere in the repository. The database now refuses a 'clean' row that cannot name
its engine (see the smoke polarities in that file's header); this module guards the two halves that
stay in Python: the protocol client's verdicts, and the rule that no code path may hand that column
a constant again.

Every reader below ships with the sample that must make it fire.
"""
import ast
import pathlib
import re
import socket
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKER = ROOT / "services/worker/worker.py"
MEDIA_INSERT = re.compile(r"\bINSERT\s+INTO\s+media_assets\b", re.IGNORECASE)
VERDICT_LITERAL = re.compile(r"'(clean|unscanned|pending|rejected)'")
import sys
sys.path.insert(0, str(ROOT / "services/worker"))
import media_scan  # noqa: E402  (the seam under test, importable on its own precisely so this file
                  # does not have to start redis/boto3/the provider adapter)

CLEAN_ANSWER = b"stream(1) OK\x00"
INFECTED_ANSWER = b"stream(1) Eicar-Signature FOUND\x00"


class ClamdStub(threading.Thread):
    """A daemon that speaks enough of clamd's INSTREAM to catch a framing bug on either side.

    It does not just answer: it reassembles the length-prefixed chunks and refuses to reply until it
    has seen the terminator, so a client that forgets the 4-byte prefixes, the sizes, or the final
    zero-length chunk hangs into the timeout instead of quietly passing.
    """

    def __init__(self, answer: bytes = CLEAN_ANSWER, version: bytes = b"ClamAV 1.4.2/27830/OK\x00",
                 expect_bytes: bytes = b""):
        super().__init__(daemon=True)
        self.answer, self.version, self.expect_bytes = answer, version, expect_bytes
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.received = bytearray()
        self.commands: list[str] = []

    def run(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                self._serve(conn)

    def _serve(self, conn):
        # A stream, not a datagram: the client's `nINSTREAM\n` and its first length-prefixed chunk can
        # arrive in one segment, so the command is read line-wise from the same buffer the body comes
        # out of. Reading a fixed 64 bytes as "the command" would swallow the first chunk and the
        # framing check below would pass for the wrong reason.
        with conn.makefile("rb") as stream:
            header = stream.readline()
            self.commands.append(header.decode(errors="replace"))
            if header.startswith(b"nINSTREAM"):
                while True:
                    size = stream.read(4)
                    if size is None or len(size) < 4:
                        return
                    length = int.from_bytes(size, "big")
                    if length == 0:
                        break
                    self.received.extend(stream.read(length))
                if bytes(self.received) != self.expect_bytes:
                    conn.sendall(b"stream INSTREAM framing mismatch\x00")
                    return
                conn.sendall(self.answer)
            else:
                conn.sendall(self.version)

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.port}"

    def close(self):
        self.server.close()


@pytest.fixture
def payload(tmp_path):
    data = b"RIFF....WAVEfmt " + bytes(range(64))
    path = tmp_path / "candidate.wav"
    path.write_bytes(data)
    return path, data


def test_the_seam_returns_clean_when_the_engine_says_ok(payload):
    path, data = payload
    stub = ClamdStub(answer=CLEAN_ANSWER, expect_bytes=data)
    stub.start()
    try:
        verdict = media_scan.scan(stub.endpoint, str(path), timeout=10)
    finally:
        stub.close()
    assert verdict["status"] == "clean"
    assert verdict["engine"] == "ClamAV 1.4.2/27830/OK"
    assert bytes(stub.received) == data, "the daemon never saw the whole file, so its verdict means nothing"


def test_the_seam_returns_infected_when_the_engine_names_a_signature(payload):
    path, data = payload
    stub = ClamdStub(answer=INFECTED_ANSWER, expect_bytes=data)
    stub.start()
    try:
        verdict = media_scan.scan(stub.endpoint, str(path), timeout=10)
    finally:
        stub.close()
    assert verdict["status"] == "infected"
    assert "Eicar-Signature" in verdict["detail"]


@pytest.mark.parametrize("answer", [
    b"UNKNOWN\x00",                                  # 引擎没加载完 / 没听懂
    b"INSTREAM size limit exceeded. ERROR\x00",      # clamd 对超限的原话，不是判决
    b"ERROR\x00",
    b"stream INSTREAM framing mismatch\x00",        # 我们自己这条链路的形状错了
    b"an unrelated answer that happens to end in OK",  # 前缀规则存在的全部理由
])
def test_an_answer_that_is_not_one_of_the_two_verdict_shapes_is_not_clean(payload, answer):
    """The default must be refusal: an engine we cannot read has not cleared anything.

    The old code's failure was exactly this -- anything that was not a rejection counted as clean, and
    it was written into the column the download gate reads. So every shape outside
    `stream(...) OK` / `stream(...) <name> FOUND` has to land on the exception path.
    """
    path, data = payload
    stub = ClamdStub(answer=answer, expect_bytes=data)
    stub.start()
    try:
        with pytest.raises(media_scan.ScanAmbiguous):
            media_scan.scan(stub.endpoint, str(path), timeout=10)
    finally:
        stub.close()


def test_an_unreachable_engine_raises_scan_unavailable_not_a_verdict(tmp_path):
    """No listener at all: the caller must retry the job, never label the candidate."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    path = tmp_path / "x.wav"
    path.write_bytes(b"RIFF")
    with pytest.raises(media_scan.ScanUnavailable):
        media_scan.scan(f"127.0.0.1:{port}", str(path), timeout=2)


@pytest.mark.parametrize("endpoint", ["", "nonsense", "clamav:", ":3310"])
def test_a_malformed_endpoint_is_refused_before_any_connection(endpoint):
    with pytest.raises(media_scan.ScanUnavailable):
        media_scan.scan(endpoint, "/dev/null", timeout=1)


# ------------------------------------------------ the literal-write guard and its control ----

def literal_scan_statuses(source: str) -> list[str]:
    """Verdict words written into the media_assets INSERT as SQL literals.

    The rule is about the value that lands in the column, not every mention of the word: `store_media`
    legitimately *compares* a verdict while deciding whether a cached row can be reused. Reads the
    call arguments of `execute`, because the statement is one string and the values are a tuple, so a
    line scan would miss either half.
    """
    tree = ast.parse(source)
    writes, found = 0, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute" and node.args):
            continue
        statement = node.args[0]
        if not (isinstance(statement, ast.Constant) and isinstance(statement.value, str)):
            continue
        if not MEDIA_INSERT.search(statement.value):
            continue
        writes += 1
        found += VERDICT_LITERAL.findall(statement.value)
    if not writes:
        raise AssertionError("no INSERT INTO media_assets in the worker: this guard now guards nothing")
    return found


def test_store_media_never_writes_a_scan_verdict_as_a_constant():
    assert literal_scan_statuses(WORKER.read_text(encoding="utf-8")) == [], \
        "the verdict has to come from scan_media(); a literal here is how the 020 defect was born"


def test_the_literal_guard_fires_on_the_shape_it_replaces():
    sample = '''
async def store_media(job, ordinal, tmp_path, sha, mime, size, duration_ms, scan=None):
    cur.execute("INSERT INTO media_assets(workspace_id,scan_status) VALUES(%s,'clean')", (1,))
    return {"scan_status": "clean"}
'''
    assert literal_scan_statuses(sample) == ["clean"], "the reader cannot see a literal it is meant to ban"


def test_the_literal_guard_says_so_when_the_write_moves():
    with pytest.raises(AssertionError, match="guards nothing"):
        literal_scan_statuses("def other():\n    cur.execute('SELECT 1')\n")
