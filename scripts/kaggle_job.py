"""Run the corpus embedding on a free Kaggle GPU (Phase 1.3) through the Kaggle CLI.

Steps (credentials come from the Kaggle CLI's own config, never from this repo):
    python scripts/kaggle_job.py dataset            # upload corpus + Q queries as a private dataset
    python scripts/kaggle_job.py kernel --limit 2000 --keys bge-base e5-base bge-m3   # speed test
    python scripts/kaggle_job.py status
    python scripts/kaggle_job.py fetch              # download /kaggle/working into data/index/kaggle/output
    python scripts/kaggle_job.py kernel --keys bge-base e5-base bge-m3                # full corpus
Then `python scripts/build_index.py --from-vectors data/index/kaggle/output` builds the indexes.

Only names, signatures, texts and query statements are uploaded; the gold sets of Q stay local.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data" / "index" / "kaggle"
DATASET_SLUG = "formal-mas-mathlib-corpus"
KERNEL_SLUG = "formal-mas-embed"
ACCELERATOR = "NvidiaTeslaT4"
KAGGLE = str(Path(sys.executable).with_name("kaggle.exe" if os.name == "nt" else "kaggle"))


def kaggle_user(cli_user: str | None) -> str:
    if cli_user:
        return cli_user
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8"))["username"]
    sys.exit("Kaggle username unknown: pass --user")


def run(*args: str) -> None:
    print("$ kaggle " + " ".join(args), flush=True)
    subprocess.run([KAGGLE, *args], check=True)


def build_dataset(user: str, update: bool) -> None:
    folder = WORK / "dataset"
    folder.mkdir(parents=True, exist_ok=True)
    with (ROOT / "data" / "index" / "corpus.jsonl").open(encoding="utf-8") as src, \
            (folder / "corpus.jsonl").open("w", encoding="utf-8") as dst:
        for line in src:
            r = json.loads(line)
            dst.write(json.dumps({k: r[k] for k in ("name", "signature", "text")}, ensure_ascii=False) + "\n")
    with (folder / "queries.jsonl").open("w", encoding="utf-8") as dst:
        for split in ("dev", "test"):
            with (ROOT / "data" / "retrieval_eval" / f"q_{split}.jsonl").open(encoding="utf-8") as src:
                for line in src:
                    r = json.loads(line)
                    dst.write(json.dumps({k: r[k] for k in ("name", "split", "query")}, ensure_ascii=False) + "\n")
    shutil.copy(ROOT / "config" / "models.yaml", folder / "models.yaml")
    shutil.copy(ROOT / "src" / "formal_mas" / "retrieval" / "encoders.py", folder / "encoders.py")
    meta = {"title": "formal-mas Mathlib corpus", "id": f"{user}/{DATASET_SLUG}", "licenses": [{"name": "other"}]}
    (folder / "dataset-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if update:
        run("datasets", "version", "-p", str(folder), "-m", "update corpus/encoders")
    else:
        run("datasets", "create", "-p", str(folder))


def render_job(keys: list[str], limit: int | None, precision: str, batch_size: int) -> str:
    code = (ROOT / "scripts" / "kaggle_embed.py").read_text(encoding="utf-8")
    job = (f"KEYS = {json.dumps(keys)}\nLIMIT = {limit}\nPRECISION = {json.dumps(precision)}\n"
           f"BATCH_SIZE = {batch_size}\n")
    new, n = re.subn(r"(# --- JOB.*?---\n).*?(# --- end JOB ---)", lambda m: m[1] + job + m[2], code, flags=re.S)
    if n != 1:
        raise RuntimeError("JOB block not found in kaggle_embed.py")
    return new


def push_kernel(user: str, keys: list[str], limit: int | None, precision: str, batch_size: int) -> None:
    folder = WORK / "kernel"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "kaggle_embed.py").write_text(render_job(keys, limit, precision, batch_size), encoding="utf-8")
    meta = {
        "id": f"{user}/{KERNEL_SLUG}",
        "title": KERNEL_SLUG,
        "code_file": "kaggle_embed.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,  # downloads the models from the Hugging Face Hub
        "dataset_sources": [f"{user}/{DATASET_SLUG}"],
        "competition_sources": [],
        "kernel_sources": [],
    }
    (folder / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    run("kernels", "push", "-p", str(folder), "--accelerator", ACCELERATOR)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["dataset", "kernel", "status", "fetch"])
    parser.add_argument("--user", help="Kaggle username (default: from ~/.kaggle/kaggle.json)")
    parser.add_argument("--update", action="store_true", help="dataset: new version of an existing dataset")
    parser.add_argument("--keys", nargs="+", default=["bge-base", "e5-base", "bge-m3"])
    parser.add_argument("--limit", type=int, help="kernel: embed only a random sample (speed test)")
    parser.add_argument("--precision", choices=["fp16", "fp32"], default="fp16")
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args(argv)
    user = kaggle_user(args.user)

    if args.command == "dataset":
        build_dataset(user, args.update)
    elif args.command == "kernel":
        push_kernel(user, args.keys, args.limit, args.precision, args.batch_size)
    elif args.command == "status":
        run("kernels", "status", f"{user}/{KERNEL_SLUG}")
    else:
        out = WORK / "output"
        if out.exists():
            shutil.rmtree(out)
        run("kernels", "output", f"{user}/{KERNEL_SLUG}", "-p", str(out))


if __name__ == "__main__":
    main()
