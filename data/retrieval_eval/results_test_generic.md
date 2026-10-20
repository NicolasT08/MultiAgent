> Single re-evaluation on Q-test (2026-10-19) of the generic_query method (V1, rrf(bm25+e5-base/instr)+generic) against the method chosen in Phase 1.4 (V0, rrf(bm25+e5-base/instr)). The change was motivated by a manual error analysis of generic olympiad lemmas (sq_nonneg, two_mul_le_add_sq) and checked on Q-dev only; it was adopted BEFORE this run and was not tuned on Q-test numbers. The original Phase 1.4 test results remain in results_test.*.

| Method (c) | R@4 | R@8 | R@16 | R@32 | MRR |
|---|---|---|---|---|---|
| bm25 | 0.394 | 0.561 | 0.621 | 0.697 | 0.346 |
| e5-base/no_instr | 0.379 | 0.455 | 0.545 | 0.606 | 0.296 |
| e5-base/instr | 0.333 | 0.409 | 0.530 | 0.606 | 0.279 |
| rrf(bm25+e5-base/no_instr) | 0.424 | 0.545 | 0.621 | 0.667 | 0.333 |
| rrf(bm25+e5-base/instr) | 0.409 | 0.530 | 0.621 | 0.667 | 0.333 |
| rrf(bm25+e5-base/instr)+generic | 0.409 | 0.530 | 0.621 | 0.667 | 0.322 |

| Method (b) | R@4 | R@8 | R@16 | R@32 | MRR |
|---|---|---|---|---|---|
| bm25 | 0.424 | 0.591 | 0.652 | 0.727 | 0.361 |
| e5-base/no_instr | 0.394 | 0.470 | 0.606 | 0.682 | 0.306 |
| e5-base/instr | 0.333 | 0.424 | 0.576 | 0.697 | 0.286 |
| rrf(bm25+e5-base/no_instr) | 0.439 | 0.576 | 0.652 | 0.727 | 0.344 |
| rrf(bm25+e5-base/instr) | 0.439 | 0.561 | 0.652 | 0.697 | 0.347 |
| rrf(bm25+e5-base/instr)+generic | 0.439 | 0.561 | 0.652 | 0.697 | 0.336 |
