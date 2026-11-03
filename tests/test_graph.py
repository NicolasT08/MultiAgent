"""The graph with fake LLM and Lean: retries, replans, ablations, and Proposition 1 under random
behaviour of both (many seeds): it always ends, never passes b_max, respects Eq. 13."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from fake_llm import MODELS_CFG, FakeClock, FakeTransport, pipeline_cfg
from formal_mas.agents.contracts import CriticFeedback, Critique, schemas
from formal_mas.agents.prompting import render
from formal_mas.config import ABLATIONS, apply_ablation, load_pipeline, validate_pipeline
from formal_mas.lean.runner import LeanMessage, LeanResult
from formal_mas.llm.client import LLMClient
from formal_mas.orchestrator.graph import GRAPH, _recursion_limit, limits_from, run_graph
from formal_mas.orchestrator.policy import Limits, structural_bound
from formal_mas.tracing import Budget, Tracer
from test_pipeline_linear import PROBLEM, FakeLean, FakeRetriever, answer, sketch

ROOT = Path(__file__).resolve().parents[1]
BASE = {"flow": "full", "planner": {"n_max": 3}, "generator": {"samples": 3}, "budget": {"b_max": 40},
        "critic": {"k_max": 3, "r_max": 1}, "retrieval": {"enabled": True, "k_retry": 32},
        "prepass": {"enabled": True, "tactics": ["norm_num", "linarith"], "heartbeats": 1000, "timeout_s": 5}}
ONE = json.dumps({"subgoals": ["x = 3"], "closing": "exact h1"})
REFUTE = {**BASE, "planner": {"n_max": 3, "refute": {"tactics": ["linarith"], "heartbeats": 1000, "timeout_s": 5}},
          "prepass": {"enabled": False}}


def client_for(tmp_path, *script, json_repair=True, cache=False):
    clock = FakeClock()
    transport = FakeTransport(*script)
    tracer = Tracer()
    client = LLMClient("experiment", models_cfg=MODELS_CFG, pipeline_cfg=pipeline_cfg(cache=cache, json_repair=json_repair),
                       tracer=tracer, transport=transport, cache_dir=tmp_path / "cache", clock=clock, sleep=clock.sleep,
                       monotonic=clock.monotonic)
    return client, transport, tracer


def run(tmp_path, lean, *script, cfg=BASE, retriever=None, b_max=None, **kw):
    client, transport, tracer = client_for(tmp_path, *script, **kw)
    budget = Budget(b_max or cfg["budget"]["b_max"])
    out = run_graph(PROBLEM, client, lean, retriever if retriever is not None else Recorder(), budget, tracer, cfg)
    return out, budget, transport, tracer


class Recorder(FakeRetriever):
    def __init__(self):
        self.queries, self.asked = [], []

    def retrieve(self, query, k=None):
        self.queries.append((query, k))
        return super().retrieve(query, k)

    def suggest_names(self, ident, n=None):
        self.asked.append(ident)
        return ["Nat.succ_le_iff"]


class ScriptedLean(FakeLean):
    """Lemma `<name>_sg<i>`: the block `BAD:<text>` fails with Lean message <text>; the rest as FakeLean."""

    def verify(self, code, theorem=None, timeout=None):
        m = re.search(r"BAD_(\w+)", code)
        if theorem and m:
            self.files.append((theorem, code))
            text = {"tactic": "linarith failed", "name": "Unknown identifier `nat.foo`", "parse": "unexpected token 'x'; expected term",
                    "slow": "(deterministic) timeout at `whnf`, maximum number of heartbeats (10) has been reached"}[m.group(1)]
            return LeanResult("finished", "repl", [LeanMessage("error", 100, 2, text)], theorem=theorem)
        return super().verify(code, theorem, timeout)


def by_role(tactics):
    """Autoformalizer answers in order; planner and generator fixed."""
    seq = iter(tactics)

    def reply(request):
        system = request["messages"][0]["content"]
        if "split a theorem" in system:
            return ONE
        if "turn a natural-language proof sketch" in system:
            return json.dumps({"tactics": next(seq, tactics[-1])})
        return sketch()
    return reply


def roles(transport):
    out = []
    for _, req in transport.calls:
        system = req["messages"][0]["content"]
        out.append("plan" if "split a theorem" in system else "formal" if "turn a natural-language" in system
                   else "critic" if "give brief, concrete advice" in system else "sketch")
    return out


def events(tracer, name):
    return [e for e in tracer.events if e["event"] == name]


# -- retries ----------------------------------------------------------------------------------------


def test_tactic_failure_retries_through_generate_with_feedback(tmp_path):
    out, budget, transport, tracer = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic", "linarith"]))
    assert out["status"] == "solved" and out["error_classes"] == ["tactic_failure"] and out["replans"] == 0
    assert budget.used == 1 + 3 + 1 + 3 + 1
    assert roles(transport) == ["plan", "sketch", "sketch", "sketch", "formal", "sketch", "sketch", "sketch", "formal"]
    retry_prompt = transport.calls[5][1]["messages"][1]["content"]
    assert "linarith failed" in retry_prompt and "simp -- BAD_tactic" in retry_prompt
    assert "previous attempt" not in transport.calls[1][1]["messages"][1]["content"]  # first attempt: no feedback
    assert events(tracer, "subgoal_solved")[-1]["attempts"] == 2
    assert events(tracer, "critique")[0]["route"] == "GENERATE"


def test_syntax_retries_through_formalize_keeping_the_sketch(tmp_path):
    out, budget, transport, tracer = run(tmp_path, ScriptedLean(), by_role(["sorry", "simp -- BAD_parse", "linarith"]))
    assert out["status"] == "solved" and out["error_classes"] == ["syntax", "syntax"]
    assert roles(transport) == ["plan", "sketch", "sketch", "sketch", "formal", "formal", "formal"]
    assert [c["route"] for c in out["critiques"]] == ["FORMALIZE", "FORMALIZE"]


def test_missing_lemma_goes_to_retrieve_with_expanded_query_and_suggestions(tmp_path):
    retriever = Recorder()
    out, _, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_name", "linarith"]), retriever=retriever)
    assert out["status"] == "solved" and out["error_classes"] == ["missing_lemma"]
    assert retriever.asked == ["nat.foo"] and out["critiques"][0]["suggestions"] == ["Nat.succ_le_iff"]
    query, k = retriever.queries[-1]
    assert query.endswith("nat.foo") and k == 32 and retriever.queries[-2][1] is None
    assert any("Nat.succ_le_iff" in req["messages"][1]["content"] for _, req in transport.calls)
    assert roles(transport)[-4:] == ["sketch", "sketch", "sketch", "formal"]  # RETRIEVE → GENERATE → FORMALIZE


def test_retries_are_new_cache_entries(tmp_path):
    """The same failure twice must not replay the cached answer."""
    out, budget, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic", "simp -- BAD_tactic", "linarith"]),
                                    cache=True)
    assert out["status"] == "solved" and len(transport.calls) == budget.used == 1 + 3 * 4


# -- replan and abandon -------------------------------------------------------------------------------


def test_k_max_failures_replan_then_abandon(tmp_path):
    out, budget, transport, tracer = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic"]))
    assert out["status"] == "failed" and out["replans"] == 1 and out["stage"] == "verify:sg1"
    assert [c["action"] for c in out["critiques"]] == ["retry", "retry", "replan", "retry", "retry", "abandon"]
    assert budget.used == 2 * (1 + 3 * 4)
    assert roles(transport).count("plan") == 2 and len(out["plans"]) == 2
    replan_prompt = next(req for _, req in transport.calls[1:] if "split a theorem" in req["messages"][0]["content"])
    assert "x = 3" in replan_prompt["messages"][1]["content"] and "propose a different" in replan_prompt["messages"][1]["content"]


class Refuting(FakeLean):
    """The first `refuted` plans' _ref1 lemma compiles; the rest as FakeLean."""

    def __init__(self, refuted=1):
        super().__init__()
        self.refuted = refuted

    def verify(self, code, theorem=None, timeout=None):
        if theorem is None and "_ref1" in code:
            self.files.append((theorem, code))
            lines, msgs = code.split("\n"), []
            ok = self.refuted > 0
            self.refuted -= 1
            for no, line in enumerate(lines, start=1):
                if line.startswith("theorem ") and not (ok and "_ref1" in line):
                    msgs.append(LeanMessage("error", no + 2, 2, "failed"))
            return LeanResult("finished", "repl", msgs)
        return super().verify(code, theorem, timeout)


