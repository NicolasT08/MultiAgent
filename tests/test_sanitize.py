"""lean/sanitize.py: attack examples must be rejected (they are only scanned as text here,
never compiled) and ordinary Lean 4 / Mathlib proofs must pass."""

from __future__ import annotations

import pytest

from formal_mas.lean.sanitize import check_tactics, check_term, strip_comments

# Real-looking Lean 4 proofs (several taken from the fixtures and the REPL probes).
VALID_BLOCKS = [
    "nlinarith [sq_nonneg (a - b), sq_nonneg (a + b)]",
    "subst h2\nomega",
    "constructor\n{ simp }\n{ omega }",
    "constructor\n· simp\n· omega",
    "rcases h with ⟨x, hx⟩\nexact ⟨x, by linarith⟩",
    "rcases h with h | h <;> simp [h]",
    "obtain ⟨k, rfl⟩ := hk\nring",
    "cases' h with x hx\nexact hx",
    "induction n with\n| zero => simp\n| succ k ih =>\n  rw [Finset.sum_range_succ, ih]\n  ring",
    "cases h with\n| inl h => exact Or.inr h\n| inr h => exact Or.inl h",
    "match n with\n| 0 => left; rfl\n| k + 1 => right; omega",
    "intro x hx\nhave h₁ : 0 < x := by positivity\nfield_simp\nnlinarith [h₁]",
    "open Real in\n  exact sqrt_nonneg _",
    "set_option maxHeartbeats 400000 in\nnlinarith [sq_nonneg (x - 1)]",
    "simp only [Finset.card_range]  -- `#s` style card notation below\nexact (rfl : #(Finset.range 3) = 3)",
    "/- nested /- comment -/ still a comment -/\nexact h",
    "calc a = b := by rw [h]\n  _ = c := by simp",
    "interval_cases n <;> norm_num",
    "exact fun ⟨a, b⟩ => ⟨b, a⟩",
    "refine ⟨1, ?_⟩\nnorm_num",
    "decide",
    "norm_num [Nat.Prime] at h ⊢",
    "  exact h  -- the whole block may be indented",
]


@pytest.mark.parametrize("block", VALID_BLOCKS)
def test_valid_lean4_passes(block):
    res = check_tactics(block)
    assert res.ok, (res.reason, res.detail)


# (attack, accepted reasons). Each one would, if compiled, escape the proof, forge the
# runner's checks, skip the kernel, add axioms or run code on the machine.
ATTACKS = [
    # leave the proof and declare something else
    ("exact h\ntheorem evil : False := sorry", {"escape"}),
    ("exact h theorem evil : 1 = 2 := sorry", {"escape"}),
    ("  simp\nexact h", {"escape"}),
    ("simp\n\nnamespace t\ntheorem sneaky : 1 = 1 := rfl\nend t", {"escape"}),
    ("exact helper\nwhere helper := trivial", {"escape"}),
    ("exact h\nend", {"escape"}),
    ("exact h\n@[simp] theorem bad : 1 = 1 := rfl", {"escape"}),
    ("exact h\ninstance : Inhabited Empty := sorry", {"escape"}),
    ("exact h\nlocal attribute [instance] foo", {"escape"}),
    ("exact h\nopen Lean Elab Command", {"escape", "metaprogramming"}),
    ("exact h\n#print axioms t", {"escape"}),
    ("exact h\n#exit", {"escape"}),
    ("exact h\n#guard_msgs (drop info) in", {"escape"}),
    # metaprogramming: forge `#print axioms` / the declarations listing, skip the kernel
    ("exact h\nmacro_rules | `(command| #print axioms $x) => `(command| example : True := trivial)", {"metaprogramming", "escape"}),
    ("exact h\nsyntax (name := fake) \"#print axioms\" ident : command", {"string", "metaprogramming"}),
    ("exact h\nelab_rules : command | `(fake) => pure ()", {"metaprogramming"}),
    ("run_tac Lean.Elab.Tactic.closeMainGoal (Lean.mkConst ``trivial)", {"metaprogramming"}),
    ("exact h\nrun_cmd Lean.logInfo m!\"'t' depends on axioms: [propext]\"", {"string", "metaprogramming"}),
    ("exact h\nrun_cmd Lean.modifyEnv id", {"metaprogramming"}),
    ("by_elab Lean.Meta.mkSorry (← Lean.Elab.Term.getMainGoal) true", {"metaprogramming", "sorry"}),
    ("exact (Lean.ofReduceBool _ _ rfl)", {"native", "metaprogramming"}),
    ("set_option debug.skipKernelTC true in\nexact h", {"option", "metaprogramming"}),
    ("set_option maxHeartbeats 0\nnlinarith", {"option"}),
    ("set_option pp.all true in\nexact h", {"option"}),
    ("open Lean in\nexact h", {"metaprogramming"}),
    ("exact h\naxiom cheat : False", {"metaprogramming"}),
    ("exact (unsafe (cast lcProof ()))", {"metaprogramming"}),
    ("exact h\n@[implemented_by foo] def bar := 0", {"escape", "metaprogramming"}),
    # run code on the machine
    ("exact h\n#eval IO.Process.exit 0", {"escape", "io"}),
    ("exact (unsafeBaseIO (IO.Process.spawn {cmd := \"calc.exe\"}) |> fun _ => h)", {"string", "io"}),
    ("exact (unsafeBaseIO (pure ()) |> fun _ => h)", {"io"}),
    ("have := (IO.FS.removeFile)\nexact h", {"io"}),
    ("have := System.FilePath.mk\nexact h", {"io"}),
    # hide tokens from the scanner
    ("exact \"--\" run_cmd Lean.logInfo", {"string"}),
    ("exact «--» run_cmd foo", {"string"}),
    ("exact h /- comment that never ends", {"comment"}),
    ("exact h\n\trun_cmd foo", {"tab"}),
    ("exact h -- fine\n  run_cmd foo", {"metaprogramming"}),
    # sorry / native / Lean 3
    ("sorry", {"sorry"}),
    ("admit", {"sorry"}),
    ("native_decide", {"native"}),
    ("decide +native", {"native"}),
    ("begin\n  simp,\nend", {"lean3"}),
    ("assume x,\nsimp", {"lean3"}),
    ("cases h with x hx\nexact hx", {"lean3"}),
    ("induction n with n ih\nsimp", {"lean3"}),
    ("exact nat.succ_le_iff.mp h", {"lean3"}),
    ("", {"empty"}),
    ("   \n  ", {"empty"}),
]


