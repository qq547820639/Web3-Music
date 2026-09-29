"""The capacity leg's metric line must carry measurements, not the thresholds that sit beside them.

This exists because the inline version of the parser read `error_rate_pct=1.0` off the verdict line
(`gate=FAIL max_error_rate=1.0%`) instead of the `error_rate=0.000%` printed two lines earlier, and shipped that
into a step log. The fixture is a real producer transcript -- `capacity-results/20260929T063130Z.log`, the arm
in-chain -- so the shapes checked here are the ones the load tool actually emits rather than ones invented to pass.
"""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import capacity_metrics  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRANSCRIPT = ROOT / "capacity-results/20260929T063130Z.log"


def test_the_tracked_transcript_renders_its_own_measurements():
    line = capacity_metrics.render(TRANSCRIPT.read_text(encoding="utf-8"), acceptance_rc="0")
    assert line.startswith("metric "), line
    fields = dict(pair.split("=", 1) for pair in line.split()[1:])
    assert fields["p95_ms"] == "10958.8" and fields["p50_ms"] == "5197.4", fields
    assert fields["users"] == "500" and fields["requests_per_user"] == "2", fields
    assert fields["throughput_rps"] == "59.7", fields
    # The point of the module: 1.0 is the threshold on the next line, 0.000 is what was measured.
    assert fields["error_rate_pct"] == "0.000", fields
    assert fields["acceptance_rc"] == "0", fields


def test_a_verdict_line_alone_is_refused_rather_than_fabricated():
    verdict_only = "statuses={200: 1000}\ngate=FAIL max_error_rate=1.0% max_p95_ms=800.0\n"
    line = capacity_metrics.render(verdict_only, acceptance_rc="2")
    assert line.startswith("metric unavailable:"), line
    assert "max_error_rate" not in line and "1.0" not in line, line
    refused, _, _ = (line.split("unavailable:", 1)[1]).partition("reading")
    assert refused.strip(), "the refusal has to name what is missing"


def test_the_error_rate_pattern_ignores_the_prefixed_threshold():
    both = ("statuses={200: 998} error_rate=0.200%\n"
            "gate=FAIL max_error_rate=1.0% max_p95_ms=800.0\n"
            "users=500 requests_per_user=2 total=1000\n"
            "elapsed=15.0s throughput=60.0 req/s\n"
            "latency_ms p50=1 p95=2 p99=3 mean=1.5\n")
    assert capacity_metrics.readings(both)["error_rate_pct"] == "0.200"
    only_threshold = both.replace("statuses={200: 998} error_rate=0.200%", "statuses={200: 998}")
    assert capacity_metrics.readings(only_threshold)["error_rate_pct"] == "unset", only_threshold


def test_a_missing_field_never_becomes_a_zero_and_the_script_uses_this_module():
    script = (ROOT / "scripts/capacity-gate-500.sh").read_text(encoding="utf-8")
    assert "capacity_metrics.py" in script, "the leg must render through this module, not an inline parser"
    partial = TRANSCRIPT.read_text(encoding="utf-8").replace("error_rate=0.000%", "")
    line = capacity_metrics.render(partial, acceptance_rc="0")
    assert line == "metric unavailable: the load tool printed no error_rate_pct reading", line


def test_the_module_runs_as_a_command_and_stays_silent_about_exit_codes(tmp_path):
    log = tmp_path / "arm.log"
    log.write_text(TRANSCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    ran = subprocess.run([sys.executable, str(ROOT / "scripts/capacity_metrics.py"), str(log), "0"],
                         capture_output=True, text=True)
    assert ran.returncode == 0 and ran.stdout.startswith("metric users=500"), ran.stdout
    gone = subprocess.run([sys.executable, str(ROOT / "scripts/capacity_metrics.py"), str(tmp_path / "no.log"), "1"],
                          capture_output=True, text=True)
    assert gone.returncode == 0 and "no log at" in gone.stdout, (gone.returncode, gone.stdout)
