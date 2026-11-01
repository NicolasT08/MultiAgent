"""Offline tests of Phase 5: Autoformalizer (chat and prover styles), automation pre-pass and the
forward path of the graph, PRE-PASS → PLAN → PRE-PASS → GENERATE → FORMALIZE → VERIFY → ASSEMBLE,
with one attempt per subgoal (k_max = 1, R_max = 0, as C4). Fake LLM, Lean and Retriever."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

from fake_llm import MODELS_CFG, FakeClock, FakeTransport, http_error, pipeline_cfg
from formal_mas.agents.autoformalizer import prover_tactics, run_autoformalizer
from formal_mas.agents.contracts import LemmaHit, Plan
from formal_mas.agents.prepass import prepass, subgoal_targets, theorem_target
from formal_mas.lean.runner import LeanMessage, LeanResult
from formal_mas.lean.statement import parse_theorem
from formal_mas.llm.client import LLMClient
from formal_mas.orchestrator.graph import run_graph
from formal_mas.tracing import Budget, Tracer

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = {"id": "p", "informal": "If x + y = 5 and x - y = 1 then x * y = 6.",
           "statement": "theorem p (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) : x * y = 6 := by sorry"}
PLAN = json.dumps({"subgoals": ["x = 3", "y = 2"], "closing": "rw [h1, h2]\nnorm_num", "rationale": "solve"})
TACTICS = ["norm_num", "linarith", "nlinarith"]
CFG = {"planner": {"n_max": 3}, "generator": {"samples": 2}, "budget": {"b_max": 16}, "critic": {"k_max": 1, "r_max": 0},
       "prepass": {"enabled": True, "tactics": TACTICS, "heartbeats": 1000, "timeout_s": 5}}


class FakeLean:
    """Files with `sorry` compile (skeletons). Single-theorem files without `sorry` are
    accepted unless they contain `WRONG`. Multi-lemma files (theorem=None): `<base>_auto<j>`
    compiles iff (base, its tactic) is in `auto`; refutation / closing candidates never do."""

    def __init__(self, auto=(), raise_exc=None):
        self.auto, self.raise_exc = set(auto), raise_exc
        self.files: list[tuple[str | None, str]] = []

    def verify(self, code, theorem=None, timeout=None):
        if self.raise_exc:
            raise self.raise_exc
        self.files.append((theorem, code))
        if theorem is None:
            lines, msgs = code.split("\n"), []
            for no, line in enumerate(lines, start=1):
                if line.startswith("theorem "):
                    name = line.split()[1]
                    m = re.fullmatch(r"(.+)_auto\d+", name)
                    if not (m and (m.group(1), lines[no + 1].strip()) in self.auto) or name.endswith("_close_auto1"):
                        msgs.append(LeanMessage("error", no + 2, 2, "failed"))
            return LeanResult("finished", "repl", msgs)
        if "WRONG" in code:
            return LeanResult("finished", "repl", [LeanMessage("error", 12, 2, "linarith failed")], theorem=theorem)
        if "sorry" in code:
            return LeanResult("finished", "repl", [LeanMessage("warning", 8, 8, "declaration uses `sorry`")],
                              theorem=theorem)
        return LeanResult("finished", "repl", axioms=["propext"], declarations=[theorem], theorem=theorem)

    def verified(self, theorem):
        return [code for name, code in self.files if name == theorem]


class FakeRetriever:
    def retrieve(self, query, k=None):
        return [LemmaHit(name="sq_nonneg", signature="(a : α) : 0 ≤ a ^ 2", score=1.0)]


def sketch(strategy="algebraic_manipulation"):
    return json.dumps({"strategy": strategy, "sketch": "1. Solve the linear system.", "lemmas": []})


def answer(tactics: dict | str = "linarith", plan=PLAN):
    """Route by system prompt: planner, generator, autoformalizer (tactics per lemma name)."""
    def reply(request):
        system = request["messages"][0]["content"]
        if "split a theorem" in system:
            return plan
        if "turn a natural-language proof sketch" in system:
            if isinstance(tactics, str):
                return json.dumps({"tactics": tactics})
            lemma = re.search(r"theorem (\w+)", request["messages"][1]["content"]).group(1)
            return json.dumps({"tactics": tactics[lemma]})
        return sketch()
    return reply


def client_for(tmp_path, *script, models_cfg=MODELS_CFG):
    clock = FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    client = LLMClient("experiment", models_cfg=models_cfg, pipeline_cfg=pipeline_cfg(), tracer=tracer,
                       transport=transport, cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep,
                       monotonic=clock.monotonic)
    return client, transport, tracer


def events(tracer, name):
    return [e for e in tracer.events if e["event"] == name]


def linear(tmp_path, lean, *script, b_max=16, cfg=CFG, models_cfg=MODELS_CFG):
    client, transport, tracer = client_for(tmp_path, *script, models_cfg=models_cfg)
    budget = Budget(b_max)
    out = run_graph(PROBLEM, client, lean, FakeRetriever(), budget, tracer, {**cfg, "budget": {"b_max": b_max}})
    return out, budget, transport, tracer


# -- pre-pass ------------------------------------------------------------------


def test_prepass_finds_the_first_accepted_tactic():
    thm = parse_theorem(PROBLEM["statement"])
    lean = FakeLean(auto={("p", "linarith"), ("p", "nlinarith"), ("p_sg2", "norm_num")})
    tracer = Tracer()
    targets = [theorem_target(thm)] + subgoal_targets(thm, ["x = 3", "y = 2"], [2])
    found = prepass(thm, ["x = 3", "y = 2"], targets, lean, tracer, CFG["prepass"])
    assert found == {"theorem": "linarith", "sg2": "norm_num"}
    auto_file = lean.files[0][1]
    assert auto_file.count("set_option maxHeartbeats 1000 in") == 6  # 2 targets × 3 tactics, one compilation
    assert "theorem p_sg2_auto1 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (h1 : x = 3) :\n    y = 2" in auto_file
    # each found tactic is confirmed alone on the file the pipeline will use
    assert lean.verified("p")[0].endswith(":\n    x * y = 6 := by\n  linarith\n")
    assert "theorem p_sg2 " in lean.verified("p_sg2")[0]
    assert events(tracer, "prepass")[0]["found"] == found
    # off for the ablation, or no targets → nothing compiled
    assert prepass(thm, [], [theorem_target(thm)], lean, tracer, {**CFG["prepass"], "enabled": False}) == {}
    assert prepass(thm, [], [theorem_target(thm)], lean, tracer, None) == {} and len(lean.files) == 3


def test_prepass_never_raises():
    thm = parse_theorem(PROBLEM["statement"])
    tracer = Tracer()
    assert prepass(thm, [], [theorem_target(thm)], FakeLean(raise_exc=RuntimeError("x")), tracer, CFG["prepass"]) == {}
    assert prepass(thm, [], [theorem_target(thm)], FakeLean(), tracer, {"enabled": True}) == {}  # bad config
    assert len(events(tracer, "node_error")) == 2


# -- autoformalizer --------------------------------------------------------------


def plan_obj():
    return Plan(subgoals=["x = 3", "y = 2"], closing="rw [h1, h2]\nnorm_num", hypotheses=["h1", "h2"], source="llm")


def test_autoformalizer_chat_style(tmp_path):
    client, transport, tracer = client_for(tmp_path, json.dumps({"tactics": "```lean\nby\n  linarith [h₀, h1]\n```"}))
    lean, budget = FakeLean(), Budget(16)
    sk = {"strategy": "algebraic_manipulation", "sketch": "1. Subtract."}
    out = run_autoformalizer(PROBLEM, plan_obj(), 2, sk, client, lean, FakeRetriever(), budget, tracer)
    assert out["status"] == "ok" and out["tactics"] == "linarith [h₀, h1]" and out["lemma"] == "p_sg2"
    assert budget.used == 1 and out["lemmas"] == ["sq_nonneg"]
    (name, code), = lean.files
    assert name == "p_sg2" and "(h1 : x = 3) :\n    y = 2 := by\n  linarith [h₀, h1]" in code
    request = transport.calls[0][1]
    system, user = request["messages"][0]["content"], request["messages"][1]["content"]
    assert request["response_format"] == {"type": "json_object"}
    assert "No goals to be solved" in system and "<!--" not in system  # the cheat sheet, without its comment
    assert "theorem p_sg2 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (h1 : x = 3) :\n    y = 2 := by" in user
    assert "1. Subtract." in user and "step 2 of 2" in user and "sq_nonneg : (a : α) : 0 ≤ a ^ 2" in user


@pytest.mark.parametrize("tactics, status, reason", [
    ("sorry", "failed", "sanitize:sorry"),
    ("exact?", "failed", "sanitize:search"),
    ("linarith\ntheorem q : True := trivial", "failed", "sanitize:escape"),
    ("WRONG", "failed", "lean:error"),
])
def test_autoformalizer_rejections(tmp_path, tactics, status, reason):
    client, _, tracer = client_for(tmp_path, json.dumps({"tactics": tactics}))
    lean = FakeLean()
    out = run_autoformalizer(PROBLEM, plan_obj(), 1, None, client, lean, FakeRetriever(), Budget(16), tracer)
    assert (out["status"], out["reason"]) == (status, reason)
    assert len(lean.files) == (1 if reason.startswith("lean") else 0)  # unsafe text is never compiled


def test_autoformalizer_stops(tmp_path):
    for script, status in ((http_error(401), "provider_error"), ("not json", "failed")):
        client, _, tracer = client_for(tmp_path / status, script)
        out = run_autoformalizer(PROBLEM, plan_obj(), 1, None, client, FakeLean(), FakeRetriever(), Budget(16), tracer)
        assert out["status"] == status
    client, _, tracer = client_for(tmp_path / "b", "{}")
    out = run_autoformalizer(PROBLEM, plan_obj(), 1, None, client, FakeLean(), FakeRetriever(), Budget(0), tracer)
    assert out["status"] == "budget_exhausted"
    client, _, tracer = client_for(tmp_path / "c", json.dumps({"tactics": "linarith"}))
    out = run_autoformalizer(PROBLEM, plan_obj(), 1, None, client, FakeLean(raise_exc=RuntimeError("x")),
                             FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "error" and events(tracer, "node_error")


PROVER_ANSWER = """Plan: subtract the equations, then solve.

