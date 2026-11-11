"""Download the published run logs into runs/, so the analysis can be regenerated.

    python scripts/download_runs.py                 # every run of the dataset -> runs/
    python scripts/download_runs.py --dest OTHER    # somewhere else (OTHER/<run>/, OTHER/spend_ledger.jsonl)
    python scripts/download_runs.py --revision REV  # a specific revision of the dataset

Source: the `runs/` folder of the Hugging Face dataset NicolasT08/formal-mas-eval (uploaded by
scripts/publish_hf.py eval): the run folders read by docs/results/make_tables.py and the spend
ledger. The ledger lands in runs/spend_ledger.jsonl, never in data/spend_ledger.jsonl (that file
is the live ledger whose sum the spend caps of config/pipeline.yaml check).

Existing files are never overwritten: a file already present with the same content is skipped, a
different one stops the script. Afterwards:

    python docs/results/make_tables.py
    python docs/results/check_explained.py
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "NicolasT08/formal-mas-eval"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download the published run logs into runs/.")
    ap.add_argument("--dest", type=Path, default=ROOT / "runs", help="target folder (default: runs/)")
    ap.add_argument("--revision", default=None, help="dataset revision (default: main)")
    a = ap.parse_args(argv)
    from huggingface_hub import HfApi, snapshot_download

    files = [f for f in HfApi().list_repo_files(REPO, repo_type="dataset", revision=a.revision)
             if f.startswith("runs/")]
    if not files:
        print(f"ERROR: {REPO} has no runs/ folder at this revision.")
        return 1
    print(f"{REPO}: {len(files)} files under runs/")
    with tempfile.TemporaryDirectory(prefix="formal_mas_runs_") as tmp:
        snapshot_download(REPO, repo_type="dataset", revision=a.revision, allow_patterns=["runs/*", "runs/**"],
                          local_dir=tmp)
        conflicts, new, same = [], [], 0
        for f in files:
            src = Path(tmp) / f
            dst = a.dest / Path(f).relative_to("runs")
            if dst.exists():
                if sha256(dst) == sha256(src):
                    same += 1
                else:
                    conflicts.append(dst)
            else:
                new.append((src, dst))
        if conflicts:
            print("ERROR: these files exist with a different content; nothing was written:")
            for c in conflicts:
                print(f"  {c}")
            print("Use --dest to download somewhere else.")
            return 1
        for src, dst in new:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    runs = sorted({Path(f).parts[1] for f in files if len(Path(f).parts) > 2})
    print(f"wrote {len(new)} files ({same} already present) into {a.dest}: {len(runs)} runs + spend_ledger.jsonl")
    print("next: python docs/results/make_tables.py && python docs/results/check_explained.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
