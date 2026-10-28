"""I/O contracts between agents (RNF-4). Each model exports a JSON Schema via
`Model.model_json_schema()`; later phases add the Planner, Generator, ... contracts here."""

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
# JSON Schemas (RNF-4): docs/schemas/<Model>.json, kept in sync by tests/test_baselines.py.

SCHEMA_MODELS = (LemmaHit, TacticProof, NLSolution, SelfAssessment)


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