def test_refuted_plan_replans_saying_which_subgoal(tmp_path):
    out, budget, transport, tracer = run(tmp_path, Refuting(), by_role(["linarith"]), cfg=REFUTE)
    assert out["status"] == "solved" and out["replans"] == 1 and out["error_classes"] == ["planning"]
    assert out["critiques"][0]["reason"] == "refuted:sg1" and out["critiques"][0]["action"] == "replan"
    second_plan = next(req for _, req in transport.calls[1:] if "split a theorem" in req["messages"][0]["content"])
    assert "refuted:sg1" in second_plan["messages"][1]["content"] and "x = 3" in second_plan["messages"][1]["content"]


def test_refuted_without_replans_left_goes_on_with_the_trivial_plan(tmp_path):
    out, _, _, _ = run(tmp_path, Refuting(refuted=5), by_role(["linarith"]), cfg=REFUTE)
    assert out["status"] == "solved" and out["replans"] == 1
    assert out["plans"][-1]["plan"]["source"] == "trivial"  # Eq. 12: r ≥ R_max, a_i < k_max → retry
    assert out["critiques"][-1]["action"] == "retry"


def test_c4_flags_give_one_attempt_and_no_replan(tmp_path):
    cfg = {**BASE, "critic": {"k_max": 1, "r_max": 0}}
    out, budget, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic"]), cfg=cfg)
    assert out["status"] == "failed" and budget.used == 1 + 3 + 1 and out["critiques"][0]["action"] == "abandon"


