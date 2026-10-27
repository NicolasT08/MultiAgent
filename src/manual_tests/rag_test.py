import sys
sys.path.insert(0, "src")
from formal_mas.retrieval.retriever import Retriever

r = Retriever() 

consultas = [
    "(x : ℝ) : 0 ≤ x ^ 2",
    "(a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2",
    "(p : ℕ) (hp : p.Prime) (h : p ≠ 2) : Odd p",
    "(n : ℕ) : ∑ i ∈ Finset.range (n + 1), i = n * (n + 1) / 2",
    "x : ℝ\n⊢ 0 ≤ x ^ 2", 
]

for q in consultas:
    print("\n=== ", q.replace("\n", " | "))
    for hit in r.retrieve(q)[:15]:
        print(f"  {hit.score:.3f}  {hit.name}")

print("\n=== Corrección de nombres")
for mal in ["Finset.sum_rnage_succ", "nat.succ_le_iff"]:
    print(" ", mal, "→", r.suggest_names(mal)[:3])