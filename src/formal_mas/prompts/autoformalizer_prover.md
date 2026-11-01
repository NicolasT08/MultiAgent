<!-- Autoformalizer, `prover` style (models.yaml → prompt_style: prover), for whole-proof
     models such as Goedel-Prover-V2, which are trained on "Complete the following Lean 4 code"
     and answer with a complete ```lean4 block (no JSON). The sketch goes in as a Lean comment
     above the lemma, as in the provers' own informal-to-formal prompts. The code takes from the
     answer only the tactics after `:= by` of the lemma (by its name); whatever statement the model
     writes is discarded (the lemma is always rebuilt by statement.py). Not deployed yet
     (2026-10-29): tested with the fake LLM only. -->
USER:
Complete the following Lean 4 code:

```lean4
{{header}}
/- Proof sketch ({{strategy}}):
{{sketch}}
Mathlib lemmas that may help: {{lemma_names}}{{feedback}} -/
{{lemma}} := by
```

Before producing the Lean 4 code to formally prove the given theorem, provide a detailed proof plan outlining the main proof steps and strategies.
The plan should highlight key ideas, intermediate lemmas, and proof structures that will guide the construction of the final formal proof.