```lean4
import Mathlib
theorem p_sg1 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) : x = 3 ∨ True := by
  left
  linarith
```"""


def prover_models():
    cfg = copy.deepcopy(MODELS_CFG)
    cfg["models"]["prover"] = {**cfg["models"]["flash"], "id": "goedel-prover", "prompt_style": "prover",
                               "json_mode": False}
    cfg["profiles"]["experiment"]["roles"]["autoformalizer"] = {"model": "prover", "max_tokens": 1000}
    return cfg


def test_autoformalizer_prover_style(tmp_path):
    client, transport, tracer = client_for(tmp_path, PROVER_ANSWER, models_cfg=prover_models())
    lean = FakeLean()
    out = run_autoformalizer(PROBLEM, plan_obj(), 1, {"strategy": "other", "sketch": "Subtract -/ the equations."},
                             client, lean, FakeRetriever(), Budget(16), tracer)
    # only the tactics are kept; the statement the model wrote (`x = 3 ∨ True`) is discarded
    assert out["status"] == "ok" and out["tactics"] == "left\nlinarith"
    assert "theorem p_sg1 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) :\n    x = 3 := by\n  left\n  linarith" in \
        lean.files[0][1]
    request = transport.calls[0][1]
    assert "response_format" not in request and [m["role"] for m in request["messages"]] == ["user"]
    user = request["messages"][0]["content"]
    assert user.startswith("Complete the following Lean 4 code:\n\n```lean4\nimport Mathlib")
    assert "Subtract - / the equations." in user  # the sketch cannot close the Lean comment
    assert "theorem p_sg1 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) :\n    x = 3 := by\n```" in user
    # no block for the lemma → no_tactics
    client, _, tracer = client_for(tmp_path / "b", "I cannot prove it.", models_cfg=prover_models())
    out = run_autoformalizer(PROBLEM, plan_obj(), 1, None, client, FakeLean(), FakeRetriever(), Budget(16), tracer)
    assert (out["status"], out["reason"]) == ("failed", "no_tactics")


def test_prover_tactics_extraction():
    two = "```lean4\ntheorem p_sg1 : x := by\n  simp\n```\nbetter:\n```lean4\nlemma p_sg1 (a : ℕ) : a = a := by\n  rfl\n```"
    assert prover_tactics(two, "p_sg1") == "rfl"  # last block
    assert prover_tactics("```lean4\ntheorem p_sg10 : x := by\n  simp\n```", "p_sg1") is None
    assert prover_tactics("theorem p_sg1 (h : a := 1) : b := by\n  exact h", "p_sg1") == "exact h"
    assert prover_tactics("```lean4\ntheorem p_sg1 : x := by\n  omega", "p_sg1") == "omega"  # unterminated block
    assert prover_tactics(None, "p_sg1") is None
    # a later declaration is cut off; a first tactic on the `by` line keeps the block aligned
    extra = "```lean4\ntheorem p_sg1 : x := by\n  simp\n  omega\n\ntheorem helper : True := trivial\n```"
    assert prover_tactics(extra, "p_sg1") == "simp\nomega"
    assert prover_tactics("theorem p_sg1 : x := by constructor\n  · simp\n  · omega", "p_sg1") == \
        "constructor\n· simp\n· omega"


# -- linear pipeline --------------------------------------------------------------


def test_linear_solved_by_the_theorem_prepass(tmp_path):
    lean = FakeLean(auto={("p", "nlinarith")})
    out, budget, transport, tracer = linear(tmp_path, lean, answer())
    assert out["status"] == "solved" and out["solved"] and out["stage"] == "prepass"
    assert budget.used == 0 and transport.calls == []
    assert out["proof"].endswith("x * y = 6 := by\n  nlinarith\n") and out["final"]["accepted"]


def test_linear_full_path(tmp_path):
    lean = FakeLean()
    out, budget, transport, tracer = linear(tmp_path, lean, answer({"p_sg1": "linarith", "p_sg2": "linarith [h1]"}))
    assert out["status"] == "solved" and out["stage"] == "assemble"
    assert budget.used == 1 + 2 * 2 + 2  # planner + 2 subgoals × (2 sketches + 1 formalization)
    assert out["proof"].endswith("  have h1 : x = 3 := by\n    linarith\n  have h2 : y = 2 := by\n    linarith [h1]\n"
                                 "  rw [h1, h2]\n  norm_num\n")
    assert lean.verified("p")[-1] == out["proof"]  # the assembled file is what was accepted
    assert [f["status"] for f in out["formalizer"]] == ["ok", "ok"]
    assert events(tracer, "pipeline_end")[0]["solved"] is True


def test_linear_subgoal_prepass_skips_generation(tmp_path):
    lean = FakeLean(auto={("p_sg1", "linarith")})
    out, budget, transport, _ = linear(tmp_path, lean, answer({"p_sg2": "linarith"}))
    assert out["status"] == "solved" and out["prepass"]["subgoals"] == {"sg1": "linarith"}
    assert budget.used == 1 + 2 + 1 and [g["subgoals"][0]["index"] for g in out["generator"]] == [2]
    assert "  have h1 : x = 3 := by\n    linarith\n" in out["proof"]


@pytest.mark.parametrize("tactics, stage", [("WRONG", "verify:sg1"), ("sorry", "formalize:sg1")])
def test_linear_stops_at_the_first_failed_subgoal(tmp_path, tactics, stage):
    out, budget, _, _ = linear(tmp_path, FakeLean(), answer({"p_sg1": tactics, "p_sg2": "linarith"}))
    assert out["status"] == "failed" and out["stage"] == stage and not out["solved"] and out["proof"] is None
    assert len(out["formalizer"]) == 1 and budget.used == 1 + 2 + 1  # sg2 is not sketched: per-subgoal path


def test_linear_assembly_must_be_accepted(tmp_path):
    plan = json.dumps({"subgoals": ["x = 3", "y = 2"], "closing": "rw [h1, h2]\nnorm_num -- WRONG"})
    lean = FakeLean()
    # the closing lemma fails in the planner too (WRONG), so the plan is trivial: x * y = 6 itself
    out, _, _, _ = linear(tmp_path, lean, answer("WRONG", plan=plan))
    assert out["planner"]["plan"]["source"] == "trivial" and out["stage"] == "verify:sg1"


def test_linear_direct_plan(tmp_path):
    direct = json.dumps({"subgoals": [], "closing": "nlinarith [h₀, h₁]"})
    out, budget, _, _ = linear(tmp_path, FakeLean(), answer(plan=direct))
    assert out["status"] == "solved" and out["stage"] == "plan" and budget.used == 1
    assert out["proof"].endswith("x * y = 6 := by\n  nlinarith [h₀, h₁]\n")


def test_linear_budget_and_stops(tmp_path):
    out, budget, _, _ = linear(tmp_path, FakeLean(), answer(), b_max=3)  # planner + 2 sketches, then nothing
    assert out["status"] == "budget_exhausted" and out["stage"] == "formalize:sg1" and budget.used == 3
    out, _, _, _ = linear(tmp_path / "b", FakeLean(), http_error(401))
    assert out["status"] == "provider_error" and out["stage"] == "plan" and not out["solved"]
    # Lean dies everywhere: every node catches it; the planner's trivial plan is unchecked → stop at plan
    out, _, _, tracer = linear(tmp_path / "c", FakeLean(raise_exc=RuntimeError("REPL died")), answer())
    assert out["status"] == "failed" and out["stage"] == "plan" and not out["solved"]
    assert len(events(tracer, "node_error")) == 2  # pre-pass and planner
    bad = run_graph({"id": "x", "statement": "def f := 1"}, None, FakeLean(), None, Budget(16), Tracer(), CFG)
    assert bad["status"] == "invalid_statement"


def test_linear_prepass_off(tmp_path):
    lean = FakeLean(auto={("p", "nlinarith")})
    cfg = {**CFG, "prepass": {**CFG["prepass"], "enabled": False}}
    out, budget, _, _ = linear(tmp_path, lean, answer(), cfg=cfg)
    assert out["stage"] == "assemble" and budget.used == 7 and out["prepass"] == {"theorem": None, "subgoals": {}}


def test_run_eval_c4(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    spec = importlib.util.spec_from_file_location("run_eval_lin", ROOT / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = tmp_path / "p.jsonl"
    data.write_text(json.dumps({**PROBLEM, "split": "dev"}) + "\n", encoding="utf-8")
    clock = FakeClock()

    class Runner(FakeLean):
        def start(self):
            return True

        def close(self):
            pass

        def run_info(self):
            return {"backend": "fake"}

    class Retr(FakeRetriever):
        mode, warnings = "fake", []

    rc = mod.main(["--config", "C4", "--data", str(data), "--echo"], models_cfg=MODELS_CFG,
                  pipeline_cfg={**pipeline_cfg(), **CFG},
                  client_kwargs={"transport": FakeTransport(answer()), "cache_dir": tmp_path / "cache",
                                 "clock": clock, "sleep": clock.sleep, "monotonic": clock.monotonic},
                  runner_factory=Runner, retriever_factory=Retr, runs_dir=tmp_path / "runs")
    assert rc == 0
    run_dir = next((tmp_path / "runs").iterdir())
    row = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "solved" and row["solved"] is True and row["stage"] == "assemble"
    assert row["llm"]["llm_calls"] == 7 and "have h2 : y = 2" in row["proof"]
    assert json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))["summary"]["solved"] == 1
