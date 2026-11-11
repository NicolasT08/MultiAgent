---
license: other
license_name: mixed-sources
license_link: LICENSE.md
language:
- en
pretty_name: Formal-MAS evaluation set
tags:
- lean4
- mathlib
- theorem-proving
- minif2f
- putnambench
size_categories:
- n<1K
configs:
- config_name: default
  data_files:
  - split: dev
    path: data/eval.jsonl
---

# Formal-MAS evaluation set

45 competition problems stated in Lean 4 (Mathlib), used to evaluate the Formal-MAS multi-agent
prover. The set was fixed before any tuning on it. Code: https://huggingface.co/NicolasT08/formal-mas
(retrieval index: https://huggingface.co/datasets/NicolasT08/formal-mas-index)

## Files

- `data/eval.jsonl`: one problem per line: `id`, `source`, `source_id`, `source_commit`,
  `source_split`, `license`, `informal`, `header`, `formal_statement`, `statement`, `area`,
  `difficulty`, `split` (`dev` / `test`), `seed`; some records also keep `formal_statement_original`
  and `converted` (see "Allowed edits").
- `data/curation/`: `criteria.yaml` (difficulty, area, grid, seed, labels), `discarded.json`
  (every discarded problem and why), `fidelity_review.yaml` (informal-to-formal review, notes,
  verification limits, post-test review), `build_info.json` (Lean back-end of the build).
- `docs/results_explained.md`: the results in prose, table by table.
- `docs/results/`: the study's tables (`results.md`, `tables.tex`), figure SR(k), method notes,
  the Baseline-B review sheet and the scripts that regenerate them from the run logs.
- `runs/`: the run logs those scripts read (see "Run logs" below) and `runs/spend_ledger.jsonl`.

The `split` column is the study's own `dev` / `test` partition (15 / 30), not a Hub split: the
file is exposed as one table; filter on `split`.

## Sources and licenses

| Source | Commit | License |
|---|---|---|
| miniF2F, Lean 4 port in DeepSeek-Prover-V1.5 (`datasets/minif2f.jsonl`) | `2c4ba911` | MIT |
| PutnamBench (`lean4/src`, `informal/putnam.json`) | `a635813f` | Apache-2.0 (Lean statements) |

Each record keeps its source, original id, source commit and license (`license` column).
The set has no single license; each part keeps the license of its source (see `LICENSE.md`):

- **miniF2F problems** (40 records; Lean statements and informal text): MIT, as in the
  DeepSeek-Prover-V1.5 Lean 4 port (`LICENSE-CODE`).
- **PutnamBench Lean statements** (5 records): Apache-2.0.
- **PutnamBench informal statements: not redistributed.** PutnamBench states that they are available
with permission from the MAA, so for the 5 `putnam_*` records `informal` holds a reference to the
problem instead (`informal_redacted: true`); the Lean statements (Apache-2.0) are included.

## Grid, difficulty and split

Difficulty is decided only by the competition of origin: easy = `mathd_*`; medium = AMC 12, AIME
and the textbook families `algebra_*`, `numbertheory_*`, `induction_*`; hard = IMO, IMO Shortlist,
Putnam. Area labels come from reading the informal text (strict combinatorics; geometry,
trigonometry and real analysis out of scope). PutnamBench is used only for hard × combinatorics,
where miniF2F has no pool.

| | algebra | number_theory | combinatorics | total |
|---|---|---|---|---|
| easy | 8 (3 dev) | 7 (2 dev) | 0 | 15 |
| medium | 7 (2 dev) | 6 (2 dev) | 2 (1 dev) | 15 |
| hard | 5 (2 dev) | 5 (1 dev) | 5 (2 dev) | 15 |
| total | 20 | 18 | 7 | 45 |

Target 5 per cell; miniF2F has no strict combinatorics at easy level and only 2 at medium, so the
missing 8 were reassigned within the same difficulty. Within a cell, problems are taken in the order
of `sha256("2026:<id>")`; a problem that does not compile with `sorry` is replaced by the next one.
`dev` slots (15) are allocated per cell by largest remainder. No system result informed selection.
`dev` was used for all tuning; `test` (30) only for the final runs.

## Allowed edits

- `∑ x in s` / `∏ x in s` → `∑ x ∈ s` / `∏ x ∈ s` by script (original kept in
  `formal_statement_original`, record marked `"converted": "big_operator_in_to_mem"`).
- PutnamBench's `:= sorry` becomes `:= by`, the format of all records.

## Fidelity review

Every statement was compared with its informal text by hand (`data/curation/fidelity_review.yaml`):
the review establishes that statements match the informal problem, not that they are true. After
the test runs, every solved problem with a fidelity note was reviewed again by hand before being
counted (`post_test_review`); no reported number changed.

## Contamination

All problems are public (miniF2F, PutnamBench and their competitions), so they may be in the
training data of the models evaluated; results measure the system on public problems, not on unseen
ones. Lean checks every accepted proof, so contamination can make a proof easier to find but cannot
make a wrong proof pass.

## Results (test split, n = 30)

Full system (C5) 24/30 verified; without the Critic (C4) 14/30; LLM-only Lean proof (C1) 20/30;
without retrieval (C3) 20/30. See `docs/results/results.md`.

## Run logs

`runs/` holds the 13 run folders that `docs/results/make_tables.py` reads, plus the spend ledger:

| Folders | Role |
|---|---|
| `20261103T174646Z_C5_experiment`, `…212359Z_C5_experiment_resume`, `…225140Z_C5_experiment_resume2` | C5 on `test` (the resumes complete the problems the first run left out) |
| `20261103T235556Z_C4_experiment`, `20261104T194056Z_C3_experiment`, `20261104T232056Z_C2_experiment_check` | C4, C3, C2 on `test` |
| `20261105T131443Z_C1_experiment`, `20261107T200233Z_C1_experiment_resume` | C1 on `test` (merged in `C1_test_merged_results.jsonl`) |
| `20261103T151038Z_C5_experiment_r1`, `20261103T130158Z_C5_experiment`, `20261103T173028Z_C2_experiment_check` | `dev` runs used for the dry run of the analysis (`make_tables.py --dev`) |
| `20261103T161957Z_C1_experiment_c1_cost` | `dev` cost run of C1 (analysis plan, method notes) |
| `20261108T130803Z_C5_experiment_pro_pro_hard` | exploratory post-hoc run (C5 with deepseek-v4-pro), descriptive table only |

Each folder has `run_config.json` (models, seed, Lean/Mathlib/REPL versions, git commit, spend caps;
RNF-2), `results.jsonl` (one record per problem), `trace.jsonl` (one JSON event per line: every LLM
call, Lean check, policy decision) and, when present, `console.log`. `runs/spend_ledger.jsonl` has one
line per paid LLM call of the whole project (tokens and USD; no prompts).

To regenerate the tables from them, with the code repository checked out:

```bash
python scripts/download_runs.py          # -> runs/ (refuses to overwrite existing folders)
python docs/results/make_tables.py       # -> docs/results/results.md, tables.tex, figures/
python docs/results/check_explained.py   # numbers of docs/results_explained.md vs results.md
```

`results.md` and `tables.tex` come out byte-identical to the published ones.

Changes made before publishing, and nothing else: absolute paths of the authors' machine in
`run_config.json` (`argv`, `dataset.path`) were made relative to the repository (`data/eval.jsonl`)
or reduced to `<tmp>/<file name>` for temporary copies of the dataset; in `test_remaining.jsonl` (the
problems a resume run was given) the informal text of the PutnamBench records is replaced as in
`data/eval.jsonl`, so `dataset.sha256` of those runs refers to the original, unredacted file. The
logs contain no prompts with the PutnamBench informal text and no API keys (every file is scanned
before upload by `scripts/publish_hf.py`). The run logs are MIT, like the rest of the study files.

