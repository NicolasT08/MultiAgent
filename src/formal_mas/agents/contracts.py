"""I/O contracts between agents (RNF-4). Each model exports a JSON Schema via
`Model.model_json_schema()`; later phases add the Autoformalizer and Critic contracts here."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LemmaHit(BaseModel):
    """One Mathlib lemma returned by the Retriever for a subgoal."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Fully qualified Lean name, ready to paste into a proof")
    signature: str = Field(description="Binders and type, without the proof")
    score: float = Field(description="Ranking score (RRF score, or BM25 score in lexical-only mode)")


# ---------------------------------------------------------------------------
# LLM outputs of the baselines (Phase 3). Parsed by llm/client.py `complete_json`.


class TacticProof(BaseModel):
    """Baseline A (C1) and, later, the Autoformalizer: only the tactic block after `:= by`."""

    tactics: str = Field(description="Lean 4 tactic block, one tactic per line; never a statement")


class NLSolution(BaseModel):
    """Baseline B (C2): a natural-language proof, never verified."""

    solution: str = Field(description="Complete step-by-step proof in natural language")
    final_answer: str = Field(default="", description="Final answer or claim in one line")


class SelfAssessment(BaseModel):
    """Baseline B (C2): the model's verdict on its own solution (its claim of success)."""

    verdict: Literal["correct", "incorrect"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# Planner (Phase 4). The LLM proposes `PlanProposal`; the code checks every piece with
# lean/sanitize.py, builds the skeleton with statement.skeleton and compiles it (sorry
# allowed). The node's output is `Plan` (source "llm", "direct", or "trivial" with the reason).


class PlanProposal(BaseModel):
    """LLM output of the Planner: subgoal propositions and the closing tactic block."""

    subgoals: list[str] = Field(description="Lean 4 propositions over the theorem's variables, one line each, "
                                            "proved in order; subgoal i becomes hypothesis h<i>")
    closing: str = Field(description="Tactic block that proves the theorem's goal from the subgoal hypotheses")
    rationale: str = Field(default="", description="Why these subgoals, in one or two sentences")


class Plan(BaseModel):
    """Planner node output. The trivial plan is the whole goal as the only subgoal; a direct
    plan has no subgoals and its closing block alone is an accepted proof of the theorem."""

    subgoals: list[str]
    closing: str
    hypotheses: list[str] = Field(description="Names the subgoals get in the skeleton (h1, h2, …)")
    rationale: str = ""
    source: Literal["llm", "trivial", "direct"]
    closing_source: str = Field(default="llm", description="`llm`, or `auto:<tactic>` when the model's closing "
                                                           "failed and a tactic of planner.closing_auto closed the goal")
    reason: str | None = Field(default=None, description="Why the trivial plan was used (e.g. `refuted:sg2`), or "
                                                         "`closing_auto:<tactic>` for a repaired closing")
    skeleton_compiles: bool | None = Field(default=None, description="Skeleton compiles (sorry allowed) and the "
                                              "closing lemma is accepted (direct plan: the proof is accepted); "
                                              "None if not compiled")


# ---------------------------------------------------------------------------
# Generator (Phase 4): N natural-language sketches per subgoal, each labelled with one
# strategy of the fixed list STRATEGIES; the vote (Eq. 9) is over these labels.

STRATEGIES = ("induction", "case_analysis", "algebraic_manipulation", "classical_inequality",
              "modular_arithmetic", "contradiction", "direct_computation", "other")
Strategy = Literal["induction", "case_analysis", "algebraic_manipulation", "classical_inequality",
                   "modular_arithmetic", "contradiction", "direct_computation", "other"]


class SketchProposal(BaseModel):
    """LLM output of the Generator: one proof sketch of one subgoal."""

    strategy: Strategy = Field(description="Main strategy, one label of the fixed list")
    sketch: str = Field(description="Numbered natural-language proof steps of the subgoal")
    lemmas: list[str] = Field(default_factory=list, description="Names of the listed Mathlib lemmas the sketch uses")

    @field_validator("strategy", mode="before")
    @classmethod
    def _normalise(cls, v):
        return "_".join(v.strip().lower().replace("-", " ").split()) if isinstance(v, str) else v


class Sketch(SketchProposal):
    sample: int = Field(description="Self-consistency sample index (0-based)")


class SubgoalSketches(BaseModel):
    """Generator output for one subgoal: the samples, the vote and the chosen sketch."""

    index: int = Field(description="1-based subgoal index")
    subgoal: str
    lemmas: list[str] = Field(default_factory=list, description="Retrieved lemma names given to the model")
    samples: list[Sketch] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list, description="Samples without a valid sketch, with the reason")
    votes: dict[str, int] = Field(default_factory=dict)
    strategy: Strategy | None = Field(default=None, description="Winning strategy (Eq. 9)")
    representative: Sketch | None = Field(default=None, description="Lowest-index sample of the winning strategy")
    status: Literal["ok", "no_sketch", "budget_exhausted", "budget_stop", "provider_error", "error"] = "ok"


# ---------------------------------------------------------------------------
# JSON Schemas (RNF-4): docs/schemas/<Model>.json, kept in sync by tests/test_baselines.py.

SCHEMA_MODELS = (LemmaHit, TacticProof, NLSolution, SelfAssessment, PlanProposal, Plan, SketchProposal, Sketch,
                 SubgoalSketches)


def schemas() -> dict[str, dict]:
    return {m.__name__: m.model_json_schema() for m in SCHEMA_MODELS}


def export_schemas(out_dir) -> list:
    """Write every contract's JSON Schema: `python -m formal_mas.agents.contracts`."""
    import json
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in schemas().items():
        path = out / f"{name}.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    from formal_mas.config import ROOT

    for p in export_schemas(ROOT / "docs" / "schemas"):
        print(p)
