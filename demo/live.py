"""Live mode: solve one problem with the frozen C5 configuration and stream its trace.

Local only (needs .env, lean_project/ and lean_repl/). Nothing here changes the method: a
solve is `scripts/run_eval.py --config C5` on a one-problem data file, in this process, so
the run lands in runs/<UTC time>_C5_<profile>_demo/ like any other (run_config.json,
trace.jsonl, results.jsonl). The only differences are operational:

- the Lean REPL (Mathlib imported once, ~33 s warm) and the Retriever are loaded in the
  background when the app starts and shared by every solve (`_Shared` keeps run_eval's
  `runner.close()` from killing the REPL);
- `--run-usd 0.20`: the existing spend guard (llm/spend.py) caps each demo run, i.e. each
  problem, at DEMO_RUN_USD; b_max is untouched;
- the one-problem input file is kept in runs/demo_inputs/ for reproducibility.

Statements typed by the user are written by the user, never by an LLM (CLAUDE.md §1.3); they
are parsed by lean/statement.py and must compile with `sorry` before any LLM call.
Heavy imports (formal_mas, torch, faiss) happen inside functions, so importing this module
is cheap and the replay-only Space never needs them.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from timeline import render, summary

ROOT = Path(__file__).resolve().parents[1]
DEMO_RUN_USD = 0.20
PROFILES = ("dev", "experiment")
POLL_S = 0.4
# the ready check: a proof Lean must accept (base imports, no sorry, standard axioms only)
CHECK_PROOF = "import Mathlib\n\ntheorem demo_ready_check : (2 : ℕ) + 2 = 4 := by norm_num\n"
LOAD_STEPS = ("loading_lean", "loading_retriever", "checking")  # the "step X of 3" of the header

__all__ = ["DEMO_RUN_USD", "LiveEngine", "PROFILES", "live_available"]


def live_available(root: Path = ROOT, env=None) -> tuple[bool, str]:
    """(shown, reason). Hidden with DEMO_MODE=replay, without .env, or without Lean built."""
    env = os.environ if env is None else env
    if str(env.get("DEMO_MODE", "")).strip().lower() == "replay":
        return False, "DEMO_MODE=replay"
    if not (root / ".env").is_file():
        return False, "no .env file"
    exe = root / "lean_repl" / ".lake" / "build" / "bin" / ("repl.exe" if sys.platform == "win32" else "repl")
    if not (root / "lean_project").is_dir() or not exe.is_file():
        return False, "Lean is not built (lean_project/ or lean_repl/)"
    if not (root / "src" / "formal_mas").is_dir():
        return False, "source code not found"
    return True, ""


def _src_on_path(root: Path) -> None:
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


class _Shared:
    """The app's LeanRunner as seen by one run: everything delegated except close()."""

    def __init__(self, runner):
        self._runner = runner

    def close(self) -> None:  # the REPL outlives the run
        pass

    def __getattr__(self, name):
        return getattr(self._runner, name)


def _read_new(path: Path, offset: int) -> tuple[list[dict], int]:
    """Complete JSON lines of `path` after byte `offset` (a line still being written waits)."""
    if not path.is_file():
        return [], offset
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    events = []
    for line in data[:end].splitlines():
        try:
            events.append(json.loads(line.decode("utf-8")))
        except ValueError:
            continue
    return events, offset + end + 1


