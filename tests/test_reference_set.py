"""Offline tests for the reference set Q post-processing (toy corpus and sources)."""

from __future__ import annotations

from pathlib import Path

import pytest

from formal_mas.retrieval.corpus import SourceIndex
from formal_mas.retrieval.reference_set import (
    QueryItem,
    build_candidates,
    gold_sets,
    infrastructure,
    is_concrete,
    proof_text,
    select,
    split,
    summary,
)

TOY_LINES = [
    "/-- Doc with := inside. -/",                                     # 1
    "theorem Nat.foo (n : ℕ) (h : a < b) : n = n := by",              # 2
    "  -- uses bar_lemma in a comment only",                          # 3
    "  have := h.not_ge",                                             # 4
    "  simp [Nat.baz, mul_one]",                                      # 5
    "theorem Nat.one_line : True := Nat.baz",                         # 6
    "theorem Nat.eqns : ℕ → True",                                    # 7
    "  | 0 => Nat.baz",                                               # 8
    "  | _ + 1 => trivial",                                           # 9
]
MODULE = "Mathlib.Data.Nat.Toy"


def rec(name, area="number_theory", module=MODULE, sig="(n : ℕ) : n = n", origin="manual"):
    return {"name": name, "signature": sig, "area": area, "module": module, "origin": origin}


CORPUS = {r["name"]: r for r in [
    rec("Nat.foo"), rec("Nat.one_line"), rec("Nat.eqns"),
    rec("Nat.baz"), rec("mul_one", area="algebra", module="Mathlib.Algebra.Group.Defs"),
    rec("LT.lt.not_ge", area="other", module="Mathlib.Order.Basic"),
    rec("Nat.simp_only_lemma"), rec("Nat.bar_lemma"),
    rec("Mathlib.Tactic.Ring.add_pf", area="other", module="Mathlib.Tactic.Ring.Basic"),
]}


def entry(name, consts, rng, sel):
    return {"name": name, "module": MODULE, "range": rng, "sel": sel, "consts": consts}


FOO = entry("Nat.foo",
            ["Nat.baz", "mul_one", "LT.lt.not_ge", "Nat.simp_only_lemma", "Nat.bar_lemma",
             "Mathlib.Tactic.Ring.add_pf", "Eq.mpr", "Nat.foo"],
            [1, 0, 5, 27], [2, 8, 2, 15])
ONE_LINE = entry("Nat.one_line", ["Nat.baz"], [6, 0, 6, 39], [6, 8, 6, 20])
EQNS = entry("Nat.eqns", ["Nat.baz"], [7, 0, 9, 22], [7, 8, 7, 16])


@pytest.fixture
def sources(tmp_path: Path) -> SourceIndex:
    path = tmp_path / "Mathlib" / "Data" / "Nat" / "Toy.lean"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(TOY_LINES) + "\n", encoding="utf-8")
    return SourceIndex(tmp_path)


def test_proof_text_skips_docstring_and_comments(sources):
    text = proof_text(FOO, sources)
    assert text.strip().startswith("by")
    assert "Doc with" not in text and "bar_lemma" not in text
    assert "Nat.baz" in proof_text(ONE_LINE, sources)
    assert "| 0 => Nat.baz" in proof_text(EQNS, sources)


def test_proof_text_missing_source(sources):
    assert proof_text({**FOO, "module": "Mathlib.Missing"}, sources) is None
    assert proof_text({**FOO, "range": None}, sources) is None


def test_gold_methods(sources):
    gold_c, gold_b = gold_sets(FOO, CORPUS, {"mul_one"}, sources)
    # (b): corpus constants, no self, no Mathlib.Tactic, no infrastructure, no non-corpus (Eq.mpr)
    assert gold_b == {"Nat.baz", "LT.lt.not_ge", "Nat.simp_only_lemma", "Nat.bar_lemma"}
    # (c): written in the proof (dot notation counts; comments and implicit simp lemmas do not)
    assert gold_c == {"Nat.baz", "LT.lt.not_ge"}


