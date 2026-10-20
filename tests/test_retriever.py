"""Offline tests for the Retriever (toy corpus; no Mathlib, no model downloads, no network)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pytest

from formal_mas.agents.contracts import LemmaHit
from formal_mas.retrieval.bm25 import BM25, tokenize
from formal_mas.retrieval.retriever import (EXTRA_NAMES_FILE, NameIndex, Retriever, build_extra_names,
                                            generalize_query, goal_to_query)

ROOT = Path(__file__).resolve().parents[1]
TOY = [
    ("Nat.Prime.one_lt", "{p : ℕ} (hp : Nat.Prime p) : 1 < p"),
    ("Nat.Prime.two_le", "{p : ℕ} (hp : Nat.Prime p) : 2 ≤ p"),
    ("Nat.add_comm", "(n m : ℕ) : n + m = m + n"),
    ("Finset.card_range", "(n : ℕ) : (Finset.range n).card = n"),
    ("Finset.sum_range_succ", "{M : Type u_1} [AddCommMonoid M] (f : ℕ → M) (n : ℕ) : "
                              "∑ x ∈ Finset.range (n + 1), f x = ∑ x ∈ Finset.range n, f x + f n"),
    ("Int.emod_emod_of_dvd", "(n : ℤ) {m k : ℤ} (h : m ∣ k) : n % k % m = n % m"),
    ("sq_nonneg", "{R : Type u} [Semiring R] [LinearOrder R] [ExistsAddOfLE R] (a : R) : 0 ≤ a ^ 2"),
    ("Real.rpow_nonneg", "{x : ℝ} (hx : 0 ≤ x) (y : ℝ) : 0 ≤ x ^ y"),
]
PIPELINE = {"retrieval": {"method": "rrf", "k": 3, "depth": 5, "rrf_k": 60, "query_instruction": True,
                          "suggestions": 3}}
MODELS = {"embeddings": {"selected": "toy", "candidates": {"toy": {
    "hf_id": "toy/encoder", "backend": "sentence_transformers", "max_length": 64,
    "query_instruction": "query: ", "doc_prefix": "", "doc_template": "{text}"}}}}


def write_corpus(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "corpus.jsonl").open("w", encoding="utf-8") as f:
        for name, sig in TOY:
            f.write(json.dumps({"name": name, "signature": sig, "text": f"{name} ∥ {sig}"}, ensure_ascii=False) + "\n")
    return root / "full"


class ToySpec:
    query_instruction = "query: "

    def query_text(self, query: str, instruction: bool) -> str:
        return (self.query_instruction if instruction else "") + query


class ToyEncoder:
    """Bag-of-tokens hashing encoder: deterministic, no downloads."""

    dim = 64
    spec = ToySpec()

    def __init__(self, fail: bool = False):
        self.fail = fail

    def encode(self, texts):
        if self.fail:
            raise RuntimeError("encoder broke")
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                out[i, hash(tok) % self.dim] += 1
        return out / np.linalg.norm(out, axis=1, keepdims=True).clip(min=1e-12)


def write_dense(index_dir: Path, key: str = "toy", hf_id: str = "toy/encoder") -> None:
    faiss = pytest.importorskip("faiss")
    folder = index_dir / key
    folder.mkdir(parents=True, exist_ok=True)
    vecs = ToyEncoder().encode([f"{n} ∥ {s}" for n, s in TOY])
    index = faiss.IndexFlatIP(ToyEncoder.dim)
    index.add(vecs)
    faiss.write_index(index, str(folder / "index.faiss"))
    (folder / "names.json").write_text(json.dumps([n for n, _ in TOY]), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"embedding_key": key, "hf_id": hf_id}), encoding="utf-8")


# -- query construction -------------------------------------------------------


def test_goal_state_becomes_q_format():
    goal = "case h\nn m : ℕ\ninst✝ : Fact (Nat.Prime n)\nh : m ∣\n  n\n⊢ m = 1 ∨\n    m = n\n\ncase h2\n⊢ False"
    assert goal_to_query(goal) == "(n m : ℕ) [Fact (Nat.Prime n)] (h : m ∣ n) : m = 1 ∨ m = n"


def test_goal_without_hypotheses_and_let_values():
    assert goal_to_query("⊢ 0 ≤ 2 ^ 2") == "0 ≤ 2 ^ 2"
    assert goal_to_query("x : ℕ := 3\n⊢ x = 3") == "(x : ℕ) : x = 3"


def test_statement_is_stripped_to_binders_and_type():
    assert goal_to_query("theorem foo (n : ℕ) (h : 0 < n) :\n  n ≠ 0 := by omega") == "(n : ℕ) (h : 0 < n) : n ≠ 0"
    assert goal_to_query("example (a : ℤ) : a = a := rfl") == "(a : ℤ) : a = a"
    assert goal_to_query("(n : ℕ) : n + 0 = n") == "(n : ℕ) : n + 0 = n"


def test_generalize_query():
    assert generalize_query("(x : ℝ) : 0 ≤ x ^ 2") == "(x : R) : 0 ≤ x ^ 2"
    # ℕ stays as index/exponent when another concrete type is present
    assert generalize_query("(a b : ℤ) (n : ℕ) : a - b ∣ a ^ n - b ^ n") == "(a b : R) (n : ℕ) : a - b ∣ a ^ n - b ^ n"
    assert generalize_query("(f : ℕ → ℝ) : ∑ i ∈ Finset.range 3, f i = 0") == "(f : ℕ → R) : ∑ i ∈ Finset.range 3, f i = 0"
    assert generalize_query("(n : ℕ) (h : 2 ∣ n) : n % 2 = 0") == "(n : R) (h : 2 ∣ n) : n % 2 = 0"
    assert generalize_query("(R : ℝ) : 0 ≤ R ^ 2") == "(R : α) : 0 ≤ R ^ 2"                    # `R` taken
    assert generalize_query("{x : ℝ≥0} {m : ℕ+} : x ≤ x") is None                              # only ℝ≥0 / ℕ+
    assert generalize_query("{R : Type u} [Ring R] (x : R) (n : ℕ) : x ^ n = x ^ n") is None   # already generic
    assert generalize_query("(p : Prop) : p → p") is None


# -- names --------------------------------------------------------------------


def test_name_suggestions():
    idx = NameIndex([n for n, _ in TOY] + ["Nat.succ_le_iff", "Finset.Nat.succ_le_iff"])
    assert idx.suggest("nat.succ_le_iff")[0] == "Nat.succ_le_iff"          # Lean 3 casing
    assert idx.suggest("succ_le_iff")[0] == "Nat.succ_le_iff"             # missing namespace
    assert idx.suggest("Nat.prime.two_le") == ["Nat.Prime.two_le"]
    assert "Finset.sum_range_succ" in idx.suggest("finset.sum_range_suc")  # typo
    assert idx.suggest("") == [] and idx.suggest("zzz_nothing") == []


# -- BM25 persistence ---------------------------------------------------------


def test_bm25_save_load_roundtrip(tmp_path):
    texts = [f"{n} ∥ {s}" for n, s in TOY]
    bm = BM25(texts)
    bm.save(tmp_path / "bm")
    loaded = BM25.load(tmp_path / "bm")
    q = "(p : ℕ) (hp : Nat.Prime p) : 2 ≤ p"
    assert np.allclose(bm.scores(q), loaded.scores(q))
    assert loaded.top(q, 1)[0][0] == 1


# -- Retriever ----------------------------------------------------------------


def test_bm25_only_when_dense_index_missing(tmp_path, caplog):
    index_dir = write_corpus(tmp_path / "index")
    with caplog.at_level(logging.WARNING):
        r = Retriever(index_dir, PIPELINE, MODELS)
    assert r.mode == "bm25"
    assert (index_dir / "bm25" / "lemmas.json").exists()  # built once, then persisted
    assert any(w["reason"] == "dense_index_missing" for w in r.warnings)
    assert '"event": "retriever_warning"' in caplog.text
    hits = r.retrieve("p : ℕ\nhp : Nat.Prime p\n⊢ 2 ≤ p")
    assert len(hits) == 3 and all(isinstance(h, LemmaHit) for h in hits)
    assert hits[0].name == "Nat.Prime.two_le" and hits[0].signature == dict(TOY)["Nat.Prime.two_le"]


def test_persisted_bm25_is_reused(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    Retriever(index_dir, PIPELINE, MODELS)
    (tmp_path / "index" / "corpus.jsonl").unlink()  # the corpus is no longer needed
    r = Retriever(index_dir, PIPELINE, MODELS)
    assert r.mode == "bm25" and r.retrieve("⊢ (Finset.range n).card = n")[0].name == "Finset.card_range"


def test_nothing_available_returns_empty(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        r = Retriever(tmp_path / "missing", PIPELINE, MODELS)
    assert r.mode == "none"
    assert r.retrieve("⊢ 1 + 1 = 2") == [] and r.suggest_names("nat.foo") == []
    assert r.warnings[0]["reason"] == "lexical_index_missing"
    assert "retriever_warning" in caplog.text


def test_broken_config_never_raises(tmp_path):
    r = Retriever(tmp_path, pipeline_cfg={"retrieval": {"k": "not-a-number"}}, models_cfg=MODELS)
    assert r.retrieve("⊢ True") == [] and r.warnings[0]["reason"] == "init_failed"


def test_hybrid_rrf(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    write_dense(index_dir)
    r = Retriever(index_dir, PIPELINE, MODELS, encoder=ToyEncoder())
    assert r.mode == "hybrid" and r.warnings == []
    hits = r.retrieve("theorem t (p : ℕ) (hp : Nat.Prime p) : 2 ≤ p := sorry")
    assert hits[0].name == "Nat.Prime.two_le"
    assert hits[0].score == pytest.approx(2 / 61)  # first in both rankings
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def rank_of(r: Retriever, query: str, name: str) -> int:
    return [h.name for h in r.retrieve(query, k=len(TOY))].index(name) + 1


def generic_cfg(on: bool) -> dict:
    return {"retrieval": {**PIPELINE["retrieval"], "depth": len(TOY), "generic_query": on}}


def test_generic_query_lifts_generic_lemma_bm25(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    r_off = Retriever(index_dir, generic_cfg(False), MODELS)
    r_on = Retriever(index_dir, generic_cfg(True), MODELS)
    assert r_on.mode == "bm25"
    goal = "x : ℝ\n⊢ 0 ≤ x ^ 2"
    assert rank_of(r_off, goal, "Real.rpow_nonneg") < rank_of(r_off, goal, "sq_nonneg")
    # RRF over BM25(query) and BM25(generalised query): sq_nonneg is 2nd then 1st, Real.* 1st then 2nd
    scores = {h.name: h.score for h in r_on.retrieve(goal, k=len(TOY))}
    assert scores["sq_nonneg"] == pytest.approx(1 / 61 + 1 / 62) == pytest.approx(scores["Real.rpow_nonneg"])


def test_generic_query_fuses_four_rankings_in_hybrid_mode(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    write_dense(index_dir)
    r_on = Retriever(index_dir, generic_cfg(True), MODELS, encoder=ToyEncoder())
    r_off = Retriever(index_dir, generic_cfg(False), MODELS, encoder=ToyEncoder())
    assert r_on.mode == "hybrid"
    assert r_on.retrieve("x : ℝ\n⊢ 0 ≤ x ^ 2")[0].score > 2 / 61  # two rankings would cap at 2/61
    generic = "{R : Type u} [Semiring R] (a : R) : 0 ≤ a ^ 2"     # nothing to generalise: same result
    assert r_on.retrieve(generic) == r_off.retrieve(generic)


def test_dense_model_mismatch_falls_back_to_bm25(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    write_dense(index_dir, hf_id="other/model")
    r = Retriever(index_dir, PIPELINE, MODELS, encoder=ToyEncoder())
    assert r.mode == "bm25" and r.warnings[0]["reason"] == "dense_index_model_mismatch"


def test_dense_failure_at_query_time_falls_back_to_bm25(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    write_dense(index_dir)
    r = Retriever(index_dir, PIPELINE, MODELS, encoder=ToyEncoder(fail=True))
    hits = r.retrieve("⊢ (Finset.range n).card = n")
    assert hits and hits[0].name == "Finset.card_range"
    assert r.warnings[-1]["reason"] == "retrieve_failed"


def test_suggest_names_through_retriever(tmp_path):
    r = Retriever(write_corpus(tmp_path / "index"), PIPELINE, MODELS)
    assert r.suggest_names("int.emod_emod_of_dvd") == ["Int.emod_emod_of_dvd"]


def write_extra_names(index_dir: Path, names: list[str]) -> None:
    raw = index_dir.parent / "names_raw.jsonl"
    raw.write_text("".join(json.dumps({"name": n, "module": "Init.Toy"}) + "\n" for n in names), encoding="utf-8")
    build_extra_names(raw, index_dir / EXTRA_NAMES_FILE)


def test_build_extra_names_drops_autogenerated(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text("".join(json.dumps({"name": n, "module": "Init.X"}) + "\n"
                           for n in ["Nat.succ_le_iff", "Nat.foo.eq_1", "Nat.bar.congr_simp", "Nat.succ_le_iff"]),
                   encoding="utf-8")
    assert build_extra_names(raw, tmp_path / "out.json") == 1
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["names"] == ["Nat.succ_le_iff"]


def test_core_names_feed_suggestions_but_not_search(tmp_path):
    index_dir = write_corpus(tmp_path / "index")
    write_extra_names(index_dir, ["Nat.succ_le_iff", "Nat.lt_irrefl"])
    r = Retriever(index_dir, PIPELINE, MODELS)
    assert r.suggest_names("nat.succ_le_iff")[0] == "Nat.succ_le_iff"   # Lean 3 casing, core name
    assert r.suggest_names("lt_irrefl")[0] == "Nat.lt_irrefl"
    hits = r.retrieve("m n : ℕ\n⊢ m.succ ≤ n ↔ m < n", k=10)
    assert "Nat.succ_le_iff" not in {h.name for h in hits}                 # never in the search index
    assert set(r.names) == {n for n, _ in TOY}


def test_missing_extra_names_only_warns(tmp_path):
    r = Retriever(write_corpus(tmp_path / "index"), PIPELINE, MODELS)
    assert r.suggest_names("nat.prime.one_lt") == ["Nat.Prime.one_lt"]
    assert any(w["reason"] == "extra_names_missing" for w in r.warnings)


@pytest.mark.skipif(not (ROOT / "data/index/full" / EXTRA_NAMES_FILE).exists(), reason="needs build_index.py --extra-names")
def test_real_core_names_fix_lean3_casing():
    extra = json.loads((ROOT / "data/index/full" / EXTRA_NAMES_FILE).read_text(encoding="utf-8"))["names"]
    assert not any(n.startswith("Lean.") for n in extra)
    assert NameIndex(extra).suggest("nat.succ_le_iff")[0] == "Nat.succ_le_iff"


def test_lemma_hit_schema():
    schema = LemmaHit.model_json_schema()
    assert set(schema["required"]) == {"name", "signature", "score"}
