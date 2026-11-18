"""Publish the project to the Hugging Face Hub from an explicit file list, never a whole folder.

    python scripts/publish_hf.py code            # dry run: what would change in NicolasT08/formal-mas
    python scripts/publish_hf.py index           # dry run: NicolasT08/formal-mas-index (data/index/)
    python scripts/publish_hf.py eval            # dry run: NicolasT08/formal-mas-eval (set + runs)
    python scripts/publish_hf.py space           # dry run: Space NicolasT08/formal-mas-demo (static page)
    python scripts/publish_hf.py code --upload   # stage, scan, show the diff, ask, then upload

Targets:
  code   every file of `git ls-files` at --ref (default HEAD), minus EXCLUDED_CODE. Contents are
         taken from git (`git show <ref>:<path>`), not from the working tree, so uncommitted edits
         are never published.
  index  the files of the retrieval index that the Retriever loads (INDEX_FILES under data/index/)
         + the card docs/hf/formal-mas-index.md. After an upload, data/index_manifest.json is
         written (repo, revision, size and SHA-256 per file); scripts/setup.py downloads from it.
  eval   the evaluation set and results already published (from git at --ref) + the run folders
         of RUN_FOLDERS + the spend ledger (runs/spend_ledger.jsonl) + the card
         docs/hf/formal-mas-eval.md (+ its LICENSE.md). scripts/download_runs.py fetches them back into runs/.
  space  the static Space (sdk: static, no Gradio, no Python on the server): the files of
         SPACE_FILES under space/, from git at --ref (or the working tree with --worktree, to
         preview before committing) + the card docs/hf/formal-mas-demo.md as README.md. space/data/
         must be exactly what scripts/build_space.py builds now, and space/ must hold no other file.
         No secret or variable is ever set on the Space: the page needs none.

Every staged file goes through the same transformations and checks:
  * the informal text of the PutnamBench problems is replaced by a reference to the problem
    (PutnamBench: "available with permission from the MAA"); in JSON Lines records the field
    `informal` is replaced and `informal_redacted: true` is added;
  * run folders: absolute paths of this machine become relative (repo root) or `<tmp>/<file>`;
  * scan: secrets (API keys, tokens, private keys), absolute paths under a user folder, e-mail
    addresses and any 40-character window of a PutnamBench informal statement abort the publish.

The upload is one commit with only the added/changed files (`--prune` also deletes remote files
that are no longer staged; .gitattributes and the files in KEEP_REMOTE are never deleted).
Nothing is uploaded without `--upload`, and `--upload` asks for the repo id unless `--yes`.
Credentials: the token saved by `huggingface-cli login` (or HF_TOKEN); never written anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "docs" / "hf"
OWNER = "NicolasT08"
REPOS = {"code": (f"{OWNER}/formal-mas", "model"),
         "index": (f"{OWNER}/formal-mas-index", "dataset"),
         "eval": (f"{OWNER}/formal-mas-eval", "dataset"),
         "space": (f"{OWNER}/formal-mas-demo", "space")}

# -- what is published ----------------------------------------------------------------------
EXCLUDED_CODE = {"docs/report/Proyecto_Inteligencia.pdf"}  # course document, not part of the code
KEEP_REMOTE = {".gitattributes", "LICENSE"}  # remote-only files that --prune never deletes
INDEX_FILES = [  # paths under data/index/, exactly what retriever.py and run_config.py read + corpus
    "corpus.jsonl", "corpus_meta.json",
    "full/bm25/bm25_idf.npy", "full/bm25/bm25_matrix.npz", "full/bm25/bm25_vocab.json",
    "full/bm25/lemmas.json", "full/bm25/meta.json",
    "full/e5-base/index.faiss", "full/e5-base/meta.json", "full/e5-base/names.json",
    "full/names_extra.json",
]
EVAL_FROM_GIT = ("data/eval.jsonl", "data/curation/", "docs/results/", "docs/results_explained.md")
RUN_FOLDERS = [
    # test split: the 8 sources of docs/results/make_tables.py (TEST_SOURCES, C1_MERGED, C1_PARTS)
    "20261103T174646Z_C5_experiment", "20261103T212359Z_C5_experiment_resume",
    "20261103T225140Z_C5_experiment_resume2", "20261103T235556Z_C4_experiment",
    "20261104T194056Z_C3_experiment", "20261104T232056Z_C2_experiment_check",
    "20261105T131443Z_C1_experiment", "20261107T200233Z_C1_experiment_resume",
    # dev: the stand-ins of make_tables.py --dev (DEV_SOURCES) and the C1 cost run of the analysis plan
    "20261103T151038Z_C5_experiment_r1", "20261103T130158Z_C5_experiment",
    "20261103T173028Z_C2_experiment_check", "20261103T161957Z_C1_experiment_c1_cost",
    # exploratory Pro run (make_tables.py PRO_RUN)
    "20261108T130803Z_C5_experiment_pro_pro_hard",
]
RUN_FILES = {"results.jsonl", "trace.jsonl", "run_config.json", "console.log", "test_remaining.jsonl",
             "C1_test_merged_results.jsonl"}
LEDGER = ROOT / "data" / "spend_ledger.jsonl"
SPACE = ROOT / "space"
SPACE_FILES = [  # paths under space/: the page, and the data of scripts/build_space.py
    "index.html", "app.js", "style.css",
    "data/i18n.json", "data/site.json",
    "data/replays/1_amc12_2001_p9_C5.json", "data/replays/2_imo_1964_p1_2_C5.json",
    "data/replays/3_algebra_absapb_C5.json", "data/replays/3b_algebra_absapb_C4.json",
    "data/replays/4_imo_1979_p1_C2.json",
]
CODE_FRONT_MATTER = """---
license: mit
library_name: formal-mas
tags:
- lean4
- theorem-proving
- multi-agent
- mathlib
---

