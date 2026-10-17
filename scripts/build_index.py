"""Build the retrieval corpus from the pinned Mathlib environment.

Stage 1 (Phase 1.1): run scripts/extract_corpus.lean with `lake env lean` (allowed for
offline data-building scripts, CLAUDE.md §7) to dump data/index/corpus_raw.jsonl, then
post-process it into data/index/corpus.jsonl and data/index/corpus_meta.json.
The full extraction takes ~15 min on a laptop (single process).

Usage:
    python scripts/build_index.py [--module-prefix Mathlib] [--skip-extract]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_mas.retrieval.corpus import (  # noqa: E402
    SourceIndex,
    build_corpus,
    corpus_stats,
    load_jsonl,
    read_raw,
    write_jsonl,
)

EXTRACT_SCRIPT = ROOT / "scripts" / "extract_corpus.lean"


def mathlib_commit(lean_project: Path) -> str | None:
    manifest = json.loads((lean_project / "lake-manifest.json").read_text(encoding="utf-8"))
    for pkg in manifest.get("packages", []):
        if pkg.get("name") == "mathlib":
            return pkg.get("rev")
    return None


def run_extraction(lean_project: Path, raw_path: Path, module_prefix: str) -> None:
    """Dump the environment with Lean. Raises CalledProcessError if Lean fails."""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "FORMAL_MAS_CORPUS_OUT": str(raw_path.resolve()),
        "FORMAL_MAS_MODULE_PREFIX": module_prefix,
    }
    proc = subprocess.run(
        ["lake", "env", "lean", str(EXTRACT_SCRIPT)],
        cwd=lean_project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lean-project", type=Path, default=ROOT / "lean_project")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "index")
    parser.add_argument("--module-prefix", default="Mathlib", help="only modules under this prefix")
    parser.add_argument("--skip-extract", action="store_true", help="reuse an existing corpus_raw.jsonl")
    args = parser.parse_args(argv)

    raw_path = args.out / "corpus_raw.jsonl"
    if not args.skip_extract:
        run_extraction(args.lean_project, raw_path, args.module_prefix)

    lean_meta, entries = read_raw(raw_path)
    sources = SourceIndex(args.lean_project / ".lake" / "packages" / "mathlib")
    decls, excluded = build_corpus(entries, sources)
    corpus_path = args.out / "corpus.jsonl"
    n = write_jsonl(decls, corpus_path)

    stats = corpus_stats(load_jsonl(corpus_path))
    meta = {
        "mathlib_commit": mathlib_commit(args.lean_project),
        "lean_toolchain": (args.lean_project / "lean-toolchain").read_text(encoding="utf-8").strip(),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extraction": lean_meta,
        "postprocess": "whitespace collapsed; leading name and universe lists `.{...}` removed",
        "excluded": dict(excluded),
        "corpus": stats,
    }
    (args.out / "corpus_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {n} declarations to {corpus_path}")
    print(json.dumps({"excluded": dict(excluded), **stats}, indent=2))


if __name__ == "__main__":
    main()
