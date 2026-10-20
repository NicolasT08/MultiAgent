"""I/O contracts between agents (RNF-4). Each model exports a JSON Schema via
`Model.model_json_schema()`; later phases add the Planner, Generator, ... contracts here."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LemmaHit(BaseModel):
    """One Mathlib lemma returned by the Retriever for a subgoal."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Fully qualified Lean name, ready to paste into a proof")
    signature: str = Field(description="Binders and type, without the proof")
    score: float = Field(description="Ranking score (RRF score, or BM25 score in lexical-only mode)")
