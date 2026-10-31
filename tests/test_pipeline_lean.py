"""Phase 5 with the real Lean REPL (`pytest -m slow`): the automation pre-pass and the
assembled proof of the linear pipeline (fake LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from formal_mas.agents.prepass import prepass, subgoal_targets, theorem_target
from formal_mas.config import load_pipeline
from formal_mas.lean.runner import LeanRunner
from formal_mas.lean.statement import parse_theorem
from formal_mas.lean.syntax_check import SyntaxChecker
from formal_mas.orchestrator.linear import run_linear
from formal_mas.tracing import Budget, Tracer
from test_pipeline_linear import PROBLEM, FakeRetriever, answer, client_for

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (ROOT / "lean_repl" / ".lake" / "build" / "bin").exists(), reason="Lean REPL not built"),
]
PREPASS = load_pipeline()["prepass"]  # the configured tactic list
SMOKE = [json.loads(line) for line in (ROOT / "data" / "smoke.jsonl").read_text(encoding="utf-8").splitlines()]
AIME = ("import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 0\n\nopen BigOperators Real Nat Topology Rat\n\n"
        "theorem aime_1991_p1 (x y : ℕ) (h₀ : 0 < x ∧ 0 < y) (h₁ : x * y + (x + y) = 71)\n"
        "  (h₂ : x ^ 2 * y + x * y ^ 2 = 880) : x ^ 2 + y ^ 2 = 146 := by sorry")


@pytest.fixture(scope="module")
def runner():
    r = LeanRunner()
    assert r.start(), r.warnings
    yield r
    r.close()


def test_prepass_on_smoke_and_aime(runner):
    for p in SMOKE:  # every smoke problem is closed by one automatic tactic
        thm = parse_theorem(p["statement"])
        assert "theorem" in prepass(thm, [], [theorem_target(thm)], runner, Tracer(), PREPASS), p["id"]
    thm = parse_theorem(AIME)
    tracer = Tracer()
    assert prepass(thm, [], [theorem_target(thm)], runner, tracer, PREPASS) == {}
    assert events_seconds(tracer) < 30
    # DeepSeek's plan (2026-10-29): the factorization step is closed by automation
    subgoals = ["(x + y) * (x * y) = 880", "(x + y) = 16 ∨ (x + y) = 55", "x + y = 16"]
    found = prepass(thm, subgoals, subgoal_targets(thm, subgoals, [1, 2, 3]), runner, Tracer(), PREPASS)
    assert "sg1" in found


def events_seconds(tracer):
    return next(e["seconds"] for e in tracer.events if e["event"] == "prepass")


def test_linear_pipeline_assembles_an_accepted_proof(runner, tmp_path):
    cfg = {"planner": {"n_max": 3}, "generator": {"samples": 2}, "prepass": {**PREPASS, "enabled": False}}
    client, _, tracer = client_for(tmp_path, answer({"p_sg1": "linarith", "p_sg2": "linarith [h1]"}))
    out = run_linear(PROBLEM, client, runner, FakeRetriever(), Budget(16), tracer, cfg, syntax=SyntaxChecker(runner))
    assert out["status"] == "solved" and out["stage"] == "assemble", out
    assert out["final"]["accepted"] and "have h2 : y = 2 := by\n    linarith [h1]" in out["proof"]
    # a wrong block is rejected by Lean at its own lemma, before assembly
    client, _, tracer = client_for(tmp_path / "b", answer({"p_sg1": "norm_num", "p_sg2": "linarith"}))
    out = run_linear(PROBLEM, client, runner, FakeRetriever(), Budget(16), tracer, cfg, syntax=SyntaxChecker(runner))
    assert out["status"] == "failed" and out["stage"] == "verify:sg1"