def test_c3_flag_uses_no_lemmas_and_no_suggestions(tmp_path):
    retriever = Recorder()
    cfg = {**BASE, "retrieval": {"enabled": False}}
    out, _, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_name", "linarith"]), cfg=cfg, retriever=retriever)
    assert out["status"] == "solved" and retriever.queries == [] and retriever.asked == []
    assert out["critiques"][0]["suggestions"] == []
    assert all("(none retrieved)" in req["messages"][1]["content"] for _, req in transport.calls)


def test_b_max_abandons_in_the_critic(tmp_path):
    out, budget, _, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic"]), b_max=5)
    assert out["status"] == "budget_exhausted" and budget.used == 5 and out["critiques"][-1]["action"] == "abandon"


def test_assembly_failure_is_planning_and_cannot_retry(tmp_path):
    class BadAssembly(FakeLean):
        def verify(self, code, theorem=None, timeout=None):
            if theorem == "p" and "sorry" not in code:
                return LeanResult("finished", "repl", [LeanMessage("error", 9, 2, "type mismatch")], theorem=theorem)
            return super().verify(code, theorem, timeout)

    out, _, _, _ = run(tmp_path, BadAssembly(), by_role(["linarith"]), cfg={**BASE, "prepass": {"enabled": False}})
    assert out["status"] == "failed" and out["stage"] == "assemble" and out["replans"] == 1 and not out["solved"]
    assert out["error_classes"] == ["planning", "planning"]


# -- optional LLM feedback ------------------------------------------------------------------------------


def critic_reply(tactics):
    inner = by_role(tactics)

    def reply(request):
        if "give brief, concrete advice" in request["messages"][0]["content"]:
            return json.dumps({"feedback": "Try nlinarith with a square hint."})
        return inner(request)
    return reply


