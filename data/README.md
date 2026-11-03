# Evaluation set (`eval.jsonl`)

45 competition problems stated in Lean 4, fixed before any tuning on this set. Build it with:

```bash
bash scripts/fetch_eval_sources.sh   # pinned sources into data/raw/ (not in git)
python scripts/build_eval_set.py     # seeded draw, compile with sorry, write eval.jsonl
```

## Sources and licenses

| Source | Commit | License |
|---|---|---|
| miniF2F, Lean 4 port in DeepSeek-Prover-V1.5 (`datasets/minif2f.jsonl`) | `2c4ba911` | MIT |
| PutnamBench (`lean4/src`, `informal/putnam.json`) | `a635813f` | Apache-2.0 (Lean statements) |

Each record keeps its source, original id, source commit and license. The problems are public,
so they may be in the training data of the models under evaluation.

PutnamBench states that its informal statements are available with permission from the MAA, so
Apache-2.0 covers the Lean statements only: check this before publishing the set. The MIT license of
the miniF2F port is the one in the DeepSeek-Prover-V1.5 repository (`LICENSE-CODE`).

## Criteria (`curation/criteria.yaml`)

Written before the draw, except for what its header lists as added afterwards.

- **Difficulty, by competition of origin.** easy: `mathd_*`. medium: AMC12, AIME and the textbook
  families `algebra_*`, `numbertheory_*`, `induction_*`. hard: IMO, IMO Shortlist, Putnam.
- **Area.** `mathd_*` keep the area of their prefix, even when they are coordinate geometry
  (`mathd_algebra_288`). Combinatorics is strict: counting configurations, binomials and their
  identities, pigeonhole, graphs, partitions; counting integers under an arithmetic condition is not
  combinatorics, and a divisibility or primality claim about a binomial sum is number theory. For other families,
  geometry, trigonometry and real analysis are out of scope.
- **PutnamBench** is used only where miniF2F has no pool (hard × combinatorics), and only for problems
  tagged combinatorics that are one self-contained theorem, without geometry, analysis, probability
  or linear-algebra tags. Factored `_solution` answers and local definitions are excluded.

## Grid

The target was 5 per cell. miniF2F has no strict combinatorics at easy level and 2 problems at medium
level, so the missing 8 were reassigned within the same difficulty, proportionally to the 5:5 target
of the other two cells; an odd one went to the larger pool (algebra).

`amc12a_2020_p4` was first labelled combinatorics and was corrected to number theory after the first
draw, by the written definition. The correction used no system result.

| | algebra | number_theory | combinatorics | total |
|---|---|---|---|---|
| easy | 8 (3 dev) | 7 (2 dev) | 0 | 15 |
| medium | 7 (2 dev) | 6 (2 dev) | 2 (1 dev) | 15 |
| hard | 5 (2 dev) | 5 (1 dev) | 5 (2 dev) | 15 |
| total | 20 | 18 | 7 | 45 |

## Draw and split (seed 2026)

Within a cell, problems are taken in the order of `sha256("2026:<id>")`. A problem that fails is
replaced by the next one. `dev` slots are allocated per cell by largest remainder (15 in total, ties
by the seed), filled first by the 5 problems of `dev_preview.jsonl`, then in seeded order. No C1–C5
result was used. Problems named in tests (`mathd_algebra_10`) are excluded.

## Allowed edits

- `∑ x in s` / `∏ x in s` → `∑ x ∈ s` / `∏ x ∈ s`, by script; the original is kept in
  `formal_statement_original` and the record is marked `"converted": "big_operator_in_to_mem"`.
- PutnamBench's placeholder `:= sorry` becomes `:= by`, the format of all records. This changes no
  part of the statement. `informal` comes from `informal/putnam.json`.

## Discarded

`curation/discarded.json` has every discarded problem and its reason; `curation/fidelity_review.yaml`
has the informal-to-formal review, with its notes and verification limits. `curation/build_info.json`
records the Lean back-end of the build; the Mathlib revision is pinned in `lean_project/lake-manifest.json`.
