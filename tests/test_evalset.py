"""Curation logic and the invariants of the fixed data/eval.jsonl (Phase 7)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from formal_mas import evalset as ev
from formal_mas.lean.statement import parse_theorem

ROOT = Path(__file__).resolve().parents[1]
CRITERIA = yaml.safe_load((ROOT / "data" / "curation" / "criteria.yaml").read_text(encoding="utf-8"))
EVAL = ROOT / "data" / "eval.jsonl"


def rows() -> list[dict]:
    return [json.loads(line) for line in EVAL.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("before, after", [
    ("(∑ k in Finset.range 3, k) = 3", "(∑ k ∈ Finset.range 3, k) = 3"),
    ("∏ i in s, f i = ∑ j in t, g j", "∏ i ∈ s, f i = ∑ j ∈ t, g j"),
    ("x ∈ s ∧ ∑ k ∈ s, k = 0", "x ∈ s ∧ ∑ k ∈ s, k = 0"),
])
def test_conversion_changes_only_the_binder(before, after):
    assert ev.convert_big_operators(before) == after


def test_conversion_ignores_in_outside_a_big_operator():
    text = "theorem in_name (h : a in b) (x : ℕ) : x = x := by\n"
    assert ev.convert_big_operators(text) == text


def test_conversion_stops_at_the_binder_comma():
    assert ev.convert_big_operators("∑ k in s, (a in b)") == "∑ k ∈ s, (a in b)"


def test_difficulty_rule():
    assert ev.difficulty_of("mathd_algebra_1") == "easy"
    for name in ("amc12a_9999_p1", "aimeI_9999_p1", "algebra_x", "numbertheory_x", "induction_x"):
        assert ev.difficulty_of(name) == "medium"
    for name in ("imo_9999_p1", "imosl_9999_algebra_p1", "putnam_9999_a1"):
        assert ev.difficulty_of(name) == "hard"
    with pytest.raises(ValueError):
        ev.difficulty_of("unknown_1")


def test_area_rule():
    labels = {"amc12a_9999_p1": "combinatorics", "aime_9999_p2": "out_of_scope"}
    assert ev.area_of("mathd_numbertheory_9999", labels) == "number_theory"
    assert ev.area_of("amc12a_9999_p1", labels) == "combinatorics"
    assert ev.area_of("aime_9999_p2", labels) is None
    with pytest.raises(ValueError):
        ev.area_of("amc12a_1", labels)


def test_every_manual_discard_needs_a_reason():
    ev.check_discards({"a": "misformalized"})
    for bad in ({"a": ""}, {"a": None}):
        with pytest.raises(ValueError):
            ev.check_discards(bad)


def test_a_label_is_unique():
    with pytest.raises(ValueError):
        ev.flatten_labels({"algebra": "a b", "number_theory": "b"})


def test_seeded_order_is_stable_and_seed_dependent():
    ids = [f"p{i}" for i in range(30)]
    assert ev.seeded_order(ids, 2026) == ev.seeded_order(list(reversed(ids)), 2026)
    assert ev.seeded_order(ids, 2026) != ev.seeded_order(ids, 2027)


def test_dev_counts_add_up_and_cover_reserved():
    minimum = {("medium", "algebra"): 2, ("medium", "number_theory"): 2, ("medium", "combinatorics"): 1}
    counts = ev.dev_counts(CRITERIA["grid"], CRITERIA["total_dev"], CRITERIA["seed"], minimum)
    assert sum(counts.values()) == CRITERIA["total_dev"]
    assert all(counts[cell] >= n for cell, n in minimum.items())


def test_assign_split_forces_reserved_to_dev():
    splits = ev.assign_split(["a", "b", "c", "d"], 2, {"c"}, 7)
    assert splits["c"] == "dev" and Counter(splits.values()) == {"dev": 2, "test": 2}
    with pytest.raises(ValueError):
        ev.assign_split(["a", "b"], 1, {"a", "b"}, 7)


PUTNAM = "import Mathlib\n\nopen Foo\n\n/--\ntext\n-/\ntheorem putnam_x\n(a : ℕ)\n: a = a :=\nsorry\n"


def test_putnam_record_makes_the_placeholder_by():
    rec = ev.putnam_record("putnam_x", ["combinatorics"], "text", PUTNAM)
    assert rec["formal_statement"].endswith(":= by\n") and "sorry" not in rec["formal_statement"]
    assert rec["header"] == "import Mathlib\n\nopen Foo\n\n"
    assert parse_theorem(rec["header"] + rec["formal_statement"], header=rec["header"]).goal == "a = a"


@pytest.mark.parametrize("extra", ["abbrev putnam_x_solution : ℕ := sorry\n", "def f : ℕ := 1\n"])
def test_putnam_factored_answers_and_definitions_are_not_eligible(extra):
    assert ev.putnam_record("putnam_x", ["combinatorics"], "t", PUTNAM.replace("/--", extra + "/--")) is None


def test_putnam_tags():
    assert ev.putnam_eligible({"tags": ["combinatorics", "algebra"]})
    assert not ev.putnam_eligible({"tags": ["geometry", "combinatorics"]})
    assert not ev.putnam_eligible({"tags": ["algebra"]})


@pytest.mark.skipif(not EVAL.exists(), reason="data/eval.jsonl not built yet")
class TestFixedSet:
    def test_grid_and_split_match_the_criteria(self):
        data = rows()
        cells = Counter((r["difficulty"], r["area"]) for r in data)
        expected = {(d, a): n for d, row in CRITERIA["grid"].items() for a, n in row.items() if n}
        assert cells == expected and sum(cells.values()) == 45
        assert Counter(r["split"] for r in data) == {"dev": 15, "test": 30}
        dev = Counter((r["difficulty"], r["area"]) for r in data if r["split"] == "dev")
        minimum = {("medium", "algebra"): 2, ("medium", "number_theory"): 2, ("medium", "combinatorics"): 1}
        assert dev == ev.dev_counts(CRITERIA["grid"], CRITERIA["total_dev"], CRITERIA["seed"], minimum)

    def test_ids_are_unique_and_reserved_are_dev(self):
        data = {r["id"]: r for r in rows()}
        assert len(data) == len(rows())
        for problem_id in CRITERIA["reserved_dev"]:
            assert data[problem_id]["split"] == "dev"

    def test_reserved_keep_their_preview_statement(self):
        data = {r["id"]: r for r in rows()}
        preview = (ROOT / "data" / "dev_preview.jsonl").read_text(encoding="utf-8").splitlines()
        for line in preview:
            old = json.loads(line)
            assert data[old["id"]]["statement"] == old["statement"]
            assert (data[old["id"]]["difficulty"], data[old["id"]]["area"]) == (old["difficulty"], old["area"])

    def test_records_have_provenance_and_parse(self):
        for r in rows():
            for key in ("source", "source_id", "source_commit", "license", "informal", "formal_statement"):
                assert r[key], f"{r['id']}: {key}"
            assert r["formal_statement"].rstrip().endswith(":= by")
            assert parse_theorem(r["statement"], header=r["header"]).name == r["id"]

    def test_only_the_binder_differs_from_the_original(self):
        for r in rows():
            if "formal_statement_original" in r:
                assert r["converted"] == ev.CONVERSION_MARK
                assert ev.convert_big_operators(r["formal_statement_original"]) == r["formal_statement"]
            else:
                assert ev.convert_big_operators(r["formal_statement"]) == r["formal_statement"]

    def test_manual_discards_are_not_in_the_set(self):
        ids = {r["id"] for r in rows()}
        assert not ids & set(CRITERIA["discards"] or {})
