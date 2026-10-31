"""Offline tests of the baselines C1/C2, prompts and run_config.json (fake LLM, fake Lean)."""

from __future__ import annotations

import json

import pytest

from fake_llm import MODELS_CFG, FakeClock, FakeTransport, http_error, pipeline_cfg, response
from formal_mas.agents import prompting
from formal_mas.baselines.baseline_a import clean_tactics, run_baseline_a
from formal_mas.baselines.baseline_b import run_baseline_b
from formal_mas.lean.runner import LeanMessage, LeanResult
from formal_mas.llm.client import LLMClient
from formal_mas.run_config import build_run_config, git_info, write_run_config
from formal_mas.tracing import Budget, Tracer

PROBLEM = {"id": "p", "informal": "x = 2", "statement": "theorem p (x : ℝ) (h₀ : 2 * x + 3 = 7) : x = 2 := by sorry"}


class FakeRunner:
    """Accepts a proof iff its tactic block contains `good`; records the files it saw."""

    def __init__(self, raise_exc: Exception | None = None):
        self.files: list[tuple[str, str]] = []
        self.raise_exc = raise_exc

    def verify(self, code, theorem=None, timeout=None):
        if self.raise_exc:
            raise self.raise_exc
        self.files.append((code, theorem))
        if "good" in code:
            return LeanResult("finished", "repl", axioms=["propext"], declarations=[theorem], theorem=theorem)
        return LeanResult("finished", "repl", [LeanMessage("error", 5, 2, "linarith failed")], axioms=[],
                          declarations=[theorem], theorem=theorem)


def client_for(tmp_path, *script):
    clock = FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=pipeline_cfg(), tracer=tracer,
                       transport=transport, cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep,
                       monotonic=clock.monotonic)
    return client, transport, tracer


def tactics(t: str) -> str:
    return json.dumps({"tactics": t})


# -- C1 ------------------------------------------------------------------------


def test_c1_solves_and_code_builds_the_statement(tmp_path):
    client, transport, tracer = client_for(tmp_path, tactics("linarith"), tactics("```lean\nby\n  good\n```"))
    runner, budget = FakeRunner(), Budget(16)
    res = run_baseline_a(PROBLEM, client, runner, budget, tracer, attempts=4)
    assert res["status"] == "solved" and res["solved"] and res["proof"] == "  good"
    assert [a["reason"] for a in res["attempts"]] == ["error", "accepted"]
    assert budget.used == 2 and [r["sample"] for r in res["attempts"]] == [0, 1]
    code, theorem = runner.files[-1]
    assert theorem == "p" and "theorem p (x : ℝ) (h₀ : 2 * x + 3 = 7) :\n    x = 2 := by\n" in code
    # identical requests, but the samples of one problem are distinct cache entries
    assert len(transport.calls) == 2
    assert len({e["cache_key"] for e in tracer.events if e["event"] == "llm_call"}) == 2


def test_c1_sanitize_rejection_is_a_failed_attempt(tmp_path):
    evil = tactics("exact h\ntheorem evil : False := sorry")
    client, _, tracer = client_for(tmp_path, evil, tactics("begin\n  simp\nend"), tactics("sorry"))
    runner = FakeRunner()
    res = run_baseline_a(PROBLEM, client, runner, Budget(16), tracer, attempts=3)
    assert res["status"] == "failed" and not runner.files  # never compiled
    reasons = [a["reason"] for a in res["attempts"]]
    assert all(r.startswith("sanitize:") for r in reasons) and "sanitize:sorry" in reasons
    assert len([e for e in tracer.events if e["event"] == "attempt_failed"]) == 3


