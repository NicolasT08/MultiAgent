"""One-command installation of formal-mas on Windows, Linux or macOS.

Run it from the repository root with the project's Python 3.12 environment active:

    python scripts/setup.py                 # every step, in order
    python scripts/setup.py --skip lean     # skip some steps (lean, repl, index, smoke)
    python scripts/setup.py --only smoke    # run only some steps

Steps (each one can be re-run safely; finished work is detected and skipped):

  1. checks   Python 3.12, the Python packages of requirements.txt, git, and elan/lake
              (if elan is missing, it prints how to install it for your OS and stops).
  2. lean     `lake exe cache get` + `lake build` in lean_project/ (versions pinned by
              lean_project/lean-toolchain and lake-manifest.json); checks the Mathlib commit.
  3. repl     clones leanprover-community/repl into lean_repl/ at the tag and commit pinned in
              scripts/build_repl.sh, checks them and runs `lake build repl`.
  4. index    downloads the retrieval index (data/index/) from the Hugging Face dataset and
              revision pinned in data/index_manifest.json, and checks every file's SHA-256.
  5. smoke    one Lean verification through LeanRunner and one Retriever search.

The bash scripts scripts/setup_lean.sh and scripts/build_repl.sh do steps 1-3 by hand.
Nothing is deleted: a step that finds unexpected local state stops with a message instead.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # formal_mas without `pip install -e .`, like the other scripts
LEAN_PROJECT = ROOT / "lean_project"
REPL_DIR = ROOT / "lean_repl"
INDEX_DIR = ROOT / "data" / "index"
MANIFEST = ROOT / "data" / "index_manifest.json"
BUILD_REPL_SH = ROOT / "scripts" / "build_repl.sh"
STEPS = ["checks", "lean", "repl", "index", "smoke"]
PYTHON = (3, 12)
# import name -> pip name, for the packages the system needs at run time
PACKAGES = {"yaml": "pyyaml", "pydantic": "pydantic", "dotenv": "python-dotenv", "pytest": "pytest",
            "numpy": "numpy", "scipy": "scipy", "torch": "torch", "faiss": "faiss-cpu",
            "sentence_transformers": "sentence-transformers", "transformers": "transformers",
            "huggingface_hub": "huggingface_hub", "psutil": "psutil", "openai": "openai",
            "langgraph": "langgraph", "matplotlib": "matplotlib"}
ELAN_BIN = Path.home() / ".elan" / "bin"


class SetupError(Exception):
    """A step cannot continue; the message says what is missing and how to fix it."""


def say(msg: str) -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    say(f"\n=== {title} ===")


def run(cmd: list[str], cwd: Path, what: str) -> None:
    """Run a command with its output streamed to the console; SetupError if it fails."""
    say(f"$ {' '.join(cmd)}   (in {cwd.relative_to(ROOT) if cwd != ROOT else '.'})")
    try:
        code = subprocess.call(cmd, cwd=cwd)
    except OSError as exc:
        raise SetupError(f"{what}: could not start `{cmd[0]}` ({exc}).") from exc
    if code != 0:
        raise SetupError(f"{what} failed (exit code {code}). Read the output above; you can run this "
                         f"script again after fixing it (finished work is kept).")


def capture(cmd: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def find_tool(name: str) -> str | None:
    """A tool on PATH, or in ~/.elan/bin (a fresh elan install is only on PATH in a new terminal)."""
    found = shutil.which(name)
    if found:
        return found
    for cand in (ELAN_BIN / name, ELAN_BIN / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    return None


def elan_instructions() -> str:
    system = platform.system()
    if system == "Windows":
        how = ("In PowerShell:\n"
               "    curl.exe -O --location https://raw.githubusercontent.com/leanprover/elan/master/elan-init.ps1\n"
               "    powershell -ExecutionPolicy Bypass -f elan-init.ps1 -NoPrompt $true -DefaultToolchain none\n"
               "    del elan-init.ps1")
    else:
        how = ("In a terminal:\n"
               "    curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf "
               "| sh -s -- -y --default-toolchain none")
    return (f"elan (the Lean version manager) is not installed.\n{how}\n"
            "Then open a NEW terminal (so that ~/.elan/bin is on PATH) and run this script again.\n"
            "elan downloads the exact Lean version of lean_project/lean-toolchain by itself.")


# -- 1. checks -------------------------------------------------------------------------------

def check_environment() -> None:
    step("1/5 checks")
    v = sys.version_info
    if (v.major, v.minor) != PYTHON:
        raise SetupError(
            f"Python {v.major}.{v.minor} is running ({sys.executable}); this project needs Python "
            f"{PYTHON[0]}.{PYTHON[1]}.\nCreate the environment with it, e.g. `uv venv --python 3.12 .venv` "
            "(or `py -3.12 -m venv .venv` on Windows, `python3.12 -m venv .venv` elsewhere), activate it "
            "and run this script with that environment's python.")
    say(f"ok  Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    if sys.prefix == sys.base_prefix:
        say("note: no virtual environment is active; packages are taken from the system Python.")

    missing = []
    for mod, pip_name in PACKAGES.items():
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001 — any import failure means "not usable"
            missing.append(pip_name)
    if missing:
        raise SetupError(
            "Missing Python packages: " + ", ".join(missing) + ".\nInstall them in this environment:\n"
            "    pip install torch --index-url https://download.pytorch.org/whl/cpu   (CPU-only torch)\n"
            "    pip install -r requirements.txt\n"
            "    pip install -e .\n"
            "(with uv: prefix each command with `uv`).")
    say("ok  Python packages of requirements.txt")
    if os.name == "nt" and os.environ.get("PYTHONUTF8") != "1":
        say("note: set PYTHONUTF8=1 so the console prints Lean symbols (ℝ, ⊢, ≤); see README §4.")

    if not shutil.which("git"):
        raise SetupError("git is not installed (needed by lake and to clone the Lean REPL). "
                         "Install it from https://git-scm.com/downloads and run this script again.")
    say("ok  git")

    elan = find_tool("elan")
    if not elan:
        raise SetupError(elan_instructions())
    lake = find_tool("lake")
    if not lake:
        raise SetupError("elan is installed but `lake` was not found in ~/.elan/bin. "
                         "Run `elan self update` and open a new terminal.")
    say(f"ok  elan ({capture([elan, '--version'], ROOT) or elan})")
    if not shutil.which("lake"):
        raise SetupError(f"lake exists ({lake}) but is not on PATH, and the Lean runner calls `lake env`.\n"
                         f"Add {ELAN_BIN} to PATH (a new terminal usually does it after installing elan) "
                         "and run this script again.")
    say("ok  lake on PATH")


# -- 2. lean_project -------------------------------------------------------------------------

def manifest_mathlib_rev() -> str:
    data = json.loads((LEAN_PROJECT / "lake-manifest.json").read_text(encoding="utf-8"))
    for pkg in data.get("packages", []):
        if pkg.get("name") == "mathlib":
            return pkg["rev"]
    raise SetupError("lean_project/lake-manifest.json has no `mathlib` package; restore it from the repository.")


def setup_lean() -> None:
    step("2/5 lean_project (Lean 4 + Mathlib, ~6 GB)")
    for f in ("lean-toolchain", "lakefile.toml", "lake-manifest.json"):
        if not (LEAN_PROJECT / f).exists():
            raise SetupError(f"lean_project/{f} is missing; restore lean_project/ from the repository "
                             "(it pins the Lean version and the Mathlib commit).")
    toolchain = (LEAN_PROJECT / "lean-toolchain").read_text(encoding="utf-8").strip()
    rev = manifest_mathlib_rev()
    say(f"pinned: {toolchain}, Mathlib {rev}")
    # The first lake call installs the toolchain through elan if it is not there yet.
    run(["lake", "exe", "cache", "get"], LEAN_PROJECT, "Downloading the precompiled Mathlib cache")
    run(["lake", "build"], LEAN_PROJECT, "Building lean_project")
    got = capture(["git", "rev-parse", "HEAD"], LEAN_PROJECT / ".lake" / "packages" / "mathlib")
    if got != rev:
        raise SetupError(f"lean_project/.lake/packages/mathlib is at {got}, but lake-manifest.json pins {rev}.\n"
                         "Run `lake update` only if you mean to change versions; otherwise delete "
                         "lean_project/.lake/packages/mathlib and run this script again.")
    say(f"ok  Mathlib at {rev}; {capture(['lean', '--version'], LEAN_PROJECT) or toolchain}")


# -- 3. lean_repl ----------------------------------------------------------------------------

def repl_pins() -> tuple[str, str, str]:
    """REPL_URL, REPL_TAG and REPL_COMMIT, read from scripts/build_repl.sh (single source)."""
    text = BUILD_REPL_SH.read_text(encoding="utf-8")
    vals = {}
    for key in ("REPL_URL", "REPL_TAG", "REPL_COMMIT"):
        m = re.search(rf'^{key}="([^"]+)"', text, re.M)
        if not m:
            raise SetupError(f"{key} not found in scripts/build_repl.sh.")
        vals[key] = m.group(1)
    return vals["REPL_URL"], vals["REPL_TAG"], vals["REPL_COMMIT"]


def setup_repl() -> None:
    step("3/5 lean_repl (Lean REPL, ~1 min)")
    url, tag, commit = repl_pins()
    say(f"pinned: {url} {tag} ({commit})")
    if not (REPL_DIR / ".git").exists():
        if REPL_DIR.exists() and any(REPL_DIR.iterdir()):
            raise SetupError("lean_repl/ exists but is not a git clone; move it away and run this script again.")
        partial = ROOT / "lean_repl.partial"
        if partial.exists():
            shutil.rmtree(partial)  # a clone this script started and that was cut off
        run(["git", "clone", "--quiet", "--depth", "1", "--branch", tag, url, str(partial)], ROOT,
            "Cloning the Lean REPL")
        if REPL_DIR.exists():
            REPL_DIR.rmdir()
        partial.rename(REPL_DIR)
    got = capture(["git", "rev-parse", "HEAD"], REPL_DIR)
    if got != commit:
        raise SetupError(f"lean_repl/ is at {got}, expected {commit} ({tag}). "
                         "Move lean_repl/ away and run this script again.")
    if capture(["git", "status", "--porcelain", "--untracked-files=no"], REPL_DIR):
        raise SetupError("lean_repl/ has local changes; move it away and run this script again.")
    project_tc = (LEAN_PROJECT / "lean-toolchain").read_text(encoding="utf-8").strip()
    repl_tc = (REPL_DIR / "lean-toolchain").read_text(encoding="utf-8").strip()
    if project_tc != repl_tc:
        raise SetupError(f"REPL toolchain {repl_tc} != lean_project toolchain {project_tc}.")
    run(["lake", "build", "repl"], REPL_DIR, "Building the REPL")
    say(f"ok  REPL {tag} ({commit}) in lean_repl/.lake/build/bin/")


# -- 4. retrieval index ----------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def setup_index() -> None:
    step("4/5 retrieval index (data/index/, ~880 MB)")
    if not MANIFEST.exists():
        raise SetupError("data/index_manifest.json is missing; restore it from the repository.")
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    repo, rev, files = man["repo_id"], man["revision"], man["files"]
    say(f"source: https://huggingface.co/datasets/{repo} (revision {rev[:12]}), {len(files)} files")
    from huggingface_hub import hf_hub_download

    for name, info in files.items():
        dest = INDEX_DIR / name
        if dest.exists() and dest.stat().st_size == info["size"] and sha256(dest) == info["sha256"]:
            say(f"ok  {name} (already present)")
            continue
        if dest.exists():
            raise SetupError(f"data/index/{name} exists but its SHA-256 differs from the manifest.\n"
                             "It may be a locally rebuilt index; move data/index/ away (it is not deleted "
                             "by this script) and run it again to download the published one.")
        say(f"..  downloading {name} ({info['size'] / 1e6:.1f} MB)")
        for attempt in range(1, 4):
            try:
                hf_hub_download(repo, name, repo_type="dataset", revision=rev, local_dir=INDEX_DIR)
                break
            except Exception as exc:  # noqa: BLE001 — network errors: retry, then a clear message
                if attempt == 3:
                    raise SetupError(f"Could not download {name} from {repo}: {exc}\n"
                                     "Check the internet connection and run this script again "
                                     "(files already downloaded are kept).") from exc
                say(f"    retry {attempt}/2 after: {exc}")
                time.sleep(5 * attempt)
        got = sha256(dest)
        if got != info["sha256"]:
            dest.unlink()
            raise SetupError(f"{name}: SHA-256 {got} does not match the manifest ({info['sha256']}); "
                             "the file was removed. Run this script again.")
        say(f"ok  {name} (SHA-256 verified)")


# -- 5. smoke tests --------------------------------------------------------------------------

SMOKE_LEAN = "import Mathlib\n\ntheorem setup_smoke (x : ℝ) : 0 ≤ x ^ 2 := sq_nonneg x\n"
SMOKE_QUERY = "(a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2"


def smoke() -> None:
    step("5/5 smoke tests")
    from formal_mas.lean.runner import LeanRunner, accepted
    from formal_mas.retrieval.retriever import Retriever

    say("Lean: starting the REPL and loading Mathlib (~30 s on an SSD; can take ~30 min the first time "
        "on a hard disk)...")
    t0 = time.time()
    with LeanRunner() as lean:
        res = lean.verify(SMOKE_LEAN, theorem="setup_smoke")
        ok, why = accepted(res)
        backend = res.backend
    if not ok:
        raise SetupError(f"The Lean smoke proof was not accepted ({why}; status {res.status}, "
                         f"backend {backend}, detail: {res.detail}). Re-run steps lean and repl.")
    say(f"ok  Lean accepted the smoke proof via the {backend} back-end in {time.time() - t0:.0f} s; "
        f"axioms {res.axioms}")
    if backend != "repl":
        say("note: the REPL could not be used; checks fall back to `lake env lean` (~30 s each).")

    say("Retriever: loading the indexes and the embedding model (the model, ~440 MB, is downloaded "
        "from Hugging Face the first time)...")
    t0 = time.time()
    r = Retriever()
    hits = r.retrieve(SMOKE_QUERY, k=5)
    if r.mode != "hybrid" or not hits:
        raise SetupError(f"The Retriever is not fully working (mode {r.mode!r}, {len(hits)} hits); "
                         "re-run the index step.")
    say(f"ok  Retriever mode {r.mode} in {time.time() - t0:.0f} s; top hits for `{SMOKE_QUERY}`:")
    for h in hits[:3]:
        say(f"      {h.score:.3f}  {h.name}")


# --------------------------------------------------------------------------------------------

ACTIONS = {"checks": check_environment, "lean": setup_lean, "repl": setup_repl, "index": setup_index,
           "smoke": smoke}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install formal-mas (Lean 4 + Mathlib, REPL, index) and test it.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--only", nargs="+", choices=STEPS, help="run only these steps")
    g.add_argument("--skip", nargs="+", choices=STEPS, default=[], help="skip these steps")
    a = ap.parse_args(argv)
    steps = a.only or [s for s in STEPS if s not in a.skip]
    if "checks" not in steps and any(s in steps for s in ("lean", "repl")):
        steps = ["checks", *steps]  # lean/repl need the checks of elan and lake
    os.chdir(ROOT)
    try:
        for s in STEPS:
            if s in steps:
                ACTIONS[s]()
    except SetupError as exc:
        say(f"\nERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        say("\nInterrupted. Run the script again to continue; finished steps are detected and skipped.")
        return 130
    say("\nAll requested steps finished. Next: copy .env.example to .env and add your API keys "
        "(README §4), then run `pytest`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
