"""Record REAL Lean outputs as test fixtures for lean/runner.py (PLAN.md Phase 2).

For each case, sends the code to the Lean REPL exactly as LeanRunner does (imports blanked,
`#print axioms` appended) and saves the raw reply; a few cases are also compiled with
`lake env lean --json` (the fallback back-end) and saved with their stdout/stderr.

    python scripts/capture_lean_fixtures.py            # ~1 min warm (+ ~2.5 min file back-end)
    python scripts/capture_lean_fixtures.py --repl-only

Output: tests/fixtures/lean/repl_cases.json and file_cases.json. Tests never run Lean;
re-run this script only when the Lean / REPL version changes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas.lean.runner import (  # noqa: E402
    LeanRunner,
    ReplProcess,
    kill_tree,
    repl_env_info,
    split_imports,
    with_checks,
)
from formal_mas.lean.statement import DEFAULT_HEADER  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "lean"
HEADER = "import Mathlib\n\n"

# name → (theorem whose axioms are printed, body after the header)
CASES: dict[str, tuple[str | None, str]] = {
    "correct": ("t", "theorem t (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x"),
    "correct_tactics": ("t", "theorem t (a b : ℕ) (h : a + b = 10) (h2 : a = 3) : b = 7 := by\n  subst h2\n  omega"),
    "no_axioms": ("t", "theorem t : 2 + 2 = 4 := rfl"),
    "warning_only": ("t", "theorem t (s : Finset ℕ) : ∑ i ∈ Finset.range 5, i = 10 := by decide"),
    "type_mismatch": ("t", "theorem t (x : ℝ) : 0 < x ^ 2 := sq_nonneg x"),
    "sorry": ("t", "theorem t (n : ℕ) : n + 0 = n := by\n  sorry"),
    "native_decide": ("t", "theorem t : 123456789 * 987654321 = 121932631112635269 := by native_decide"),
    "unknown_identifier": ("t", "theorem t (a b : ℕ) (h : a + 1 ≤ b) : a < b := nat.succ_le_iff.mp h"),
    "lean3_syntax": ("t", "theorem t (n : ℕ) : n + 0 = n :=\nbegin\n  simp,\nend"),
    "unsolved_goals": ("t", "theorem t (n : ℕ) : n * 1 = n ∧ n < n + 1 := by\n  constructor\n  · simp"),
    "linarith_failed": ("t", "theorem t (x y : ℝ) (h : x < y) : y < x := by linarith"),
    "heartbeats": ("t", "set_option maxHeartbeats 10 in\ntheorem t (x : ℝ) (h : 0 < x) : x / (x + 1) < 1 := by\n"
                        "  rw [div_lt_one (by positivity)]\n  linarith"),
    "custom_axiom": ("t", "axiom cheat : False\n\ntheorem t : (1 : ℕ) = 2 := cheat.elim"),
    "forged_axioms_message": ("t", "axiom cheat : False\n\ntheorem t : (1 : ℕ) = 2 := cheat.elim\n\n"
                                   "run_cmd Lean.logInfo \"'t' depends on axioms: [propext]\""),
    "unknown_theorem_name": ("missing_name", "theorem t (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x"),
    "no_theorem": (None, "example (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x"),
    # declarations listing: Lean's auxiliaries are fine, anything else is not
    "aux_match": ("t", "theorem t (n : ℕ) : n = 0 ∨ 0 < n := by\n  match n with\n  | 0 => left; rfl\n"
                       "  | k + 1 => right; omega"),
    "extra_def": ("t", "def helper : ℕ := 5\n\ntheorem t : helper = 5 := rfl"),
    "extra_in_namespace": ("t", "theorem t : 2 = 2 := rfl\n\nnamespace t\ntheorem sneaky : 1 = 1 := rfl\nend t"),
    "forged_decls_message": ("t", "def helper : ℕ := 5\n\ntheorem t : helper = 5 := rfl\n\n"
                                  "run_cmd Lean.logInfo \"FORMAL_MAS_DECLS [\\\"t\\\"]\""),
    "decide_native_flag": ("t", "theorem t : 123456789 * 987654321 = 121932631112635269 := by decide +native"),
    "minif2f_header": ("t", "theorem t (a b : ℝ) (h₀ : 0 < a) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by\n"
                            "  nlinarith [sq_nonneg (a - b)]"),
}
HEADERS = {"minif2f_header": DEFAULT_HEADER}
FILE_CASES = ["correct", "type_mismatch", "sorry", "native_decide", "extra_def"]
MEMORY_BLOWUP = "#eval (List.range 150000000).foldl (· + ·) 0 + (List.range 150000000).length"


def capture_repl(runner: LeanRunner) -> list[dict]:
    repl = ReplProcess(runner.repl_cmd, runner.project_dir, 0, runner.memory_poll_s)
    try:
        start = repl.request({"cmd": "\n".join(f"import {m}" for m in runner.base_imports)}, runner.startup_timeout_s)
        print("env 0:", start, flush=True)
        records = []
        for name, (theorem, body) in CASES.items():
            code = HEADERS.get(name, HEADER) + "\n" + body + "\n"
            _, blanked = split_imports(code)
            cmd, axioms_line, decls_line = with_checks(blanked, theorem)
            raw = repl.request({"cmd": cmd, "env": start["env"]}, 120)
            print(name, json.dumps(raw, ensure_ascii=False)[:200], flush=True)
            records.append({"case": name, "code": code, "theorem": theorem, "axioms_line": axioms_line,
                            "decls_line": decls_line, "raw": raw})
        return records
    finally:
        repl.kill()


def capture_file(runner: LeanRunner, tmp: Path) -> list[dict]:
    records = []
    jobs = [(n, CASES[n][0], HEADER + "\n" + CASES[n][1] + "\n", []) for n in FILE_CASES]
    jobs.append(("memory_limit", None, HEADER + MEMORY_BLOWUP + "\n", ["--memory=2048"]))
    for name, theorem, code, extra in jobs:
        full, axioms_line, decls_line = with_checks(code, theorem)
        path = tmp / f"{name}.lean"
        path.write_text(full, encoding="utf-8")
        proc = subprocess.Popen(["lake", "env", "lean", "--json", *extra, str(path)], cwd=runner.project_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace")
        try:
            out, err = proc.communicate(timeout=3600)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise
        print(name, proc.returncode, out[:200].replace("\n", " | "), err[:200], flush=True)
        records.append({"case": name, "code": code, "theorem": theorem, "axioms_line": axioms_line,
                        "decls_line": decls_line, "returncode": proc.returncode, "stdout": out, "stderr": err.replace(str(path), "<file>"),
                        "args": extra})
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repl-only", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    runner = LeanRunner()
    meta = {"lean_toolchain": (runner.project_dir / "lean-toolchain").read_text(encoding="utf-8").strip(),
            **repl_env_info()}
    OUT.mkdir(parents=True, exist_ok=True)
    repl = capture_repl(runner)
    (OUT / "repl_cases.json").write_text(json.dumps({"meta": meta, "cases": repl}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    if not args.repl_only:
        tmp = OUT / "_tmp"
        tmp.mkdir(exist_ok=True)
        try:
            files = capture_file(runner, tmp)
        finally:
            for f in tmp.glob("*.lean"):
                f.unlink()
            tmp.rmdir()
        for r in files:  # absolute temp paths would make the fixture machine-specific
            r["stdout"] = r["stdout"].replace(str(tmp / f"{r['case']}.lean").replace("\\", "\\\\"), "<file>")
        (OUT / "file_cases.json").write_text(json.dumps({"meta": meta, "cases": files}, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