def test_c1_budget_and_provider_errors(tmp_path):
    client, _, tracer = client_for(tmp_path, tactics("linarith"))
    budget = Budget(3)
    res = run_baseline_a(PROBLEM, client, FakeRunner(), budget, tracer, attempts=10)
    assert res["status"] == "budget_exhausted" and budget.used == 3 and len(res["attempts"]) == 3
    client, _, tracer = client_for(tmp_path / "b", http_error(402))
    res = run_baseline_a(PROBLEM, client, FakeRunner(), Budget(3), tracer)
    assert res["status"] == "provider_error" and res["attempts"] == []


def test_c1_invalid_output_and_invalid_statement(tmp_path):
    client, _, tracer = client_for(tmp_path, "no json here")
    res = run_baseline_a(PROBLEM, client, FakeRunner(), Budget(16), tracer, attempts=2)
    assert [a["reason"] for a in res["attempts"]] == ["invalid_output", "invalid_output"]
    assert res["status"] == "failed"
    for bad in ({"id": "x", "statement": "def f := 1"}, {"id": "y"},
                {"id": "z", "statement": "theorem z : 1 = 1 := by rfl"}):
        res = run_baseline_a(bad, client, FakeRunner(), Budget(16), tracer)
        assert res["status"] == "invalid_statement"


def test_c1_never_raises(tmp_path):
    client, _, tracer = client_for(tmp_path, tactics("linarith"))
    res = run_baseline_a(PROBLEM, client, FakeRunner(RuntimeError("REPL exploded")), Budget(16), tracer)
    assert res["status"] == "error" and "REPL exploded" in res["detail"]
    assert any(e["event"] == "node_error" for e in tracer.events)


def test_clean_tactics():
    assert clean_tactics("```lean\nby\n  simp\n```") == "  simp"
    assert clean_tactics("by norm_num") == "norm_num"
    assert clean_tactics("nlinarith [sq_nonneg (a - b)]") == "nlinarith [sq_nonneg (a - b)]"
    assert clean_tactics("bycases") == "bycases"  # only a whole `by` word


# -- C2 ------------------------------------------------------------------------


SOL = json.dumps({"solution": "Subtract 3 and divide by 2.", "final_answer": "x = 2"})


def test_c2_self_assessment_rounds(tmp_path):
    client, transport, tracer = client_for(tmp_path, SOL, json.dumps({"verdict": "Incorrect", "confidence": 0.4}),
                                           SOL, json.dumps({"verdict": "correct", "confidence": 0.9, "issues": []}))
    budget = Budget(16)
    res = run_baseline_b(PROBLEM, client, budget, tracer, attempts=2)
    assert res["status"] == "claimed_solved" and res["claimed_solved"] and res["solved"] is None
    assert [r["verdict"] for r in res["rounds"]] == ["incorrect", "correct"] and budget.used == 4
    assert all(r[1]["extra_body"] == {"thinking": {"type": "enabled"}} for r in transport.calls)


def test_c2_failures_never_raise(tmp_path):
    client, _, tracer = client_for(tmp_path, SOL, json.dumps({"verdict": "incorrect"}))
    res = run_baseline_b(PROBLEM, client, Budget(16), tracer, attempts=2)
    assert res["status"] == "claimed_failed" and not res["claimed_solved"]
    client, _, tracer = client_for(tmp_path / "b", http_error(401))
    assert run_baseline_b(PROBLEM, client, Budget(16), tracer)["status"] == "provider_error"
    client, _, tracer = client_for(tmp_path / "c", SOL, json.dumps({"verdict": "correct"}))
    assert run_baseline_b(PROBLEM, client, Budget(1), tracer)["status"] == "budget_exhausted"
    assert run_baseline_b({"id": "q", "statement": "nope"}, client, Budget(4), tracer)["status"] == "invalid_statement"


# -- prompts -------------------------------------------------------------------