def test_llm_feedback_is_optional_and_never_changes_the_action(tmp_path):
    tactics = ["simp -- BAD_tactic", "simp -- BAD_tactic", "linarith"]
    cfg_on = {**BASE, "critic": {**BASE["critic"], "llm_feedback": True}}
    off, _, t_off, _ = run(tmp_path / "off", ScriptedLean(), critic_reply(tactics))
    on, budget, t_on, _ = run(tmp_path / "on", ScriptedLean(), critic_reply(tactics), cfg=cfg_on)
    assert "critic" not in roles(t_off) and roles(t_on).count("critic") == 2
    keys = ("error_class", "action", "route", "attempts", "replans")
    assert [[c[k] for k in keys] for c in off["critiques"]] == [[c[k] for k in keys] for c in on["critiques"]]
    assert budget.used == len(t_on.calls)  # it counts for b_max
    assert any("Try nlinarith" in req["messages"][1]["content"] for _, req in t_on.calls)


def test_llm_feedback_failure_falls_back_to_the_deterministic_text(tmp_path):
    def reply(request):
        if "give brief, concrete advice" in request["messages"][0]["content"]:
            return "not json"
        return by_role(["simp -- BAD_tactic", "linarith"])(request)

    cfg_on = {**BASE, "critic": {**BASE["critic"], "llm_feedback": True}}
    seq = iter(["simp -- BAD_tactic", "linarith"])

    def scripted(request):
        system = request["messages"][0]["content"]
        if "turn a natural-language" in system:
            return json.dumps({"tactics": next(seq, "linarith")})
        return reply(request) if "give brief" in system or "split a theorem" in system else sketch()

    out, _, _, tracer = run(tmp_path, ScriptedLean(), scripted, cfg=cfg_on)
    assert out["status"] == "solved" and out["critiques"][0]["llm_feedback"] is False
    assert events(tracer, "critic_llm_failed")


# -- nodes never raise --------------------------------------------------------------------------------


def test_lean_exception_becomes_a_state(tmp_path):
    out, _, _, tracer = run(tmp_path, FakeLean(raise_exc=RuntimeError("REPL died")), answer())
    assert out["status"] in ("failed", "error") and not out["solved"]
    assert events(tracer, "node_error") and events(tracer, "pipeline_end")


def test_retriever_exception_becomes_a_state(tmp_path):
    class Bad(FakeRetriever):
        def retrieve(self, query, k=None):
            raise RuntimeError("index gone")

    out, _, _, tracer = run(tmp_path, FakeLean(), by_role(["linarith"]), retriever=Bad(), cfg={**BASE, "prepass": {"enabled": False}})
    assert out["status"] in ("failed", "error") and not out["solved"]
    assert any(e["node"] in ("retrieve", "planner") for e in events(tracer, "node_error"))


def test_bad_config_and_invalid_statement_become_a_state(tmp_path):
    out = run_graph(PROBLEM, None, FakeLean(), None, Budget(1), Tracer(), {"flow": "full"})
    assert out["status"] == "error" and out["error_classes"] == []
    out = run_graph({"id": "x", "statement": "def f := 1"}, None, FakeLean(), None, Budget(1), Tracer(), BASE)
    assert out["status"] == "invalid_statement"


# -- Proposition 1: random LLM and Lean -----------------------------------------------------------------


class RandomLean:
    """Random verdicts with real-looking messages; never trusts the code it gets."""

    MESSAGES = ["linarith failed", "unsolved goals\n⊢ x = 3", "Unknown identifier `foo.bar`", "unexpected token 'x'; expected term",
                "(deterministic) timeout at `whnf`, maximum number of heartbeats (1) has been reached", "Type mismatch"]

    def __init__(self, rng, accept):
        self.rng, self.accept, self.compiles = rng, accept, 0

    def verify(self, code, theorem=None, timeout=None):
        self.compiles += 1
        r = self.rng.random()
        if r < 0.03:
            return LeanResult(self.rng.choice(["timeout", "crash", "memory_limit"]), "repl", theorem=theorem)
        if r < 0.05:
            raise RuntimeError("REPL died")
        if theorem is None:
            msgs = [LeanMessage("error", no + 2, 2, "failed") for no, line in enumerate(code.split("\n"), start=1)
                    if line.startswith("theorem ") and self.rng.random() > 0.08]
            return LeanResult("finished", "repl", msgs)
        if "sorry" in code and self.rng.random() < 0.95:
            return LeanResult("finished", "repl", [LeanMessage("warning", 8, 8, "declaration uses `sorry`")], theorem=theorem)
        if self.rng.random() < self.accept:
            return LeanResult("finished", "repl", axioms=["propext"], declarations=[theorem], theorem=theorem)
        line = self.rng.choice([1, 2, 100])
        return LeanResult("finished", "repl", [LeanMessage("error", line, 0, self.rng.choice(self.MESSAGES))], theorem=theorem)


