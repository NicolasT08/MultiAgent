"""runs/<run>/run_config.json (RNF-2): everything needed to reproduce or audit a run.

Written when the run starts and rewritten when it ends (the observed models, the Lean
runner's `degraded` flag and the LLM stats are only known at the end). Git is only read
(`git log -1`, `git status --porcelain`), never modified (CLAUDE.md §1.8).
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from formal_mas.config import ROOT, load_models, load_pipeline
from formal_mas.tracing import iso, utc_now

PACKAGES = ("openai", "pydantic", "pyyaml", "numpy", "torch", "faiss-cpu", "sentence-transformers", "psutil")


def _git(root: Path, *args: str) -> str | None:
    """Read-only git (`--no-optional-locks`: `status` never takes the index lock)."""
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.rstrip() if out.returncode == 0 else None  # porcelain lines start with a space


def git_info(root: Path = ROOT) -> dict:
    """Current commit and whether the working tree has uncommitted changes (read-only)."""
    log = _git(root, "log", "-1", "--format=%H%n%cI%n%s")
    status = _git(root, "status", "--porcelain")
    commit, date, subject = ((log or "").split("\n", 2) + [None, None, None])[:3]
    changed = [line[3:] for line in (status or "").splitlines() if line.strip()]
    return {"commit": commit or None, "commit_date": date, "commit_subject": subject,
            "dirty": None if status is None else bool(changed), "changed_files": changed[:200]}


def lean_versions(root: Path = ROOT, project_dir: str = "lean_project") -> dict:
    """Lean toolchain and Mathlib revision pinned by lean_project/ (lake-manifest.json)."""
    proj = root / project_dir
    out: dict = {"lean_toolchain": None, "mathlib_rev": None, "mathlib_tag": None}
    tc = proj / "lean-toolchain"
    if tc.exists():
        out["lean_toolchain"] = tc.read_text(encoding="utf-8").strip()
    manifest = proj / "lake-manifest.json"
    try:
        pkgs = json.loads(manifest.read_text(encoding="utf-8")).get("packages", [])
    except (OSError, ValueError):
        pkgs = []
    for p in pkgs:
        if p.get("name") == "mathlib":
            out["mathlib_rev"], out["mathlib_tag"] = p.get("rev"), p.get("inputRev")
    return out


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rag_index_info(root: Path = ROOT, pipeline_cfg: dict | None = None, models_cfg: dict | None = None) -> dict:
    """Metadata of the retrieval index the run would use (from the files, no model loaded)."""
    pipeline_cfg = pipeline_cfg if pipeline_cfg is not None else load_pipeline()
    models_cfg = models_cfg if models_cfg is not None else load_models()
    retr = pipeline_cfg.get("retrieval") or {}
    index_dir = root / retr.get("index_dir", "data/index/full")
    key = (models_cfg.get("embeddings") or {}).get("selected")
    corpus = _read_json(index_dir.parent / "corpus_meta.json") or {}  # written by build_index.py
    dense = _read_json(index_dir / str(key) / "meta.json") or {} if key else {}
    bm25 = _read_json(index_dir / "bm25" / "meta.json") or {}
    return {"index_dir": index_dir.relative_to(root).as_posix() if index_dir.is_relative_to(root) else str(index_dir),
            "present": bool(corpus or dense or bm25), "retrieval": retr,
            "corpus": {k: corpus.get(k) for k in ("mathlib_commit", "lean_toolchain", "created_utc")},
            "dense": {k: dense.get(k) for k in ("embedding_key", "hf_id", "hf_revision", "dim", "precision",
                                                 "n_corpus", "corpus_names_sha256")},
            "bm25": {k: bm25.get(k) for k in ("size", "corpus_names_sha256", "k1", "b")}}


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def package_versions() -> dict:
    out = {}
    for p in PACKAGES:
        try:
            out[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            out[p] = None
    return out


def build_run_config(*, run_id: str, config_name: str, seed: int, dataset: Path, split: str | None,
                     llm_info: dict, lean_run_info: dict | None, pipeline_cfg: dict, argv: list[str],
                     root: Path = ROOT, extra: dict | None = None) -> dict:
    return {
        "run_id": run_id, "config": config_name, "profile": llm_info.get("profile"), "seed": seed,
        "created_utc": iso(utc_now()), "argv": argv,
        "dataset": {"path": str(dataset), "sha256": file_sha256(dataset), "split": split},
        "models": llm_info, "lean": {**lean_versions(root, (pipeline_cfg.get("lean") or {}).get("project_dir",
                                                                                                  "lean_project")),
                                     "runner": lean_run_info},
        "rag_index": rag_index_info(root, pipeline_cfg), "pipeline": pipeline_cfg, "git": git_info(root),
        "python": sys.version.split()[0], "platform": platform.platform(), "packages": package_versions(),
        **(extra or {}),
    }


def write_run_config(run_dir: Path, cfg: dict) -> Path:
    path = Path(run_dir) / "run_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path