class LiveEngine:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.state = "not_started"  # not_started | loading_lean | loading_retriever | ready | failed
        self.detail = ""
        self.started_at: float | None = None
        self.load_s: float | None = None
        self.runner = None
        self.retriever = None
        self._start_lock = threading.Lock()
        self._run_lock = threading.Lock()

    # -- background loading ---------------------------------------------------------

    def start(self) -> None:
        """Load Lean (REPL + Mathlib) and the Retriever in a background thread; idempotent."""
        with self._start_lock:
            if self.state != "not_started":
                return
            self.state = "loading_lean"
            self.started_at = time.perf_counter()
        threading.Thread(target=self._load, name="demo-live-load", daemon=True).start()

    def _load(self) -> None:
        try:
            _src_on_path(self.root)
            from formal_mas.lean.runner import LeanRunner

            runner = LeanRunner()
            if not runner.ensure_available():
                self.state, self.detail = "failed", f"Lean did not load Mathlib: {runner.warnings[-3:]}"
                return
            self.runner = runner
            self.state = "loading_retriever"
            from formal_mas.retrieval.retriever import Retriever

            self.retriever = Retriever()
            self.detail = f"retriever mode {self.retriever.mode}"
            self.state = "checking"
            from formal_mas.lean.runner import accepted

            # step 3: one tiny proof through the same runner, so "ready" means a proof was just checked
            ok, why = accepted(runner.verify(CHECK_PROOF, "demo_ready_check"))
            if not ok:
                self.state, self.detail = "failed", f"the verifier rejected a known proof ({why})"
                return
            self.state = "ready"
        except Exception as exc:  # noqa: BLE001 — shown in the status line, never raised into the UI
            self.state, self.detail = "failed", repr(exc)[:500]
        finally:
            self.load_s = time.perf_counter() - (self.started_at or time.perf_counter())

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def status_markdown(self) -> str:
        waited = time.perf_counter() - self.started_at if self.started_at else 0.0
        return {
            "not_started": "⚪ Lean not started",
            "loading_lean": f"🟡 **Loading Lean…** (REPL + `import Mathlib`, {waited:.0f} s; ~30 s warm, longer on a cold disk)",
            "loading_retriever": f"🟡 **Loading the Retriever…** (Mathlib index + embeddings, {waited:.0f} s)",
            "checking": "🟡 **Checking the verifier…** (one known proof)",
            "ready": f"🟢 **Lean ready** (loaded in {self.load_s or 0:.0f} s; {self.detail})",
            "failed": f"🔴 **Lean failed to load**: {self.detail}",
        }[self.state]

    def profile_label(self, profile: str) -> str:
        """`profile — model keys` read from config/models.yaml (models are named only there)."""
        try:
            _src_on_path(self.root)
            from formal_mas.config import load_models

            cfg = load_models(self.root / "config")
            roles = ((cfg.get("profiles") or {}).get(profile) or {}).get("roles") or {}
            models = sorted({str(r["model"]) for r in roles.values() if isinstance(r, dict) and r.get("model")})
            return f"{profile} — {', '.join(models)}" if models else profile
        except Exception:  # noqa: BLE001 — a label only
            return profile

    def profile_models(self, profile: str) -> tuple[str, str]:
        """(provider display name, model keys) of a profile, from config/models.yaml."""
        try:
            _src_on_path(self.root)
            from formal_mas.config import load_models

            cfg = load_models(self.root / "config")
            roles = ((cfg.get("profiles") or {}).get(profile) or {}).get("roles") or {}
            keys = sorted({str(r["model"]) for r in roles.values() if isinstance(r, dict) and r.get("model")})
            providers = cfg.get("providers") or {}
            names = sorted({str((providers.get((cfg.get("models") or {}).get(k, {}).get("provider")) or {})
                                .get("display_name") or (cfg.get("models") or {}).get(k, {}).get("provider") or k)
                            for k in keys})
            return " / ".join(names) or profile, ", ".join(keys) or profile
        except Exception:  # noqa: BLE001 — a label only
            return profile, profile

    # -- problems ------------------------------------------------------------------

    def dataset(self) -> list[dict]:
        path = self.root / "data" / "eval.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        order = {"easy": 0, "medium": 1, "hard": 2}
        return sorted(rows, key=lambda r: (r.get("split") != "dev", order.get(r.get("difficulty"), 3), r["id"]))

    def custom_problem(self, text: str) -> tuple[dict | None, str]:
        """(problem, error). The statement must parse and compile with `sorry` (no LLM call)."""
        _src_on_path(self.root)
        from formal_mas.lean.runner import compiles
        from formal_mas.lean.statement import parse_theorem

        text = (text or "").strip()
        if not text:
            return None, "Paste a Lean 4 theorem ending in `:= by sorry`."
        try:
            # a bare `theorem …` gets the default header; text with its own prelude keeps it as is
            first = parse_theorem(text)
            statement = (first.header + text if text.startswith(("theorem", "lemma")) else text) + "\n"
            thm = parse_theorem(statement)  # exactly what the pipeline will parse (graph.py)
        except ValueError as exc:
            return None, f"Could not parse the statement: {exc}"
        if not self.ready:
            return None, "Lean is not ready yet."
        res = self.runner.verify(thm.header + thm.declaration() + " := by\n  sorry\n", thm.name)
        if not compiles(res):
            errs = "\n".join(f"line {m.line}: {m.text}" for m in res.errors[:3]) or res.status
            return None, f"The statement does not compile with `sorry`:\n```text\n{errs}\n```"
        return {"id": thm.name, "source": "typed by the user in the demo", "license": None, "informal": "",
                "statement": statement,
                "split": "custom", "area": None, "difficulty": None}, ""

    # -- solving -------------------------------------------------------------------

    def _run_eval(self):
        spec = importlib.util.spec_from_file_location("formal_mas_run_eval", self.root / "scripts" / "run_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def solve(self, problem: dict, profile: str) -> Iterator[tuple[str, str]]:
        """Yield (timeline, summary) Markdown while C5 runs on `problem`; the run goes to runs/."""
        header = f"Profile `{profile}` · configuration C5 (frozen) · spend cap ${DEMO_RUN_USD:.2f} per problem\n\n"
        for f in self.stream(problem, profile):
            if f["refused"]:
                yield "", f["message"]
            elif not f["done"]:
                yield render(f["events"]), (header + "_running…_") if f["events"] else f["message"]
            else:
                tail = f"\n\n⚠ The run stopped with an error: `{f['error']}`" if f["error"] else ""
                if f["run_dir"]:
                    tail += f"\n\nRun folder: `{f['run_dir']}`"
                if f["row"] is None and not f["error"]:
                    tail += "\n\nNo result row was written (see the run's trace.jsonl and console)."
                yield render(f["events"], f["row"]), header + summary(f["row"], f["events"]) + tail

    def stream(self, problem: dict, profile: str) -> Iterator[dict]:
        """Yield frames {events, row, done, error, run_dir, refused, reason, message} while C5 runs.
        `refused` frames (reason not_ready | bad_profile | busy) come alone, before any run."""
        def refused(reason: str, message: str) -> dict:
            return {"events": [], "row": None, "done": True, "error": None, "run_dir": None,
                    "refused": True, "reason": reason, "message": message}

        if not self.ready:
            yield refused("not_ready", f"Lean is not ready: {self.status_markdown()}")
            return
        if profile not in PROFILES:
            yield refused("bad_profile", f"Unknown profile {profile!r}")
            return
        if not self._run_lock.acquire(blocking=False):
            yield refused("busy", "Another problem is being solved; wait for it to finish.")
            return
        # The lock lasts as long as the run, not the UI generator: if the browser disconnects the
        # generator is closed but run_eval keeps going (bounded by b_max and the spend cap), and no
        # second run may start beside it (shared logger, MODEL_PROFILE, root handlers).
        started = False
        try:
            gen = self._frames(problem, profile)
            first = next(gen)  # runs up to thread.start(); from then on the run thread owns the lock
            started = True
            yield first
            yield from gen
        except StopIteration:
            started = True
        finally:
            if not started:
                self._run_lock.release()

    def _frames(self, problem: dict, profile: str) -> Iterator[dict]:
        inputs = self.root / "runs" / "demo_inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        data = inputs / f"{stamp}_{re.sub(r'[^A-Za-z0-9_.-]', '_', str(problem['id']))}.jsonl"
        data.write_text(json.dumps(problem, ensure_ascii=False) + "\n", encoding="utf-8")
        argv = ["--config", "C5", "--data", str(data), "--split", "all", "--run-name", "demo",
                "--run-usd", f"{DEMO_RUN_USD:.2f}"]
        box: dict = {}
        run_eval = self._run_eval()

        def on_run_dir(d) -> None:
            box.setdefault("dir", Path(d))
            # run_config.json records the app's sys.argv; this file records the demo's real call
            (Path(d) / "demo_call.json").write_text(json.dumps(
                {"script": "scripts/run_eval.py", "argv": argv, "profile": profile, "caller": "demo/app.py"},
                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

        def target() -> None:
            old_profile = os.environ.get("MODEL_PROFILE")
            handlers = list(logging.getLogger().handlers)
            os.environ["MODEL_PROFILE"] = profile  # read by run_eval through llm/specs.profile_name
            try:
                box["rc"] = run_eval.main(argv, runner_factory=lambda: _Shared(self.runner),
                                          retriever_factory=lambda: self.retriever,
                                          on_run_dir=on_run_dir)
            except BaseException as exc:  # noqa: BLE001 — argparse exits, unexpected errors: shown, not raised
                box["error"] = repr(exc)[:500]
            finally:
                if old_profile is None:
                    os.environ.pop("MODEL_PROFILE", None)
                else:
                    os.environ["MODEL_PROFILE"] = old_profile
                root = logging.getLogger()
                for h in list(root.handlers):  # run_eval adds a console handler per call
                    if h not in handlers:
                        root.removeHandler(h)
                self._run_lock.release()

        def frame(events, row=None, done=False) -> dict:
            d = box.get("dir")
            shown = None
            if d is not None:
                shown = (d.relative_to(self.root) if d.is_relative_to(self.root) else d).as_posix()
            return {"events": list(events), "row": row, "done": done, "error": box.get("error") if done else None,
                    "run_dir": shown, "refused": False, "reason": None,
                    "message": f"Starting C5 on `{problem['id']}` with profile `{profile}`…"}

        thread = threading.Thread(target=target, name="demo-live-run", daemon=True)
        thread.start()
        yield frame([])
        events: list[dict] = []
        offset = 0
        while True:
            alive = thread.is_alive()
            run_dir = box.get("dir")
            if run_dir is not None:
                new, offset = _read_new(run_dir / "trace.jsonl", offset)
                if new:
                    events += new
                    yield frame(events)
            if not alive:
                break
            time.sleep(POLL_S)
        row = None
        if box.get("dir") is not None and (box["dir"] / "results.jsonl").is_file():
            lines = [ln for ln in (box["dir"] / "results.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            row = json.loads(lines[-1]) if lines else None
        yield frame(events, row, done=True)