class RandomRetriever(FakeRetriever):
    def suggest_names(self, ident, n=None):
        return ["Foo.bar"]


def random_reply(rng, garbage):
    def reply(request):
        if rng.random() < garbage:
            return rng.choice(["not json", "{}", '{"tactics": 3}'])
        system = request["messages"][0]["content"]
        if "split a theorem" in system:
            n = rng.choice([0, 1, 1, 2, 3, 3, 5])
            return json.dumps({"subgoals": [f"x = {i}" for i in range(n)], "closing": rng.choice(["linarith", "sorry", "omega"])})
        if "turn a natural-language" in system:
            return json.dumps({"tactics": rng.choice(["linarith", "simp", "norm_num", "sorry", "omega", "#eval 1", "nlinarith [sq_nonneg x]"])})
        if "give brief, concrete advice" in system:
            return json.dumps({"feedback": "advice"})
        return sketch(rng.choice(["induction", "other", "case_analysis"]))
    return reply


CONFIGS = {
    "C5": ({"k_max": 3, "r_max": 1}, 40),
    "C4": ({"k_max": 1, "r_max": 0}, 40),
    "tight": ({"k_max": 3, "r_max": 1}, 7),
    "wide": ({"k_max": 5, "r_max": 2}, 500),
}
STATUSES = {"solved", "failed", "budget_exhausted", "provider_error", "budget_stop", "invalid_statement", "error"}


def trial(tmp_path, seed, critic, b_max, *, json_repair, llm_feedback, garbage=0.1, accept=0.5):
    rng = random.Random(seed)
    cfg = {**BASE, "critic": {**critic, "llm_feedback": llm_feedback}, "budget": {"b_max": b_max}}
    client, transport, tracer = client_for(tmp_path, random_reply(rng, garbage), json_repair=json_repair)
    budget = Budget(b_max)
    out = run_graph(PROBLEM, client, RandomLean(rng, accept), RandomRetriever(), budget, tracer, cfg)
    return out, budget, transport, tracer, cfg


@pytest.mark.parametrize("name", CONFIGS)
def test_always_ends_and_respects_eq_13(tmp_path, name):
    critic, b_max = CONFIGS[name]
    seen = set()
    for seed in range(150):
        for accept in (0.0, 0.6):
            out, budget, transport, tracer, cfg = trial(tmp_path, seed, critic, b_max, json_repair=False, llm_feedback=seed % 2 == 0,
                                                        accept=accept)
            limits = limits_from(cfg)
            assert out["status"] in STATUSES, out
            assert not any("Recursion" in str(e.get("error")) for e in events(tracer, "node_error")), seed
            assert budget.used <= structural_bound(limits, n_max=3, samples=3), (seed, budget.used)
            assert len(transport.calls) <= budget.used <= b_max
            assert out["replans"] <= limits.r_max
            attempts_per = {}
            for c in out["critiques"]:
                if c["subgoal"] is not None:
                    assert c["attempts"] <= limits.k_max
                attempts_per[c["replans"]] = attempts_per.get(c["replans"], 0) + 1
            assert len(events(tracer, "pipeline_end")) == 1
            if out["solved"]:
                assert out["final"]["accepted"] and out["proof"]
            seen.add(out["status"])
    assert {"failed"} <= seen