"""
CODE_BANNER = (
    "> In this copy the informal text of the 5 PutnamBench problems in `data/eval.jsonl` is replaced by a "
    "reference (MAA permission); restore it from PutnamBench `informal/putnam.json` (commit a635813f) before "
    "reproducing runs on them: `scripts/fetch_eval_sources.sh` then `scripts/build_eval_set.py` rebuild the "
    "full file.\n\n")

# local account name: a folder such as D:\<name>\... is personal too (never written in this file)
USER = re.escape(Path.home().name) or "Users"

# -- scan -----------------------------------------------------------------------------------
SECRET_PATTERNS = {
    "openai-style key": r"\bsk-[A-Za-z0-9_\-]{16,}",
    "Hugging Face token": r"\bhf_[A-Za-z0-9]{20,}",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{30,}",
    "GitHub token": r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "bearer token": r"Bearer\s+[A-Za-z0-9._\-]{20,}",
    "assigned secret": r"(?i)\b[A-Z_]*(?:API_KEY|TOKEN|SECRET)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    "user folder path": r"(?i)\b[A-Z]:(?:\\\\|\\|/)+(?:Users|" + USER + r")(?:\\\\|\\|/)",
    "home path": r"/(?:home|Users)/[A-Za-z0-9._-]+/",
    "e-mail": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]",
}
EMAIL_OK = re.compile(r"(?i)@(?:example\.(?:com|org)|anthropic\.com|users\.noreply\.github\.com)$|^noreply@")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".lean", ".sh", ".tex",
                 ".log", ".cfg", ".ini", ".example", ".gitignore", ".csv", ".html", ".js", ".css", ""}
WINDOW, STEP = 40, 20


@dataclass
class Staged:
    path: str                    # path in the Hub repo
    data: bytes | None = None    # transformed content, or
    source: Path | None = None   # a large file uploaded as is (index)

    def size(self) -> int:
        return len(self.data) if self.data is not None else self.source.stat().st_size

    def sha256(self) -> str:
        h = hashlib.sha256()
        if self.data is not None:
            h.update(self.data)
        else:
            with self.source.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        return h.hexdigest()

    def git_sha1(self) -> str:
        data = self.data if self.data is not None else self.source.read_bytes()
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class PublishError(Exception):
    pass


# -- PutnamBench informal text --------------------------------------------------------------

def putnam_informal() -> dict[str, str]:
    """id -> informal statement of the PutnamBench records, from the local (unredacted) eval.jsonl."""
    out = {}
    for line in (ROOT / "data" / "eval.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["id"].startswith("putnam_") and not r.get("informal_redacted"):
            out[r["id"]] = r["informal"]
    return out


def putnam_reference(pid: str) -> str:
    _, year, prob = pid.split("_")
    return ("[Informal statement not redistributed: PutnamBench states it is available with permission from "
            f"the MAA. Problem: Putnam {year} {prob.upper()}; source: PutnamBench informal/putnam.json, "
            "commit a635813f.]")


def redact_jsonl(text: str) -> str:
    """Replace `informal` of putnam_* records (byte format of build_eval_set.py: ensure_ascii=False)."""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            lines.append(line)
            continue
        r = json.loads(line)
        if str(r.get("id", "")).startswith("putnam_") and "informal" in r and not r.get("informal_redacted"):
            r["informal"] = putnam_reference(r["id"])
            r["informal_redacted"] = True
            line = json.dumps(r, ensure_ascii=False)
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def redact_text(text: str, putnam: dict[str, str]) -> str:
    for pid, s in putnam.items():
        ref = putnam_reference(pid)
        for form in (s, json.dumps(s, ensure_ascii=False)[1:-1], json.dumps(s)[1:-1]):
            text = text.replace(form, ref)
    return text


def putnam_windows(putnam: dict[str, str]) -> dict[str, str]:
    """40-char windows (whitespace collapsed) of every statement -> problem id."""
    wins = {}
    for pid, s in putnam.items():
        norm = " ".join(s.split())
        for i in range(0, max(1, len(norm) - WINDOW + 1), STEP):
            wins[norm[i:i + WINDOW]] = pid
    return wins


# -- scanning -------------------------------------------------------------------------------

def is_text(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def scan(staged: list[Staged], putnam: dict[str, str]) -> list[str]:
    wins = putnam_windows(putnam)
    compiled = {k: re.compile(v) for k, v in SECRET_PATTERNS.items()}
    problems = []
    for s in staged:
        if not is_text(s.path):
            continue
        raw = s.data if s.data is not None else s.source.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for name, rx in compiled.items():
            for m in rx.finditer(text):
                hit = m.group(0)
                if name == "e-mail" and EMAIL_OK.search(hit):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{s.path}:{line}: {name}: {hit[:12]}…")
        norm = " ".join(text.replace("\\n", " ").split())
        for w, pid in wins.items():
            if w in norm:
                problems.append(f"{s.path}: PutnamBench informal text of {pid}: “{w[:30]}…”")
                break
    return problems


# -- staging --------------------------------------------------------------------------------

def git(*args: str) -> bytes:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if out.returncode != 0:
        raise PublishError(f"git {' '.join(args)}: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def git_files(ref: str) -> list[str]:
    return sorted(p for p in git("ls-tree", "-r", "-z", "--name-only", ref).decode("utf-8").split("\0") if p)


def transform(path: str, data: bytes, putnam: dict[str, str]) -> bytes:
    if not is_text(path):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if path.endswith(".jsonl"):
        text = redact_jsonl(text)
    text = redact_text(text, putnam)
    return text.encode("utf-8")


def stage_code(ref: str, putnam: dict[str, str]) -> list[Staged]:
    tracked = git_files(ref)
    worktree = set(git("ls-files", "-z").decode("utf-8").split("\0")) - {""}
    if set(tracked) != worktree:
        print(f"note: the index differs from {ref}: staged/removed files not yet committed are ignored: "
              + ", ".join(sorted(set(tracked) ^ worktree)[:10]))
    out = []
    for p in tracked:
        if p in EXCLUDED_CODE:
            continue
        data = transform(p, git("show", f"{ref}:{p}"), putnam)
        if p == "README.md":
            text = data.decode("utf-8")
            title, _, rest = text.partition("\n")
            data = (CODE_FRONT_MATTER + title + "\n\n" + CODE_BANNER + rest.lstrip("\n")).encode("utf-8")
        out.append(Staged(p, data=data))
    return out


def stage_index() -> list[Staged]:
    base = ROOT / "data" / "index"
    out = []
    for p in INDEX_FILES:
        src = base / p
        if not src.exists():
            raise PublishError(f"data/index/{p} is missing (build it with scripts/build_index.py).")
        out.append(Staged(p, source=src))
    card = CARDS / "formal-mas-index.md"
    out.append(Staged("README.md", data=card.read_bytes()))
    return out


def sanitize_run_text(text: str) -> str:
    """Absolute paths of this machine -> repo-relative, or `<tmp>/<file>` for temporary files."""
    root_forms = {str(ROOT), str(ROOT).replace("\\", "/"), json.dumps(str(ROOT))[1:-1]}
    for form in sorted(root_forms, key=len, reverse=True):
        for sep in ("\\\\", "\\", "/"):
            text = text.replace(form + sep, "")
        text = text.replace(form, ".")
    # anything else under a user folder (e.g. a scratch copy of the dataset): keep only the file name
    return re.sub(r"(?i)\b[A-Z]:(?:\\\\|\\|/)+(?:Users|" + USER + r")(?:\\\\|\\|/)[^\"'\s,\]]*?([^\\/\"'\s,\]]+)(?=[\"'\s,\]])",
                  r"<tmp>/\1", text)


def stage_eval(ref: str, putnam: dict[str, str]) -> list[Staged]:
    out = []
    for p in git_files(ref):
        if p.startswith(EVAL_FROM_GIT) or p in EVAL_FROM_GIT:
            out.append(Staged(p, data=transform(p, git("show", f"{ref}:{p}"), putnam)))
    for run in RUN_FOLDERS:
        folder = ROOT / "runs" / run
        if not folder.is_dir():
            raise PublishError(f"runs/{run} is missing.")
        for f in sorted(folder.iterdir()):
            if f.name not in RUN_FILES:
                raise PublishError(f"runs/{run}/{f.name}: unexpected file; add it to RUN_FILES or move it.")
            text = sanitize_run_text(f.read_text(encoding="utf-8"))
            if f.suffix == ".json":
                json.loads(text)  # still valid after sanitizing
            out.append(Staged(f"runs/{run}/{f.name}", data=transform(f.name, text.encode("utf-8"), putnam)))
    if not LEDGER.exists():
        raise PublishError("data/spend_ledger.jsonl is missing.")
    out.append(Staged("runs/spend_ledger.jsonl", data=transform("x.jsonl", LEDGER.read_bytes(), putnam)))
    out.append(Staged("README.md", data=(CARDS / "formal-mas-eval.md").read_bytes()))
    out.append(Staged("LICENSE.md", data=(CARDS / "formal-mas-eval-LICENSE.md").read_bytes()))
    return out


def _build_space_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_space", ROOT / "scripts" / "build_space.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("publish_hf", sys.modules[__name__])
    spec.loader.exec_module(mod)
    return mod


def space_files_present(ref: str | None) -> set[str]:
    """Paths under space/ (relative to it) in git at `ref`, or in the working tree if ref is None."""
    if ref is None:
        return {p.relative_to(SPACE).as_posix() for p in SPACE.rglob("*") if p.is_file()}
    return {p[len("space/"):] for p in git_files(ref) if p.startswith("space/")}


def stage_space(ref: str | None, putnam: dict[str, str]) -> list[Staged]:
    """SPACE_FILES from git at `ref` (None: the working tree) + the Space card as README.md."""
    present = space_files_present(ref)
    unexpected = sorted(present - set(SPACE_FILES))
    if unexpected:
        raise PublishError("space/ holds files outside SPACE_FILES: " + ", ".join(unexpected))
    missing = sorted(set(SPACE_FILES) - present)
    if missing:
        where = "the working tree" if ref is None else ref
        raise PublishError(f"missing in {where}: " + ", ".join("space/" + p for p in missing)
                           + " (run scripts/build_space.py, then commit)")
    out = []
    for p in SPACE_FILES:
        # the working tree may have CRLF (core.autocrlf); git and the Hub get LF
        data = (SPACE / p).read_bytes().replace(b"\r\n", b"\n") if ref is None else git("show", f"{ref}:space/{p}")
        out.append(Staged(p, data=transform(p, data, putnam)))
    built = _build_space_module().build()
    stale = sorted(s.path for s in out if s.path.startswith("data/") and built.get(s.path) != s.data)
    if stale or set(built) - {s.path for s in out}:
        raise PublishError("space/data/ is not what scripts/build_space.py builds now: "
                           + ", ".join(stale or sorted(set(built) - {s.path for s in out})))
    out.append(Staged("README.md", data=(CARDS / "formal-mas-demo.md").read_bytes()))
    return out


# -- card metadata (README.md front matter) ---------------------------------------------------
# Limits the Hub enforces on upload (a failed commit otherwise): checked before anything is sent.
SPACE_SHORT_DESCRIPTION_MAX = 60
SPACE_SDKS = {"gradio", "streamlit", "docker", "static"}
SPACE_COLORS = {"red", "yellow", "green", "blue", "indigo", "purple", "pink", "gray"}
SIZE_CATEGORIES = {"n<1K", "1K<n<10K", "10K<n<100K", "100K<n<1M", "1M<n<10M", "10M<n<100M", "100M<n<1B",
                   "1B<n<10B", "10B<n<100B", "100B<n<1T", "n>1T"}


def front_matter(text: str) -> dict | None:
    """The YAML block between the leading `---` lines of a card, or None if there is none."""
    import yaml
    m = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, flags=re.S)
    if not m:
        return None
    data = yaml.safe_load(m.group(1))
    return data if isinstance(data, dict) else {}


def check_card(staged: list[Staged], rtype: str) -> list[str]:
    """Metadata problems of the staged README.md that the Hub would reject (or misread)."""
    card = next((s for s in staged if s.path == "README.md"), None)
    if card is None:
        return ["README.md: no card staged"]
    try:
        meta = front_matter(card.data.decode("utf-8"))
    except Exception as exc:  # yaml.YAMLError and decoding errors alike
        return [f"README.md: front matter is not valid YAML ({exc})"]
    if meta is None:
        return ["README.md: no YAML front matter"]
    problems, paths = [], {s.path for s in staged}
    if not meta.get("license"):
        problems.append("README.md: `license` is missing")
    elif meta["license"] == "other":
        for key in ("license_name", "license_link"):
            if not meta.get(key):
                problems.append(f"README.md: license `other` needs `{key}`")
        link = str(meta.get("license_link") or "")
        if link and "://" not in link and link not in paths:
            problems.append(f"README.md: license_link `{link}` is not among the staged files")
    for cat in meta.get("size_categories") or []:
        if cat not in SIZE_CATEGORIES:
            problems.append(f"README.md: size_categories `{cat}` is not a Hub category")
    if rtype == "space":
        short = str(meta.get("short_description") or "")
        if len(short) > SPACE_SHORT_DESCRIPTION_MAX:
            problems.append(f"README.md: short_description has {len(short)} characters "
                            f"(the Hub allows {SPACE_SHORT_DESCRIPTION_MAX})")
        if meta.get("sdk") not in SPACE_SDKS:
            problems.append(f"README.md: sdk `{meta.get('sdk')}` is not one of {sorted(SPACE_SDKS)}")
        for key in ("colorFrom", "colorTo"):
            if key in meta and meta[key] not in SPACE_COLORS:
                problems.append(f"README.md: {key} `{meta[key]}` is not one of {sorted(SPACE_COLORS)}")
        app = meta.get("app_file") or ("index.html" if meta.get("sdk") == "static" else None)
        if app and app not in paths:
            problems.append(f"README.md: app_file `{app}` is not among the staged files")
    return problems


# -- diff and upload ------------------------------------------------------------------------

def remote_state(api, repo: str, rtype: str) -> tuple[dict[str, tuple[str, str]], str | None]:
    """path -> ("sha256"|"sha1", hash) of the remote files, and the head revision (None if no repo)."""
    from huggingface_hub.errors import RepositoryNotFoundError
    try:
        info = api.repo_info(repo, repo_type=rtype, files_metadata=True)
    except RepositoryNotFoundError:
        return {}, None
    files = {}
    for s in info.siblings:
        files[s.rfilename] = ("sha256", s.lfs.sha256) if s.lfs else ("sha1", s.blob_id)
    return files, info.sha


def diff(staged: list[Staged], remote: dict[str, tuple[str, str]]):
    added, changed, same = [], [], []
    for s in staged:
        if s.path not in remote:
            added.append(s)
            continue
        kind, h = remote[s.path]
        mine = s.sha256() if kind == "sha256" else s.git_sha1()
        (same if mine == h else changed).append(s)
    names = {s.path for s in staged}
    remote_only = sorted(p for p in remote if p not in names)
    return added, changed, same, remote_only


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def write_manifest(repo: str, revision: str, staged: list[Staged]) -> Path:
    files = {s.path: {"size": s.size(), "sha256": s.sha256()} for s in staged if s.path != "README.md"}
    path = ROOT / "data" / "index_manifest.json"
    path.write_text(json.dumps({"repo_id": repo, "revision": revision, "files": files}, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish code, index or eval set + runs to the Hugging Face Hub.")
    ap.add_argument("target", choices=sorted(REPOS))
    ap.add_argument("--ref", default="HEAD", help="git revision to publish from (code, eval)")
    ap.add_argument("--upload", action="store_true", help="upload after the dry run (asks first)")
    ap.add_argument("--yes", action="store_true", help="with --upload: do not ask")
    ap.add_argument("--prune", action="store_true", help="also delete remote files that are not staged")
    ap.add_argument("--message", help="commit message on the Hub")
    ap.add_argument("--list", action="store_true", help="print every staged file")
    ap.add_argument("--worktree", action="store_true", help="space: take space/ from the working tree, not git")
    a = ap.parse_args(argv)
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

    if a.worktree and (a.upload or a.target != "space"):
        ap.error("--worktree is a preview of the space target only; uploads come from git (--ref)")
    repo, rtype = REPOS[a.target]
    putnam = putnam_informal()
    try:
        if a.target == "code":
            staged = stage_code(a.ref, putnam)
        elif a.target == "index":
            staged = stage_index()
        elif a.target == "space":
            staged = stage_space(None if a.worktree else a.ref, putnam)
        else:
            staged = stage_eval(a.ref, putnam)
    except PublishError as exc:
        print(f"ERROR: {exc}")
        return 1
    problems = scan(staged, putnam)
    if problems:
        print("ERROR: the scan found content that must not be published:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"scan: clean ({len(staged)} files; secrets, user paths, e-mails, PutnamBench text)")
    card_problems = check_card(staged, rtype)
    if card_problems:
        print("ERROR: the card metadata would be rejected by the Hub:")
        for p in card_problems:
            print("  " + p)
        return 1
    print("card: metadata within the Hub limits")

    api = HfApi()
    remote, head = remote_state(api, repo, rtype)
    added, changed, same, remote_only = diff(staged, remote)
    kind = {"dataset": "dataset", "space": "static Space"}.get(rtype, "model")
    print(f"\n{repo} ({kind}; {'new repository' if head is None else 'head ' + head[:12]})")
    print(f"staged {len(staged)} files, {human(sum(s.size() for s in staged))}")
    if a.list:
        for s in staged:
            print(f"    {human(s.size()):>10}  {s.path}")
    for label, items in (("added", added), ("changed", changed)):
        print(f"{label}: {len(items)} ({human(sum(s.size() for s in items))})")
        for s in items:
            print(f"  {'+' if label == 'added' else '~'} {s.path}  ({human(s.size())})")
    print(f"unchanged: {len(same)}")
    deletable = [p for p in remote_only if p not in KEEP_REMOTE]
    if remote_only:
        print(f"remote only: {len(remote_only)}" + ("" if a.prune else " (kept; --prune deletes them)"))
        for p in remote_only:
            print(f"  {'-' if a.prune and p in deletable else '='} {p}")
    ops = [CommitOperationAdd(s.path, s.data if s.data is not None else str(s.source)) for s in added + changed]
    if a.prune:
        ops += [CommitOperationDelete(p) for p in deletable]
    if not a.upload:
        print("\ndry run: nothing uploaded (add --upload).")
        return 0
    if not ops:
        print("\nnothing to upload.")
        if a.target == "index" and head:
            print(f"wrote {write_manifest(repo, head, staged).relative_to(ROOT)}")
        return 0
    if not a.yes:
        answer = input(f"\nUpload {len(ops)} operations to {repo}? Type the repo id to confirm: ")
        if answer.strip() != repo:
            print("cancelled.")
            return 1
    if head is None:  # a Space is created static; no secret or variable is ever set on it
        api.create_repo(repo, repo_type=rtype, exist_ok=True,
                        **({"space_sdk": "static"} if rtype == "space" else {}))
    msg = a.message or {"code": f"Sync code from git {git('rev-parse', '--short', a.ref).decode().strip()}",
                        "index": "Retrieval index (data/index/)",
                        "eval": "Run logs used by the analysis tables",
                        "space": "Static demo page (scripts/build_space.py)"}[a.target]
    info = api.create_commit(repo, operations=ops, commit_message=msg, repo_type=rtype)
    print(f"uploaded: {info.commit_url}")
    if a.target == "index":
        print(f"wrote {write_manifest(repo, info.oid, staged).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
