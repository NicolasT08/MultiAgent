"""Baseline B (C2) and its false-positive rate FPR_B (Eq. 16; docs/analysis_plan.md §4.2).

    MODEL_PROFILE=dev python scripts/run_baseline_b.py --split dev --limit 3 --check --echo
    MODEL_PROFILE=experiment python scripts/run_baseline_b.py --split test --check   # PAID: ask first

The same run as `run_eval.py --config C2` (same graph, flags, outputs in runs/<run>/); with `--check`
every proof C2 claims (self-assessed "correct") is formalized by the Autoformalizer–Verifier loop
(formal_mas.baselines.baseline_b.check_claim) and only Lean's acceptance (Eq. 2) counts:
FPR_B = |{x ∈ B⁺ : ¬Acc}| / |B⁺| over the claims checked to the end. The summary in run_config.json
and on the console has `check`: claimed, verified, not_verified, fpr_b, calls and cost of the check.
Every other option is run_eval.py's (--data, --split, --limit, --echo, --run-usd, --run-name, --seed).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("run_eval", Path(__file__).resolve().parent / "run_eval.py")
run_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_eval)


def main(argv: list[str] | None = None, **kwargs) -> int:
    """Keyword arguments go to run_eval.main (offline tests)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--config" in argv or any(a.startswith("--config=") for a in argv):
        raise SystemExit("run_baseline_b.py always runs C2; do not pass --config")
    return run_eval.main(["--config", "C2", *argv], **kwargs)


if __name__ == "__main__":
    raise SystemExit(main(on_run_dir=run_eval.copy_console))