@pytest.mark.parametrize("b_max", [1, 2, 5, 16, 40])
def test_json_repairs_never_pass_b_max(tmp_path, b_max):
    """A repair call counts for b, so Eq. 13's structural term does not cover it: B_max does."""
    for seed in range(120):
        _, budget, transport, _, _ = trial(tmp_path, seed, {"k_max": 3, "r_max": 1}, b_max, json_repair=True, llm_feedback=True,
                                           garbage=0.4, accept=0.1)
        assert budget.used <= b_max and len(transport.calls) <= b_max


def test_same_seed_gives_the_same_decisions(tmp_path):
    for seed in range(25):
        a = trial(tmp_path / "a", seed, {"k_max": 3, "r_max": 1}, 40, json_repair=True, llm_feedback=False)
        b = trial(tmp_path / "b", seed, {"k_max": 3, "r_max": 1}, 40, json_repair=True, llm_feedback=False)
        assert a[0]["critiques"] == b[0]["critiques"] and a[1].used == b[1].used and a[0]["status"] == b[0]["status"]


# -- configurations -----------------------------------------------------------------------------------


def test_every_ablation_only_sets_flags_over_pipeline_yaml():
    base = load_pipeline()
    assert (base["budget"]["b_max"], base["critic"]["k_max"], base["critic"]["r_max"], base["planner"]["n_max"],
            base["generator"]["samples"]) == (40, 3, 1, 3, 3)
    assert base["critic"]["llm_feedback"] is False
    cfgs = {name: apply_ablation(base, name) for name in ABLATIONS}
    assert cfgs["C1"]["flow"] == "baseline_a" and cfgs["C2"]["flow"] == "baseline_b"
    assert cfgs["C3"]["retrieval"]["enabled"] is False
    assert cfgs["C3"]["critic"] == {**base["critic"], "llm_feedback": True}
    assert (cfgs["C4"]["critic"]["k_max"], cfgs["C4"]["critic"]["r_max"]) == (1, 0)
    assert cfgs["C5"]["critic"] == {**base["critic"], "llm_feedback": True}
    assert {k: v for k, v in cfgs["C5"].items() if k != "critic"} == {k: v for k, v in base.items() if k != "critic"}
    assert cfgs["C4"]["critic"]["llm_feedback"] is False
    for name, cfg in cfgs.items():
        assert cfg["budget"]["b_max"] == (18 if name == "C1" else 40), name  # C1: 264/15 dev calls of C5
    assert cfgs["C1"]["baselines"]["baseline_a"]["attempts"] == 18
    assert structural_bound(limits_from(cfgs["C5"]), n_max=3, samples=3) == 40


def test_validate_pipeline_fails_on_load():
    good = load_pipeline()
    validate_pipeline(good)
    for bad in ({**good, "flow": "x"}, {**good, "critic": {"k_max": 0, "r_max": 0}}, {k: v for k, v in good.items() if k != "critic"},
                {**good, "prepass": {"enabled": True}}):
        with pytest.raises(ValueError):
            validate_pipeline(bad)


@pytest.mark.parametrize("flow, key", [("baseline_a", "attempts"), ("baseline_b", "claimed_solved")])
def test_baselines_run_on_the_same_compiled_graph(tmp_path, flow, key):
    reply = (lambda r: json.dumps({"tactics": "linarith"})) if flow == "baseline_a" else \
        (lambda r: json.dumps({"verdict": "correct", "solution": "s", "final_answer": "a"}))
    cfg = {**BASE, "flow": flow, "baselines": {flow: {"attempts": 2}}}
    client, _, tracer = client_for(tmp_path, reply)
    out = run_graph(PROBLEM, client, FakeLean(), None, Budget(40), tracer, cfg)
    assert key in out and out["status"] in ("solved", "claimed_solved", "failed"), out
    assert events(tracer, "pipeline_end")


