"""LeanRunner process handling with a fake REPL (tests/fake_repl.py): timeout, restart,
memory limit, crashes and the file fallback. No Lean needed."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from formal_mas.lean.runner import LeanResult, LeanRunner

FAKE = str(Path(__file__).with_name("fake_repl.py"))
CODE = "import Mathlib\n\ntheorem t : True := trivial\n"


def make_runner(*flags: str, **cfg) -> LeanRunner:
    base = {"timeout_s": 3, "startup_timeout_s": 10, "memory_poll_s": 0.1, "restart_memory_mb": 1e9,
            "kill_memory_mb": 1e9, "base_imports": ["Mathlib", "Aesop"], "cache_size": 0, **cfg}
    runner = LeanRunner({"lean": base}, repl_cmd=[sys.executable, "-u", FAKE, *flags])
    runner._verify_file = lambda *args: LeanResult("finished", "file", detail="fallback")
    return runner


@pytest.fixture
def runner():
    r = make_runner()
    yield r
    r.close()


def test_verify_uses_env_zero_and_reads_axioms(runner):
    res = runner.verify(CODE, theorem="t")
    assert (res.status, res.backend, res.axioms) == ("finished", "repl", ["propext"])
    assert res.messages == []  # the #print axioms answer is taken out of the messages
    assert runner.starts == 1
    runner.verify(CODE, theorem="t")
    assert runner.starts == 1  # Mathlib is not loaded again


def test_timeout_kills_and_restarts(runner):
    res = runner.verify(CODE + "-- HANG\n", timeout=1)
    assert res.status == "timeout" and res.backend == "repl"
    assert any(w["reason"] == "repl_timeout" for w in runner.warnings)
    res = runner.verify(CODE, theorem="t")  # a new REPL answers
    assert res.status == "finished" and runner.starts == 2


def test_dead_repl_is_a_crash_then_restarts(runner):
    assert runner.verify(CODE + "-- DIE\n").status == "crash"
    assert runner.verify(CODE, theorem="t").axioms == ["propext"]
    assert runner.starts == 2


def test_protocol_error_is_a_crash(runner):
    res = runner.verify(CODE + "-- PROTOCOL\n")
    assert res.status == "crash" and "Unknown environment" in res.detail


def test_memory_limit_kills_the_command():
    r = make_runner(kill_memory_mb=250, timeout_s=20)
    try:
        res = r.verify(CODE + "-- BIGMEM\n")
        assert res.status == "memory_limit"
        assert r.verify(CODE, theorem="t").status == "finished" and r.starts == 2
    finally:
        r.close()


def test_preventive_restart_every_n_commands():
    r = make_runner(restart_every=3)
    try:
        for _ in range(7):
            assert r.verify(CODE).status == "finished"
        assert r.starts == 3  # verifications 1-3, 4-6, 7
    finally:
        r.close()


def test_preventive_restart_on_memory():
    r = make_runner(restart_memory_mb=1)  # any process is above 1 MB
    try:
        r.verify(CODE)
        r.verify(CODE)
        assert r.starts == 2
    finally:
        r.close()


def test_fallback_when_repl_does_not_start():
    r = make_runner("--fail-start")
    res = r.verify(CODE)
    assert res.backend == "file" and res.detail == "fallback"
    assert r.warnings[0]["reason"] == "repl_start_failed" and r.warnings[0]["fallback"] == "file"
    r.verify(CODE)
    assert len(r.warnings) == 1  # the REPL is not retried on every call


def test_fallback_on_startup_timeout():
    r = make_runner("--slow-start", "30", startup_timeout_s=1)
    assert r.verify(CODE).backend == "file"
    assert "ReplTimeout" in r.warnings[0]["error"]


def test_out_of_sequence_reply_is_a_crash(runner):
    runner.verify(CODE)
    res = runner.verify(CODE + "-- DESYNC\n")
    assert res.status == "crash" and runner.warnings[-1]["reason"] == "repl_desync"
    assert runner.verify(CODE, theorem="t").status == "finished" and runner.starts == 2


def test_fallback_when_imports_fail():
    r = make_runner(base_imports=["Broken"])
    assert r.verify("import Broken\n\ntheorem t : True := trivial").backend == "file"
    assert r.warnings[0]["reason"] == "repl_import_failed"


def test_fallback_when_binary_missing(tmp_path):
    r = LeanRunner({"lean": {"repl_dir": "nowhere"}}, root=tmp_path)
    r._verify_file = lambda *args: LeanResult("finished", "file")
    assert r.verify(CODE).backend == "file"
    assert r.warnings[0]["reason"] == "repl_not_built"


@pytest.mark.parametrize("header", ["import Mathlib\nimport SomethingElse\n", "import Aesop\n", ""])
def test_imports_that_do_not_match_env0_go_to_file_backend(runner, header):
    # env 0 holds Mathlib: code without it (or with more) would see other declarations
    res = runner.verify(header + "\ntheorem t : True := trivial\n")
    assert res.backend == "file"
    assert runner.warnings[0]["reason"] == "imports_not_in_base"
    assert runner.starts == 0  # the REPL was not even needed


def test_mathlib_with_aesop_uses_repl(runner):
    assert runner.verify("import Mathlib\nimport Aesop\n\ntheorem t : True := trivial\n").backend == "repl"


def test_file_backend_config_skips_repl():
    r = make_runner(backend="file")
    assert r.verify(CODE).backend == "file" and r.starts == 0


# -- cache ----------------------------------------------------------------------


def test_identical_verifications_hit_the_cache():
    r = make_runner(cache_size=8)
    try:
        first = r.verify(CODE, theorem="t")
        second = r.verify(CODE, theorem="t")
        assert not first.cached and second.cached and r.cache_hits == 1
        assert second.axioms == first.axioms and second.declarations == ["t"]
        assert r.verify(CODE, theorem="t", timeout=5).cached is False  # the timeout is part of the key
    finally:
        r.close()


def test_unfinished_results_are_not_cached():
    r = make_runner(cache_size=8)
    try:
        assert r.verify(CODE + "-- HANG\n", timeout=1).status == "timeout"
        assert r.verify(CODE + "-- HANG\n", timeout=1).cached is False
    finally:
        r.close()


# -- plan B (file back-end) ----------------------------------------------------------


def file_runner(**cfg) -> tuple[LeanRunner, list[tuple[str, float]]]:
    """Runner on the file back-end with `lake env lean` replaced by a recorder."""
    calls: list[tuple[str, float]] = []

    def fake_run(code: str, timeout: float):
        calls.append((code, timeout))
        if code.strip().endswith("import Mathlib\nimport Aesop") or "theorem" not in code:
            time.sleep(0.2)  # the measured import
        return "finished", 0, "", ""

    r = LeanRunner({"lean": {"backend": "file", "timeout_s": 3, "startup_timeout_s": 999,
                             "base_imports": ["Mathlib", "Aesop"], "cache_size": 0, **cfg}})
    r._run_file = fake_run
    return r, calls


def test_file_timeout_is_timeout_plus_measured_import():
    r, calls = file_runner()
    r.verify("import Mathlib\nimport Aesop\n\ntheorem t : True := trivial\n", theorem="t")
    r.verify("import Mathlib\nimport Aesop\n\ntheorem u : True := trivial\n", theorem="u")
    imports, first, second = calls  # the import is measured once, not per verification
    assert "theorem" not in imports[0] and imports[1] == 999  # measured under startup_timeout_s
    measured = r.file_import_s["Mathlib\nAesop"]
    assert 0.2 <= measured < 2
    assert first[1] == second[1] == pytest.approx(3 + measured)  # not timeout + startup_timeout_s


def test_file_backend_marks_the_run_degraded():
    r, _ = file_runner()
    r.verify(CODE)
    info = r.run_info()
    assert info["degraded"] is True and info["file_verifications"] == 1
    assert make_runner().run_info()["degraded"] is False


def test_file_import_failure_is_a_crash():
    r, _ = file_runner()
    r._run_file = lambda code, timeout: ("finished", 1, "", "unknown module prefix")
    res = r.verify(CODE)
    assert res.status == "crash" and r.warnings[-1]["reason"] == "file_import_failed"


def test_repl_timeout_is_final(runner):
    used = []
    runner._verify_file = lambda *args: used.append(args) or LeanResult("finished", "file")
    assert runner.verify(CODE + "-- HANG\n", timeout=1).status == "timeout"
    assert used == []  # never retried with the file back-end


def test_failed_import_measurement_is_remembered():
    r, calls = file_runner()
    r._run_file = lambda code, timeout: calls.append(code) or ("finished", 1, "", "unknown module prefix")
    r.verify(CODE)
    r.verify(CODE.replace("trivial", "True.intro"))
    assert len(calls) == 1  # the import is not retried on every verification


def test_cache_returns_independent_copies():
    r = make_runner(cache_size=8)
    try:
        first = r.verify(CODE, theorem="t")
        first.axioms.append("cheat")
        first.messages.append(None)
        again = r.verify(CODE, theorem="t")
        assert again.cached and again.axioms == ["propext"] and again.messages == []
    finally:
        r.close()