@pytest.mark.parametrize(
    ("const", "text", "written"),
    [
        ("Finset.ext", "by ext x; simp", False),         # bare tactic
        ("Finset.ext", "Finset.ext fun x => h x", True),
        ("Eq.symm", "by symm; exact h", False),
        ("Eq.symm", "h.symm", True),
        ("by_contra", "by by_contra h; exact h rfl", False),
        ("Nat.le_of_lt", "le_of_lt h", True),            # ordinary lemma: bare name counts
    ],
)
def test_tactic_names_need_dotted_use(const, text, written):
    from formal_mas.retrieval.reference_set import is_written, written_tokens
    assert is_written(const, *written_tokens(text)) is written


def test_infrastructure_threshold():
    entries = [{"name": f"t{i}", "consts": ["mul_one"] + (["Nat.baz"] if i == 0 else [])} for i in range(10)]
    infra = infrastructure(entries, CORPUS, 50.0)
    assert infra == {"mul_one": 100.0}
    assert infrastructure([], CORPUS, 1.0) == {}


@pytest.mark.parametrize(
    ("sig", "concrete"),
    [("(n : ℕ) : n = n", True), ("(s : Finset α) : s.card = s.card", True), ("(i : Fin n) : i = i", True),
     ("(x : ℝ) : 0 ≤ x ^ 2", True), ("[Fintype α] : True", False), ("{G : Type*} [Group G] (a : G) : a = a", False)],
)
def test_is_concrete(sig, concrete):
    assert is_concrete(sig) is concrete


def test_build_candidates_filters(sources):
    corpus = {**CORPUS, "Nat.one_line": rec("Nat.one_line", origin="to_additive")}
    cands = build_candidates([FOO, ONE_LINE, EQNS], corpus, {"mul_one"}, sources)
    assert [q.name for q in cands] == ["Nat.eqns", "Nat.foo"]  # to_additive dropped
    foo = cands[1]
    assert foo.gold == ["LT.lt.not_ge", "Nat.baz"] and foo.concrete and foo.query == "(n : ℕ) : n = n"
    assert len(foo.gold_b) == 4


def item(name, area, concrete=True, gold=("x",)):
    return QueryItem(name=name, query="q", area=area, module="M", concrete=concrete,
                     gold=list(gold), gold_b=list(gold))


def toy_pool():
    pool = []
    for area in ("algebra", "number_theory", "combinatorics"):
        pool += [item(f"{area}.c{i}", area, True, (f"g.{area}.{i}",)) for i in range(3)]
        pool += [item(f"{area}.a{i}", area, False, (f"g.{area}.a{i}",)) for i in range(5)]
    return pool


def test_select_balanced_concrete_first():
    chosen = select(toy_pool(), total=12, seed=0)
    s = summary(split(chosen, seed=0))
    assert s["by_area"] == {"algebra": 4, "number_theory": 4, "combinatorics": 4}
    # 3 concrete available per area, the 4th is filled with an abstract theorem
    assert s["by_type"] == {"concrete": 9, "abstract": 3}


def test_select_avoids_query_gold_conflicts():
    pool = [item("A", "algebra", gold=("B",)), item("B", "algebra", gold=("z",)), item("C", "algebra", gold=("y",))]
    for seed in range(5):
        chosen = select(pool, total=3, seed=seed)
        names = {q.name for q in chosen}
        assert not ({"A", "B"} <= names)  # B is A's gold: they never appear together
        assert all(not names & set(q.gold) for q in chosen)


def test_select_is_deterministic():
    assert select(toy_pool(), 9, seed=7) == select(toy_pool(), 9, seed=7)


def test_split_stratified():
    items = split(select(toy_pool(), total=24, seed=1), seed=1)
    s = summary(items)
    assert s["n"] == 24
    for area in ("algebra", "number_theory", "combinatorics"):
        dev = sum(1 for q in items if q.area == area and q.split == "dev")
        assert dev == round(2 / 3 * 8)
    assert s["by_split"] == {"dev": 15, "test": 9}
    assert split(items, seed=1) == split(items, seed=1)
