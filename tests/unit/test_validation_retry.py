"""The audio-validation failure split: a probe that never ran is not a verdict on the bytes.

Registered after acceptance-20260928T051817Z reddened the acceptance suite on a job that settled
``partial`` because ``ffprobe`` hit its 15-second budget while the host sat at load average 11.5 (rising to
25) -- and the candidate was then recorded failed with ``attempt_count=1``, i.e. never retried. The
classifier lives in `services/worker/validation.py`; this file is what keeps both halves honest: the
classification itself, and the fact that the worker actually acts on it.
"""
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/worker"))
import validation  # noqa: E402  (its own module precisely so this file need not import redis/boto3/worker)

WORKER = ROOT / "services/worker/worker.py"


def timeout():
    return subprocess.TimeoutExpired(cmd=["ffprobe", "-v", "error"], timeout=15)


def cases():
    """Every shape the worker's `except Exception` can meet at the ffprobe call, with its verdict.

    `returncode=`/`output=` are required by CalledProcessError's constructor, so the two error shapes are
    built the way subprocess itself would build them rather than with a fake class.
    """
    return [
        (timeout(), True, "probe gave up waiting"),
        (FileNotFoundError(2, "No such file or directory", "ffprobe"), True, "no ffprobe in the image"),
        (subprocess.CalledProcessError(1, "ffprobe"), False, "ffprobe ran and refused this file"),
        (OSError(13, "Permission denied", "ffprobe"), False, "not the two named shapes"),
        (ValueError("non-positive audio duration"), False, "the bytes said something"),
        (TypeError("not a number"), False, "unparseable probe output"),
    ]


def test_the_machine_is_separated_from_the_bytes():
    for exc, expected, why in cases():
        assert validation.validation_is_infrastructure(exc) is expected, f"{type(exc).__name__} ({why})"


def test_called_process_error_is_not_swept_in_by_being_a_subprocess_error():
    """The widening that looks tidy and is wrong: TimeoutExpired and CalledProcessError are siblings, so any
    clause written for the family puts "the probe ran and refused these bytes" into the retry bucket --
    retrying unreadable audio forever, which is worse than the failure being fixed. Hierarchy asserted from
    the classes, not from memory."""
    assert issubclass(subprocess.TimeoutExpired, subprocess.SubprocessError)
    assert issubclass(subprocess.CalledProcessError, subprocess.SubprocessError)
    assert not issubclass(subprocess.CalledProcessError, OSError)
    assert issubclass(FileNotFoundError, OSError)
    assert validation.validation_is_infrastructure(subprocess.CalledProcessError(1, "ffprobe")) is False


def test_the_worker_actually_uses_the_verdict():
    """Wiring, not vocabulary: the branch must exist, ask the predicate, and raise the type the candidate
    loop re-raises (`Retryable`) rather than the one it records as a failed candidate (`RuntimeError`)."""
    src = WORKER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)
           and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "validation_is_infrastructure"
                   for c in ast.walk(n.test))]
    assert ifs, "worker.py no longer branches on validation_is_infrastructure -- the split is dead code"
    body = ast.dump(ifs[0])
    assert "Retryable" in body, "the infrastructure branch does not raise Retryable"
    assert "validation could not run" in ast.unparse(ifs[0]), \
        "the retry says nothing about what could not run, so the ledger cannot be read later"
    handler = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is not None
               and ast.unparse(n).startswith("except Exception")]
    assert handler, "the ffprobe call is no longer wrapped by a broad handler; this file's premise moved"


def test_the_import_is_reachable_from_the_worker():
    src = WORKER.read_text(encoding="utf-8")
    assert "from validation import validation_is_infrastructure" in src, \
        "worker.py cannot be importing the classifier, so the branch above can only be a name"


# ---------------------------------------------------------------------- controls ----

def mutated(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"anchor occurs {source.count(old)} times: {old[:70]!r}"
    out = source.replace(old, new)
    assert out != source, "the mutation changed nothing"
    return out


def test_the_detectors_fire_on_the_shapes_that_would_return_silently():
    src = WORKER.read_text(encoding="utf-8")

    # 1. the branch is dropped: everything is a candidate failure again.
    without = mutated(src, "            if validation_is_infrastructure(exc):\n"
                           '                raise Retryable(f"audio validation could not run: {exc}") from exc\n', "")
    tree = ast.parse(without)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.If)
                and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "validation_is_infrastructure"
                        for c in ast.walk(n.test))], "the wiring detector cannot see the branch being removed"

    # 2. the clause widened to OSError -- green today, and it turns every unreadable file into a retry loop.
    wide = (ROOT / "services/worker/validation.py").read_text(encoding="utf-8")
    widened = mutated(wide, "    if isinstance(exc, subprocess.CalledProcessError):\n        return False\n", "")
    assert widened != wide
    namespace: dict = {}
    exec(compile(widened, "widened_validation", "exec"), namespace)  # noqa: S102 -- the mutated copy, in memory
    assert namespace["validation_is_infrastructure"](subprocess.CalledProcessError(1, "ffprobe")) is False, \
        "this control does not discriminate, so it proves nothing about the veto it deletes"
    # the widening that *does* misfire: fold the veto into the family it came from
    widened2 = mutated(wide, "    if isinstance(exc, subprocess.TimeoutExpired):\n        return True\n"
                             "    if isinstance(exc, subprocess.CalledProcessError):\n        return False\n",
                       "    if isinstance(exc, subprocess.SubprocessError):\n        return True\n")
    namespace2: dict = {}
    exec(compile(widened2, "widened_validation2", "exec"), namespace2)
    assert namespace2["validation_is_infrastructure"](subprocess.CalledProcessError(1, "ffprobe")) is True, \
        "with the veto folded into SubprocessError, nothing in this file would have noticed"
    assert validation.validation_is_infrastructure(subprocess.CalledProcessError(1, "ffprobe")) is False, \
        "the shipped predicate already treats CalledProcessError as infrastructure"

    # 3. the retry raised the wrong type: a candidate failure wearing a retry's clothes.
    mislabeled = mutated(src, 'raise Retryable(f"audio validation could not run: {exc}") from exc',
                         'raise RuntimeError(f"audio validation could not run: {exc}") from exc')
    probe = [n for n in ast.walk(ast.parse(mislabeled)) if isinstance(n, ast.If)
             and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "validation_is_infrastructure"
                     for c in ast.walk(n.test))]
    assert probe and "Retryable" not in ast.dump(probe[0]), \
        "the wiring detector cannot see Retryable being swapped for RuntimeError"
