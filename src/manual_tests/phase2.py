# Prueba manual de la Fase 2. Ejecutar desde la raíz del repo:
#   .venv\Scripts\python.exe -X utf8 pruebas_manuales\fase2_test.py
import sys
import time

sys.path.insert(0, "src")

from formal_mas.lean.runner import LeanRunner, accepted
from formal_mas.lean.sanitize import check_tactics, check_term
from formal_mas.lean.statement import UnsafeLean, assemble, parse_theorem, skeleton

H = "import Mathlib\n\n"

# Caso -> (código, resultado esperado de accepted)
CASOS = {
    "correcta":      (H + "theorem t (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x",
                      "True, accepted"),
    "falsa":         (H + "theorem t : (2 : ℕ) + 2 = 5 := by norm_num",
                      "False, error"),
    "con_sorry":     (H + "theorem t (n : ℕ) : n + 0 = n := by\n  sorry",
                      "False, sorry"),
    "native_decide": (H + "theorem t : 123456789 * 987654321 = 121932631112635269 := by native_decide",
                      "False, nonstandard_axioms: ..."),
    "def_extra":     (H + "def helper : ℕ := 5\n\ntheorem t : helper = 5 := rfl",
                      "False, extra_declarations: helper"),
}

print("=== 1. Runner y predicado de aceptación ===")
print("(la primera verificación carga Mathlib: ~33 s, o mucho más si el disco está frío)")
with LeanRunner() as r:
    for nombre, (codigo, esperado) in CASOS.items():
        res = r.verify(codigo, theorem="t")
        print(f"{nombre:14} -> {accepted(res)}   esperado: {esperado}   "
              f"[{res.status}, {res.backend}, {res.elapsed_s} s]")
        for m in res.errors[:1]:
            print("               primer error:", m.text.splitlines()[0])

    print("\n=== 2. Caché ===")
    codigo = CASOS["correcta"][0]
    res = r.verify(codigo, theorem="t")
    print("misma prueba otra vez -> cached =", res.cached, "(esperado True)")

    print("\n=== 3. statement.py: esqueleto y ensamblado ===")
    thm = parse_theorem("theorem amgm (a b : ℝ) (h₁ : a + b = 10) : a * b ≤ 25 := by sorry")
    subgoals = ["0 ≤ (a - b) ^ 2", "(a + b) ^ 2 = 100"]
    cierre = "nlinarith [h1, h2]"

    esq = skeleton(thm, subgoals, cierre)
    print("--- esqueleto generado ---\n" + esq)
    res = r.verify(esq, theorem=thm.name)
    print("esqueleto compila (con sorry):", res.status == "finished" and not res.errors,
          "| accepted:", accepted(res), "(esperado False, sorry)")

    final = assemble(thm, subgoals, ["positivity", "rw [h₁]\nnorm_num"], cierre)
    print("--- prueba ensamblada ---\n" + final)
    res = r.verify(final, theorem=thm.name)
    print("ensamblada ->", accepted(res), "(esperado True, accepted)")

    try:
        assemble(thm, subgoals, ["positivity\ntheorem evil : False := sorry", "norm_num"], cierre)
        print("ERROR: dejó pasar un bloque malicioso")
    except UnsafeLean as e:
        print("bloque malicioso rechazado por assemble:", e)

    print("\n=== 4. Info de la corrida (para run_config.json) ===")
    print(r.run_info())

print("\n=== 5. sanitize.py (solo analiza texto, no ejecuta nada) ===")
ATAQUES = [
    "exact h\ntheorem evil : False := sorry",
    "#eval IO.Process.run {cmd := \"calc.exe\"}",
    "set_option debug.skipKernelTC true in\n  rfl",
    "run_tac Lean.Elab.Tactic.evalTactic (← `(tactic| trivial))",
    "native_decide",
    "decide +native",
    "sorry",
    "begin\n  simp\nend",
]
VALIDOS = ["nlinarith [sq_nonneg (a - b)]", "intro x\n  simp", "rcases h with h | h <;> omega"]
for t in ATAQUES:
    s = check_tactics(t)
    print("ataque  ->", "PASÓ (mal)" if s.ok else f"rechazado ({s.reason})", "|", t.splitlines()[0])
for t in VALIDOS:
    s = check_tactics(t)
    print("válido  ->", "pasa" if s.ok else f"RECHAZADO (mal: {s.reason})", "|", t.splitlines()[0])
print("check_term con ':=' ->", check_term("x = 1 := rfl").reason, "(esperado escape)")