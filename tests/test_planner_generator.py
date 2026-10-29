"""Offline tests of the Planner and the Generator (Phase 4): contracts, trivial-plan fallback,
strategy vote and tie-break, budget, and nodes that never raise (fake LLM, Lean, Retriever)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fake_llm import MODELS_CFG, FakeClock, FakeTransport, http_error, pipeline_cfg
from formal_mas.agents import prompting
from formal_mas.agents.contracts import STRATEGIES, LemmaHit, Plan, PlanProposal, Sketch, SketchProposal
from formal_mas.agents.generator import run_generator, vote
from formal_mas.agents.planner import run_planner
from formal_mas.lean.runner import LeanMessage, LeanResult
from formal_mas.llm.client import LLMClient
from formal_mas.llm.spend import SpendGuard
from formal_mas.tracing import Budget, Tracer

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = {"id": "p", "informal": "If x + y = 5 and x - y = 1 then x * y = 6.",
           "statement": "theorem p (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) : x * y = 6 := by sorry"}
GOOD_PLAN = json.dumps({"subgoals": ["x = 3", "y = 2"], "closing": "rw [h1, h2]\nnorm_num",
                        "rationale": "solve the system"})


class FakeRunner:
    """Skeletons compile (with sorry) unless they contain `BAD`. Files without `sorry` (closing
    lemmas `*_close`, direct proofs) are accepted unless they contain `BAD` (error) or
    `HIDDEN_SORRY` (stands for any sorry the sanitizer misses; real Lean reports it as:
    compiles, axiom sorryAx). Multi-lemma files (theorem=None: refutation, closing
    candidates): `_consistent` is proved iff `consistent`, `_ref<i>` iff its `(hr : P)` has P
    in `refutable`, `_close_auto<j>` iff its tactic is in `auto_ok`; the others get an error;
    `lemma_status` (e.g. timeout) replaces the status."""

    def __init__(self, raise_exc: Exception | None = None, refutable=(), consistent=False, auto_ok=(),
                 lemma_status="finished"):
        self.files: list[str] = []
        self.raise_exc = raise_exc
        self.refutable, self.consistent, self.auto_ok = set(refutable), consistent, set(auto_ok)
        self.lemma_status = lemma_status

    def verify(self, code, theorem=None, timeout=None):
        if self.raise_exc:
            raise self.raise_exc
        self.files.append(code)
        if theorem is None:
            return self._lemma_set(code)
        if "BAD" in code:
            return LeanResult("finished", "repl", [LeanMessage("error", 12, 2, "unsolved goals")], theorem=theorem)
        if "sorry" not in code:
            axioms = ["propext", "sorryAx"] if "HIDDEN_SORRY" in code else ["propext"]
            return LeanResult("finished", "repl", axioms=axioms, declarations=[theorem], theorem=theorem)
        return LeanResult("finished", "repl", [LeanMessage("warning", 8, 8, "declaration uses `sorry`")],
                          theorem=theorem)

    def _lemma_set(self, code):
        lines, messages = code.split("\n"), []
        for no, line in enumerate(lines, start=1):
            if not line.startswith("theorem "):
                continue
            name = line.split()[1]
            if name.endswith("_consistent"):
                proved = self.consistent
            elif "_ref" in name:
                proved = any(f"(hr : {p})" in line for p in self.refutable)
            else:
                proved = lines[no + 1].strip() in self.auto_ok
            if not proved:
                messages.append(LeanMessage("error", no + 2, 2, "linarith failed"))
        return LeanResult(self.lemma_status, "repl", messages)


class FakeRetriever:
    def __init__(self, raise_exc: Exception | None = None):
        self.queries: list[str] = []
        self.raise_exc = raise_exc

    def retrieve(self, subgoal, k=None):
        if self.raise_exc:
            raise self.raise_exc
        self.queries.append(subgoal)
        return [LemmaHit(name="sq_nonneg", signature="(a : α) : 0 ≤ a ^ 2", score=1.0)]


def client_for(tmp_path, *script, spend=None):
    clock = FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=pipeline_cfg(), tracer=tracer,
                       transport=transport, cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep,
                       monotonic=clock.monotonic, spend=spend)
    return client, transport, tracer


def events(tracer, name):
    return [e for e in tracer.events if e["event"] == name]


def sketch(strategy: str, text: str = "1. step") -> str:
    return json.dumps({"strategy": strategy, "sketch": text, "lemmas": ["sq_nonneg"]})


# -- contracts -----------------------------------------------------------------


def test_contracts():
    p = PlanProposal.model_validate_json(GOOD_PLAN)
    assert p.subgoals == ["x = 3", "y = 2"] and p.closing.startswith("rw")
    with pytest.raises(ValidationError):
        PlanProposal.model_validate({"subgoals": ["x = 3"]})  # closing is required
    s = SketchProposal.model_validate({"strategy": " Case Analysis ", "sketch": "1."})
    assert s.strategy == "case_analysis" and s.lemmas == []
    assert SketchProposal.model_validate({"strategy": "classical-inequality", "sketch": "."}).strategy == \
        "classical_inequality"
    with pytest.raises(ValidationError):
        SketchProposal.model_validate({"strategy": "magic", "sketch": "1."})
    assert set(STRATEGIES) == set(SketchProposal.model_json_schema()["properties"]["strategy"]["enum"])
    for name in ("planner", "generator"):
        assert "json" in prompting.load(name)[0][1].lower()


def test_prompts_have_the_expected_placeholders():
    assert prompting.placeholders("planner") == {"informal", "declaration", "header_summary", "lemmas", "n_max",
                                                 "hypotheses"}
    assert prompting.placeholders("generator") == {"informal", "theorem", "plan", "index", "count", "target",
                                                   "lemmas", "strategies"}


# -- planner -------------------------------------------------------------------


def test_planner_accepts_a_valid_plan(tmp_path):
    client, transport, tracer = client_for(tmp_path, GOOD_PLAN)
    runner, retriever, budget = FakeRunner(), FakeRetriever(), Budget(16)
    out = run_planner(PROBLEM, client, runner, retriever, budget, tracer, n_max=3)
    assert out["status"] == "ok" and out["lemmas"] == ["sq_nonneg"]
    plan = out["plan"]
    assert plan["source"] == "llm" and plan["subgoals"] == ["x = 3", "y = 2"] and plan["hypotheses"] == ["h1", "h2"]
    assert plan["skeleton_compiles"] is True and budget.used == 1
    # the code wrote the theorem; the model only filled the slots. Order: skeleton with the
    # closing as `sorry`, closing lemma, full skeleton (no refutation / closing_auto configured)
    sk, close, code = runner.files
    assert "  have h2 : y = 2 := by\n    sorry\n  sorry\n" in sk
    assert plan["closing_source"] == "llm" and plan["reason"] is None
    assert "theorem p (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) :\n    x * y = 6 := by" in code
    assert "  have h1 : x = 3 := by\n    sorry\n  have h2 : y = 2 := by\n    sorry\n  rw [h1, h2]\n  norm_num" in code
    # the closing step alone, with the subgoals as hypotheses, must be accepted
    assert ("theorem p_close (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (h1 : x = 3) (h2 : y = 2) :\n"
            "    x * y = 6 := by\n  rw [h1, h2]\n  norm_num") in close
    assert [e["accepted"] for e in events(tracer, "plan_closing")] == [True]
    prompt = transport.calls[0][1]["messages"]
    assert "sq_nonneg : (a : α) : 0 ≤ a ^ 2" in prompt[1]["content"] and "h1, h2, h3" in prompt[0]["content"]
    assert retriever.queries[0].startswith("theorem p (x y : ℝ)")


@pytest.mark.parametrize("answer, reason", [
    (json.dumps({"subgoals": ["x = 3"] * 4, "closing": "linarith"}), "too_many_subgoals"),
    (json.dumps({"subgoals": [], "closing": "BAD"}), "direct_closing_failed: error: line 12"),
    (json.dumps({"subgoals": [], "closing": ""}), "direct_closing_failed: sanitize:empty"),
    (json.dumps({"subgoals": ["x = 3 := by sorry"], "closing": "linarith"}), "sanitize:"),
    (json.dumps({"subgoals": ["x = 3) (hbad : False"], "closing": "linarith"}), "sanitize:brackets"),
    (json.dumps({"subgoals": ["x = 3"], "closing": "native_decide"}), "sanitize:native"),
    (json.dumps({"subgoals": ["x = 3"], "closing": "theorem q : True := trivial"}), "sanitize:"),
    (json.dumps({"subgoals": ["BAD = 3"], "closing": "linarith"}), "skeleton_finished: line 12: unsolved goals"),
    (json.dumps({"subgoals": ["x = 3"], "closing": "BAD"}), "closing_error: line 12: unsolved goals"),
    (json.dumps({"subgoals": ["x = 3"], "closing": "exact sorryAx _ false"}), "sanitize:sorry"),
    # compiles in the skeleton but closes the goal only through a sorry the sanitizer misses
    (json.dumps({"subgoals": ["x = 3"], "closing": "exact HIDDEN_SORRY"}), "closing_nonstandard_axioms: sorryAx"),
    ("not json at all", "invalid_output"),
])
def test_planner_falls_back_to_the_trivial_plan(tmp_path, answer, reason):
    client, _, tracer = client_for(tmp_path, answer)
    runner = FakeRunner()
    out = run_planner(PROBLEM, client, runner, FakeRetriever(), Budget(16), tracer, n_max=3)
    plan = out["plan"]
    assert out["status"] == "trivial" and plan["source"] == "trivial" and plan["reason"].startswith(reason)
    assert plan["subgoals"] == ["x * y = 6"] and plan["closing"] == "exact h1" and plan["skeleton_compiles"]
    assert "  have h1 : x * y = 6 := by\n    sorry\n  exact h1" in runner.files[-2]  # trivial skeleton
    assert "(h1 : x * y = 6) :\n    x * y = 6 := by\n  exact h1" in runner.files[-1]  # its closing lemma
    assert events(tracer, "plan_fallback")[0]["reason"].startswith(reason)


REFUTE = {"enabled": True, "tactics": ["omega", "nlinarith"], "heartbeats": 1000, "timeout_s": 5}
AUTO = {"enabled": True, "tactics": ["HIDDEN_SORRY", "omega", "nlinarith"], "heartbeats": 1000, "timeout_s": 5}


def plan_with(runner, tmp_path, answer=GOOD_PLAN, **kw):
    client, _, tracer = client_for(tmp_path, answer)
    out = run_planner(PROBLEM, client, runner, FakeRetriever(), Budget(16), tracer, n_max=3, **kw)
    return out, tracer


def test_planner_rejects_a_refuted_plan(tmp_path):
    runner = FakeRunner(refutable={"y = 2"})
    out, tracer = plan_with(runner, tmp_path, refute=REFUTE)
    assert out["status"] == "trivial" and out["plan"]["reason"] == "refuted:sg2"
    (ev,) = events(tracer, "plan_refuted")
    assert ev["subgoal"] == 2 and ev["proposition"] == "y = 2" and ev["refuted"] == [2]
    # one file: consistency first, then one lemma per subgoal, theorem hypotheses only
    ref = runner.files[1]
    assert ref.index("theorem p_consistent (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) :\n    False := by") < \
        ref.index("theorem p_ref1") < ref.index("theorem p_ref2")
    assert "theorem p_ref2 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (hr : y = 2) :\n    False" in ref
    assert "(h1 : x = 3)" not in ref  # earlier subgoals are never hypotheses here
    assert ref.count("set_option maxHeartbeats 1000 in\nset_option autoImplicit false in\ntheorem") == 3
    assert "  first\n    | (omega; done)\n    | (nlinarith; done)" in ref
    assert events(tracer, "plan_refute_check")[0]["refuted"] == [2]


def test_planner_refutation_is_skipped_or_harmless(tmp_path):
    # contradictory hypotheses: every subgoal "refuted" → check skipped, plan kept
    out, tracer = plan_with(FakeRunner(refutable={"x = 3", "y = 2"}, consistent=True), tmp_path, refute=REFUTE)
    assert out["status"] == "ok" and events(tracer, "plan_hypotheses_inconsistent")
    assert not events(tracer, "plan_refuted")
    # a timeout refutes nothing
    out, tracer = plan_with(FakeRunner(refutable={"y = 2"}, lemma_status="timeout"), tmp_path / "b", refute=REFUTE)
    assert out["status"] == "ok" and not events(tracer, "plan_refuted")
    # true subgoals
    out, _ = plan_with(FakeRunner(), tmp_path / "c", refute=REFUTE)
    assert out["status"] == "ok"
    # a binder already named hr → the refuted subgoal's hypothesis is hr_
    runner = FakeRunner()
    client, _, tracer = client_for(tmp_path / "d", GOOD_PLAN)
    prob = {**PROBLEM, "statement": "theorem p (x y : ℝ) (hr : x + y = 5) (h₁ : x - y = 1) : x * y = 6 := by sorry"}
    run_planner(prob, client, runner, None, Budget(16), tracer, retrieve=False, refute=REFUTE)
    assert "(hr_ : x = 3) :\n    False" in runner.files[1]


def test_planner_closing_auto(tmp_path):
    bad_closing = json.dumps({"subgoals": ["x = 3", "y = 2"], "closing": "BAD"})
    runner = FakeRunner(auto_ok={"HIDDEN_SORRY", "nlinarith"})
    out, tracer = plan_with(runner, tmp_path, bad_closing, refute=REFUTE, closing_auto=AUTO)
    plan = out["plan"]
    # HIDDEN_SORRY compiles in the candidates file but is not accepted alone → nlinarith
    assert out["status"] == "ok" and plan["source"] == "llm" and plan["closing"] == "nlinarith"
    assert plan["closing_source"] == "auto:nlinarith" and plan["reason"] == "closing_auto:nlinarith"
    assert events(tracer, "plan_closing_auto")[0]["failed_closing"] == "BAD"
    assert [e["accepted"] for e in events(tracer, "plan_closing")] == [False, False, True]
    assert "theorem p_close_auto3 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (h1 : x = 3) (h2 : y = 2) :" in \
        runner.files[3]
    assert runner.files[-1].endswith("  have h2 : y = 2 := by\n    sorry\n  nlinarith\n")  # full skeleton
    # no tactic works → the model's closing failure is the reason
    out, _ = plan_with(FakeRunner(), tmp_path / "b", bad_closing, closing_auto=AUTO)
    assert out["status"] == "trivial" and out["plan"]["reason"].startswith("closing_error")
    # refutation runs first: a refuted plan never reaches closing_auto
    runner = FakeRunner(refutable={"x = 3"}, auto_ok={"nlinarith"})
    out, tracer = plan_with(runner, tmp_path / "c", bad_closing, refute=REFUTE, closing_auto=AUTO)
    assert out["plan"]["reason"] == "refuted:sg1" and not events(tracer, "plan_closing_auto_check")


def test_planner_direct_plan(tmp_path):
    runner = FakeRunner()
    out, tracer = plan_with(runner, tmp_path, json.dumps({"subgoals": [], "closing": "nlinarith [h₀, h₁]"}),
                            refute=REFUTE, closing_auto=AUTO)
    plan = out["plan"]
    assert out["status"] == "ok" and plan["source"] == "direct" and plan["subgoals"] == [] and plan["hypotheses"] == []
    assert plan["skeleton_compiles"] is True and events(tracer, "plan_direct")[0]["accepted"]
    assert runner.files == ["import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\n"
                            "open BigOperators Real Nat Topology Rat\n\ntheorem p (x y : ℝ) (h₀ : x + y = 5) "
                            "(h₁ : x - y = 1) :\n    x * y = 6 := by\n  nlinarith [h₀, h₁]\n"]
    # a direct closing that hides a sorry is not accepted
    out, _ = plan_with(FakeRunner(), tmp_path / "b", json.dumps({"subgoals": [], "closing": "exact HIDDEN_SORRY"}))
    assert out["status"] == "trivial" and out["plan"]["reason"].startswith("direct_closing_failed: nonstandard_axioms")


def test_automatic_tactics_are_checked():
    from formal_mas.lean.statement import UnsafeLean, first_of, parse_theorem, refutation_file

    thm = parse_theorem(PROBLEM["statement"])
    with pytest.raises(UnsafeLean):
        first_of(["omega", "sorry"])
    with pytest.raises(ValueError):
        first_of(["omega\nlinarith"])
    with pytest.raises(ValueError):
        first_of([])
    with pytest.raises(UnsafeLean):
        refutation_file(thm, ["x = 3 := by sorry"], ["omega"], 1000)


def test_compiles_between():
    from formal_mas.lean.runner import compiles_between

    res = LeanResult("finished", "repl", [LeanMessage("error", 12, 2, "x"), LeanMessage("info", 3, 0, "y"),
                                          LeanMessage("warning", 20, 0, "declaration uses `sorry`")])
    assert compiles_between(res, 1, 11) and not compiles_between(res, 10, 15) and not compiles_between(res, 18, 22)
    assert not compiles_between(LeanResult("finished", "repl", [LeanMessage("error", 0, 0, "abort")]), 1, 5)
    assert not compiles_between(LeanResult("finished", "repl", sorries=[{"line": 4}]), 1, 5)
    assert not compiles_between(LeanResult("timeout", "repl"), 1, 5)


def test_planner_budget_provider_and_spend_stops(tmp_path):
    client, _, tracer = client_for(tmp_path, GOOD_PLAN)
    out = run_planner(PROBLEM, client, FakeRunner(), FakeRetriever(), Budget(0), tracer)
    assert out["status"] == "trivial" and out["plan"]["reason"] == "budget_exhausted"
    client, _, tracer = client_for(tmp_path / "b", http_error(401))
    out = run_planner(PROBLEM, client, FakeRunner(), FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "trivial" and out["plan"]["reason"].startswith("provider_error")
    client, transport, tracer = client_for(tmp_path / "c", GOOD_PLAN, spend=SpendGuard(9.0, 0.0001, None))
    out = run_planner(PROBLEM, client, FakeRunner(), FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "budget_stop" and out["plan"]["source"] == "trivial" and transport.calls == []


def test_planner_uses_the_names_the_skeleton_uses(tmp_path):
    # a binder already called h1 → the subgoal hypotheses are hsg1, hsg2 (prompt and skeleton agree)
    prob = {**PROBLEM, "statement": "theorem p (x y : ℝ) (h1 : x + y = 5) (h₁ : x - y = 1) : x * y = 6 := by sorry"}
    plan = json.dumps({"subgoals": ["x = 3", "y = 2"], "closing": "rw [hsg1, hsg2]\nnorm_num"})
    client, transport, tracer = client_for(tmp_path, plan)
    runner = FakeRunner()
    out = run_planner(prob, client, runner, None, Budget(16), tracer, n_max=2, retrieve=False)
    assert out["status"] == "ok" and out["plan"]["hypotheses"] == ["hsg1", "hsg2"] and out["lemmas"] == []
    assert "hsg1, hsg2" in transport.calls[0][1]["messages"][0]["content"] and "have hsg2 : y = 2" in runner.files[0]
    assert "(none retrieved)" in transport.calls[0][1]["messages"][1]["content"]


def test_planner_never_raises(tmp_path):
    client, _, tracer = client_for(tmp_path, GOOD_PLAN)
    out = run_planner(PROBLEM, client, FakeRunner(RuntimeError("REPL exploded")), FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "error" and out["plan"]["source"] == "trivial" and events(tracer, "node_error")
    out = run_planner(PROBLEM, client, FakeRunner(), FakeRetriever(RuntimeError("index")), Budget(16), tracer)
    assert out["status"] == "error" and out["plan"]["subgoals"] == ["x * y = 6"]
    for bad in ({"id": "x"}, {"id": "y", "statement": "def f := 1"}):
        out = run_planner(bad, client, FakeRunner(), FakeRetriever(), Budget(16), tracer)
        assert out["status"] == "invalid_statement" and out["plan"] is None


# -- vote (Eq. 9) --------------------------------------------------------------


def S(i: int, strategy: str) -> Sketch:
    return Sketch(sample=i, strategy=strategy, sketch=f"sketch {i}")


def test_vote_majority_tie_break_and_representative():
    votes, winner, rep = vote([S(0, "case_analysis"), S(1, "induction"), S(2, "case_analysis")])
    assert votes == {"induction": 1, "case_analysis": 2} and winner == "case_analysis" and rep.sample == 0
    # 1-1 tie: the strategy listed first in STRATEGIES wins, whatever the sampling order
    for order in ([S(0, "contradiction"), S(1, "induction")], [S(0, "induction"), S(1, "contradiction")]):
        assert vote(order)[1] == "induction"
    _, winner, rep = vote([S(3, "other"), S(1, "direct_computation"), S(2, "other"), S(0, "direct_computation")])
    assert winner == "direct_computation" and rep.sample == 0  # lowest index of the winner
    assert vote([]) == ({}, None, None)


# -- generator -----------------------------------------------------------------


def plan_obj(subgoals=("x = 3", "y = 2")) -> Plan:
    return Plan(subgoals=list(subgoals), closing="rw [h1, h2]\nnorm_num", hypotheses=["h1", "h2"][:len(subgoals)],
                source="llm")


def test_generator_samples_votes_and_counts_the_budget(tmp_path):
    client, transport, tracer = client_for(tmp_path, sketch("algebraic_manipulation"), sketch("direct_computation"),
                                           sketch("direct_computation"), sketch("algebraic_manipulation"),
                                           sketch("algebraic_manipulation"), sketch("direct_computation"))
    retriever, budget = FakeRetriever(), Budget(16)
    out = run_generator(PROBLEM, plan_obj().model_dump(), client, retriever, budget, tracer, samples=3)
    assert out["status"] == "ok" and budget.used == 6 and len(out["subgoals"]) == 2
    s1, s2 = out["subgoals"]
    assert s1["votes"] == {"algebraic_manipulation": 1, "direct_computation": 2}
    assert s1["strategy"] == "direct_computation" and s1["representative"]["sample"] == 1
    assert s2["strategy"] == "algebraic_manipulation" and s2["representative"]["sample"] == 0
    assert [r["sample"] for r in s1["samples"]] == [0, 1, 2]
    # the query of subgoal 2 is the subgoal lemma statement, with h1 available
    assert retriever.queries[1].startswith("theorem p_sg2 (x y : ℝ) (h₀ : x + y = 5) (h₁ : x - y = 1) (h1 : x = 3)")
    assert retriever.queries[1].rstrip().endswith("y = 2")
    # every sample is a distinct request (cache key includes the sample index); the prompt has the list
    prompt = transport.calls[0][1]["messages"]
    assert ", ".join(STRATEGIES) in prompt[0]["content"] and "step 2 of 2" in transport.calls[3][1]["messages"][1]["content"]
    assert len({json.dumps(c[1], sort_keys=True) for c in transport.calls[:3]}) == 1  # same request, 3 samples
    assert len(events(tracer, "llm_call")) == 6 and not any(e["cached"] for e in events(tracer, "llm_call"))


def test_generator_budget_exhaustion_keeps_partial_sketches(tmp_path):
    client, _, tracer = client_for(tmp_path, sketch("induction"))
    budget = Budget(4)
    out = run_generator(PROBLEM, plan_obj(), client, FakeRetriever(), budget, tracer, samples=3)
    assert out["status"] == "budget_exhausted" and budget.used == 4
    s1, s2 = out["subgoals"]
    assert s1["status"] == "ok" and len(s1["samples"]) == 3
    assert s2["status"] == "budget_exhausted" and len(s2["samples"]) == 1 and s2["strategy"] == "induction"


def test_generator_invalid_samples_and_stops(tmp_path):
    # sample 1 invalid twice (main + repair) → failure; the other two are voted
    client, _, tracer = client_for(tmp_path, sketch("induction"), "nope", "still nope", sketch("contradiction"))
    out = run_generator(PROBLEM, plan_obj(["x = 3"]), client, FakeRetriever(), Budget(16), tracer, samples=3)
    s1 = out["subgoals"][0]
    assert out["status"] == "ok" and [f["reason"] for f in s1["failures"]] == ["invalid_output"]
    assert s1["votes"] == {"induction": 1, "contradiction": 1} and s1["strategy"] == "induction"
    # all samples invalid → no_sketch, overall partial
    client, _, tracer = client_for(tmp_path / "b", "nope")
    out = run_generator(PROBLEM, plan_obj(["x = 3"]), client, FakeRetriever(), Budget(16), tracer, samples=2)
    assert out["status"] == "partial" and out["subgoals"][0]["status"] == "no_sketch"
    # provider error and spend stop end the node; later subgoals are not sketched
    client, _, tracer = client_for(tmp_path / "c", http_error(401))
    out = run_generator(PROBLEM, plan_obj(), client, FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "provider_error" and len(out["subgoals"]) == 1
    client, transport, tracer = client_for(tmp_path / "d", sketch("induction"), spend=SpendGuard(9.0, 0.0001, None))
    out = run_generator(PROBLEM, plan_obj(), client, FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "budget_stop" and transport.calls == []


def test_generator_never_raises(tmp_path):
    client, _, tracer = client_for(tmp_path, sketch("induction"))
    out = run_generator(PROBLEM, plan_obj(), client, FakeRetriever(RuntimeError("index")), Budget(16), tracer)
    assert out["status"] == "error" and events(tracer, "node_error")
    out = run_generator(PROBLEM, {"subgoals": "nonsense"}, client, FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "invalid_statement"
    out = run_generator(PROBLEM, None, client, FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "invalid_statement"
    # a plan that did not go through the Planner's checks: unsafe subgoal → that subgoal fails, no raise
    bad = plan_obj(["x = 3 := by sorry", "y = 2"])
    out = run_generator(PROBLEM, bad, client, FakeRetriever(), Budget(16), tracer)
    assert out["status"] == "partial" and out["subgoals"][0]["status"] == "error"


# -- run_eval.py --config PG -----------------------------------------------------


def test_run_eval_pg_preview(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    spec = importlib.util.spec_from_file_location("run_eval_pg", ROOT / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = tmp_path / "p.jsonl"
    data.write_text(json.dumps({**PROBLEM, "split": "dev"}) + "\n", encoding="utf-8")

    def answer(request):
        system = request["messages"][0]["content"]
        return GOOD_PLAN if "split a theorem" in system else sketch("algebraic_manipulation")

    clock = FakeClock()
    cfg = {**pipeline_cfg(), "planner": {"n_max": 3}, "generator": {"samples": 2}}

    class Runner(FakeRunner):
        def start(self):
            return True

        def close(self):
            pass

        def run_info(self):
            return {"backend": "fake"}

    class Retr(FakeRetriever):
        mode, warnings = "fake", []

    rc = mod.main(["--config", "PG", "--data", str(data), "--echo"], models_cfg=MODELS_CFG, pipeline_cfg=cfg,
                  client_kwargs={"transport": FakeTransport(answer), "cache_dir": tmp_path / "cache", "clock": clock,
                                 "sleep": clock.sleep, "monotonic": clock.monotonic},
                  runner_factory=Runner, retriever_factory=Retr, runs_dir=tmp_path / "runs")
    assert rc == 0
    run_dir = next((tmp_path / "runs").iterdir())
    row = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "plan_llm/sketch_ok" and row["llm"]["llm_calls"] == 1 + 2 * 2
    assert row["planner"]["plan"]["subgoals"] == ["x = 3", "y = 2"]
    assert [s["strategy"] for s in row["generator"]["subgoals"]] == ["algebraic_manipulation"] * 2
    cfg_out = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert cfg_out["status"] == "finished" and cfg_out["retriever"]["mode"] == "fake"