@pytest.mark.parametrize("block,reasons", ATTACKS)
def test_attacks_are_rejected(block, reasons):
    res = check_tactics(block)
    assert not res.ok, block
    assert res.reason in reasons, (block, res.reason, res.detail)


def test_reason_detail_is_informative():
    res = check_tactics("exact h\n#eval IO.Process.exit 0")
    assert "#eval" in res.detail


def test_strip_comments_keeps_columns_and_nesting():
    src = "a -- x\n/- b /- c -/ d -/ e"
    out = strip_comments(src)
    assert out.split("\n")[0] == "a     " and out.endswith(" e") and len(out) == len(src)
    assert "d" not in out
    assert strip_comments("/- open") is None


VALID_TERMS = [
    "0 < x",
    "x ^ 2 - 2 * x + 1 = (x - 1) ^ 2",
    "∑ i ∈ Finset.range n, (i : ℕ) = n * (n - 1) / 2",
    "∀ y : ℝ, 0 ≤ y → Real.sqrt (y ^ 2) = y",
    "#(Finset.filter (fun k => k % 3 = 0) (Finset.range 10)) = 4",
    "(by norm_num : (2 : ℝ) ≠ 0) = (by norm_num : (2 : ℝ) ≠ 0)",
]


@pytest.mark.parametrize("term", VALID_TERMS)
def test_valid_terms_pass(term):
    assert check_term(term).ok


@pytest.mark.parametrize("term,reason", [
    ("x = 1 := by sorry", "escape"),
    ("x = 1\ntheorem evil : False := sorry", "escape"),
    ("x = 1 ∧ sorry", "sorry"),
    ("Lean.ofReduceBool a b", "native"),
    ("(IO.println 1 = IO.println 1)", "io"),
    ("\"s\" = \"s\"", "string"),
    ("", "empty"),
])
def test_bad_terms_are_rejected(term, reason):
    res = check_term(term)
    assert not res.ok and res.reason == reason, (term, res)


@pytest.mark.parametrize("term", [
    "0 < x) (hbad : False",          # would close the binder `(h : …)` of a subgoal lemma
    "0 < x)",
    "(0 < x",
    "⟨1, 2⟩ = (⟨1, 2⟩",
    "f (x] = 1",
])
def test_unbalanced_terms_are_rejected(term):
    res = check_term(term)
    assert not res.ok and res.reason == "brackets", (term, res)


@pytest.mark.parametrize("block", [
    "exact h\nnotation3 \"x\" => 1",
    "exact h\nsimproc foo (_) := fun _ => pure .continue",
    "exact h\nadd_aesop_rules safe foo",
    "exact h\nproof_wanted bad : False",
    "exact (Mathlib.Tactic.foo)",
    "open Batteries in\nexact h",
    "exact (Aesop.foo)",
])
def test_mathlib_commands_and_meta_namespaces_are_rejected(block):
    assert not check_tactics(block).ok