def test_every_prompt_loads_and_asks_for_json():
    documents = {"lean4_cheatsheet"}  # reference text inserted into prompts, not a prompt
    prover = {"autoformalizer_prover"}  # whole-proof models: one user turn, answer is a Lean block, no JSON
    names = sorted(p.stem for p in prompting.PROMPTS_DIR.glob("*.md") if p.stem not in documents | prover)
    assert {"baseline_a", "baseline_b_solve", "baseline_b_assess", "json_repair", "autoformalizer"} <= set(names)
    for name in prover:
        sections = prompting.load(name)
        assert [r for r, _ in sections] == ["user"] and "Complete the following Lean 4 code" in sections[0][1]
    for name in names:
        sections = prompting.load(name)
        assert [r for r, _ in sections] == ["system", "user"], name
        # DeepSeek JSON mode needs the word "json" in the prompt
        assert "json" in " ".join(t for _, t in sections).lower(), name
        assert "<!--" not in " ".join(t for _, t in sections)


def test_render_fills_and_requires_placeholders():
    msgs = prompting.render("baseline_b_assess", informal="P", solution="S {{not_a_placeholder}}")
    assert msgs[0]["role"] == "system" and "S {{not_a_placeholder}}" in msgs[1]["content"]
    with pytest.raises(KeyError, match="solution"):
        prompting.render("baseline_b_assess", informal="P")


def test_baseline_a_prompt_asks_for_tactics_only():
    text = " ".join(t for _, t in prompting.load("baseline_a")).lower()
    assert "only tactics" in text and "do not restate the theorem" in text


# -- run_config.json -----------------------------------------------------------


def test_run_config_contents(tmp_path):
    client, *_ = client_for(tmp_path, "x")
    data = tmp_path / "d.jsonl"
    data.write_text('{"id": "p"}\n', encoding="utf-8")
    lean_info = {"degraded": False, "repl_commit": "abc"}
    cfg = build_run_config(run_id="r", config_name="C1", seed=7, dataset=data, split="dev", llm_info=client.info(),
                           lean_run_info=lean_info, pipeline_cfg={"budget": {"b_max": 16}}, argv=["run_eval.py"])
    path = write_run_config(tmp_path / "run", cfg)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["seed"] == 7 and saved["profile"] == "experiment" and saved["dataset"]["sha256"]
    assert saved["models"]["roles"]["baseline_a"]["expected_version"] == "DeepSeek-V4.1-Flash-1017"
    assert saved["lean"]["runner"]["degraded"] is False
    assert {"lean_toolchain", "mathlib_rev"} <= set(saved["lean"])
    assert {"commit", "dirty", "changed_files"} <= set(saved["git"])
    assert {"present", "dense", "bm25", "corpus"} <= set(saved["rag_index"])
    assert saved["packages"]["openai"]


def test_git_info_outside_a_repo(tmp_path):
    info = git_info(tmp_path)
    assert info["commit"] is None and info["dirty"] is None


# -- contracts -----------------------------------------------------------------


def test_docs_schemas_match_contracts():
    from formal_mas.agents.contracts import schemas
    from formal_mas.config import ROOT
    for name, schema in schemas().items():
        path = ROOT / "docs" / "schemas" / f"{name}.json"
        assert path.exists(), f"run `python -m formal_mas.agents.contracts` ({name})"
        assert json.loads(path.read_text(encoding="utf-8")) == schema, f"{path.name} is stale"


def test_contracts_validate():
    from pydantic import ValidationError

    from formal_mas.agents.contracts import NLSolution, SelfAssessment, TacticProof
    assert SelfAssessment.model_validate({"verdict": " Correct "}).verdict == "correct"
    with pytest.raises(ValidationError):
        SelfAssessment.model_validate({"verdict": "maybe"})
    with pytest.raises(ValidationError):
        SelfAssessment.model_validate({"verdict": "correct", "confidence": 2})
    with pytest.raises(ValidationError):
        TacticProof.model_validate({})
    assert NLSolution.model_validate({"solution": "s"}).final_answer == ""


def test_git_info_keeps_leading_dot_of_paths(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    info = git_info(tmp_path)
    assert info["dirty"] is True and ".hidden" in info["changed_files"]