def test_there_is_one_compiled_graph():
    from formal_mas.orchestrator import graph

    assert graph.GRAPH is GRAPH
    assert {"plan", "retrieve", "generate", "formalize", "verify", "critique", "assemble", "done", "fail", "baseline"} <= set(GRAPH.nodes)
    assert _recursion_limit(Limits(3, 1, 40), 3) > 2 * (2 + 3 * 15)


# -- contracts ------------------------------------------------------------------------------------------


def test_critic_contracts_and_schemas_in_sync():
    assert CriticFeedback(feedback="x").feedback == "x"
    with pytest.raises(ValueError):
        Critique(reason="r", error_class="other", message_class="syntax", action="retry", attempts=1, replans=0, calls=1)
    for name in ("CriticFeedback", "Critique"):
        assert json.loads((ROOT / "docs" / "schemas" / f"{name}.json").read_text(encoding="utf-8")) == schemas()[name]


def test_prompts_are_unchanged_on_a_first_attempt():
    messages = render("planner", informal="i", declaration="d", header_summary="h", lemmas="l", n_max=3, hypotheses="h1", feedback="")
    assert "\n\nPlan the proof." in messages[1]["content"]


# -- regressions from the invariant review -------------------------------------------------------------


def test_a_replan_with_the_same_plan_does_not_replay_the_cache(tmp_path):
    out, budget, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic"]), cache=True)
    # only the planner may repeat its own request if nothing changed; the feedback changes it too
    assert out["replans"] == 1 and budget.used == 2 * (1 + 3 * 4) and len(transport.calls) == budget.used
    assert budget.cached_calls == 0


def test_feedback_is_separated_from_the_next_instruction(tmp_path):
    _, _, transport, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic", "linarith"]))
    retry = transport.calls[5][1]["messages"][1]["content"]
    assert "same mistake.\n\nWrite the sketch." in retry
    out, _, transport, _ = run(tmp_path / "b", ScriptedLean(), by_role(["simp -- BAD_tactic"]))
    replan = next(r for _, r in transport.calls[1:] if "split a theorem" in r["messages"][0]["content"])
    assert "x = 3\n\nPlan the proof." in replan["messages"][1]["content"]


def test_k_max_distinct_strategies_is_a_planning_error(tmp_path):
    labels = iter(["induction"] * 3 + ["other"] * 3 + ["contradiction"] * 3)
    inner = by_role(["simp -- BAD_tactic"])

    def reply(request):
        if "split a theorem" in request["messages"][0]["content"] or "turn a natural-language" in request["messages"][0]["content"]:
            return inner(request)
        return sketch(next(labels, "other"))

    out, _, _, _ = run(tmp_path, ScriptedLean(), reply)
    assert out["error_classes"][:3] == ["tactic_failure", "tactic_failure", "planning"]
    assert out["critiques"][2]["action"] == "replan" and out["critiques"][2]["message_class"] == "tactic_failure"


def test_critic_advice_is_a_new_cache_entry_after_a_replan(tmp_path):
    cfg_on = {**BASE, "critic": {**BASE["critic"], "llm_feedback": True}}
    _, budget, transport, _ = run(tmp_path, ScriptedLean(), critic_reply(["simp -- BAD_tactic"]), cfg=cfg_on, cache=True)
    assert budget.cached_calls == 0 and len(transport.calls) == budget.used


def test_same_strategy_k_max_times_keeps_its_class_and_still_replans(tmp_path):
    out, _, _, _ = run(tmp_path, ScriptedLean(), by_role(["simp -- BAD_tactic"]))
    assert out["error_classes"][:3] == ["tactic_failure"] * 3 and out["critiques"][2]["action"] == "replan"


def test_critique_logs_the_b_that_pi_saw(tmp_path):
    cfg_on = {**BASE, "critic": {**BASE["critic"], "llm_feedback": True}}
    out, _, _, _ = run(tmp_path, ScriptedLean(), critic_reply(["simp -- BAD_tactic", "linarith"]), cfg=cfg_on)
    assert out["critiques"][0]["calls"] == 1 + 3 + 1 and out["critiques"][0]["llm_feedback"] is True
