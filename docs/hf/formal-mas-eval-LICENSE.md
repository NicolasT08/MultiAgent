# Licenses of the Formal-MAS evaluation set

This collection has no single license. Each part keeps the license of its source:

| Part | Records | License |
|---|---|---|
| miniF2F problems: Lean 4 statements and informal text (DeepSeek-Prover-V1.5 Lean 4 port, `datasets/minif2f.jsonl`, commit `2c4ba911`) | 40 | MIT (`LICENSE-CODE` of the DeepSeek-Prover-V1.5 repository) |
| PutnamBench Lean 4 statements (`lean4/src`, commit `a635813f`) | 5 | Apache-2.0 |
| PutnamBench informal statements (`informal/putnam.json`) | 5 | **Not redistributed.** PutnamBench states they are available with permission from the MAA; the `informal` field of these records holds a reference to the problem instead. |
| Curation files (`data/curation/`), study results and their scripts (`docs/`), run logs and spend ledger (`runs/`) | n/a | MIT, Copyright (c) 2026 Andrés Felipe Mongui Torres and Nicolás Samuel Tinjacá Topia |

Each record of `data/eval.jsonl` names its source, source commit and license in its own fields.
