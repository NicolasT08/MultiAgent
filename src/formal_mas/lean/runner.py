"""Lean runner: compile Lean code, parse the messages and decide acceptance (Eq. 2).

Every proof check of the system goes through `LeanRunner.verify` (CLAUDE.md §7).

Back-end (config/pipeline.yaml → lean, chosen 2026-10-25 in PLAN.md Phase 2):
    repl  The Lean REPL (leanprover-community/repl, tag v4.36.0-rc1, built by
          scripts/build_repl.sh) runs under `lake env` in lean_project/. The base imports
          are loaded once as environment 0 (~33 s warm); each verification is a command
          with `"env": 0` (~0.01-0.5 s), so checks never see each other's declarations.
    file  `lake env lean --json` on a temporary file (~32 s per check: Mathlib is loaded
          every time). Used when `backend: file`, when the REPL cannot start (logged as a
          `lean_runner_warning`), and for code whose imports do not fit the REPL's env 0
          (they must include `base_imports[0]`, i.e. Mathlib, and be within `base_imports`).
          Its wall-clock limit is `timeout_s` + the import time, measured once per set of
          imports the first time the file back-end is used. Any file verification marks
          the run as degraded (`run_info()["degraded"]`, for run_config.json).

Failure handling (REPL), never raising to the caller:
    hang         no reply within `timeout_s` → kill the process tree, status `timeout`
                 (final: the check is not retried with the file back-end)
    memory       the process tree exceeds `kill_memory_mb` during a command → kill,
                 status `memory_limit` (the file back-end uses Lean's own `--memory`)
    crash        the REPL exits, answers with a protocol error or out of sequence → `crash`
    preventive   after `restart_every` commands, or above `restart_memory_mb` after a
                 command (the REPL never frees old environments) → kill
A killed REPL is started again automatically before the next verification; if it cannot
start, the runner switches to the file back-end for the rest of its life.

Start-up check (2026-11-03): after `import <base>` the REPL must also answer `#check @<probe>`
for every base import (`lean.probes`: `Real.sqrt` for Mathlib, `Aesop.BuiltinRules.not_intro`
for Aesop) with no error, at start and at every restart; otherwise it is restarted once, then
the file back-end is used, whose import measurement runs the same probes. `ensure_available()`
is False when neither back-end passes: run_eval stops before any LLM call.

Message positions are those of the full file: the import lines are blanked (not removed)
before the code is sent to the REPL.

Checks appended by the runner after the code (`with_checks`), read only from their own
line and only when they answer exactly once:
    #print axioms <theorem>   → `LeanResult.axioms`
    #eval … constants.map₂    → `LeanResult.declarations`: every declaration the code added
`accepted` requires both. Code that metaprograms Lean (`macro`, `elab`, `run_cmd`, `#eval`,
`set_option debug.*`, `unsafe`…) could still forge these answers or skip the kernel, so
generated code must pass lean/sanitize.py before it reaches `verify`.

Identical verifications (same code, theorem and timeout) are answered from an in-memory
LRU cache (`cache_size`); only `finished` results are cached.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

from formal_mas.config import ROOT, load_pipeline

LOG = logging.getLogger(__name__)

ALLOWED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
SORRY_WARNING = "declaration uses `sorry`"
STATUSES = ("finished", "timeout", "memory_limit", "crash")
DEFAULTS = {
    "backend": "repl",
    "project_dir": "lean_project",
    "repl_dir": "lean_repl",
    "base_imports": ["Mathlib"],
    "timeout_s": 60.0,
    "startup_timeout_s": 2400.0,
    "restart_every": 500,
    "restart_memory_mb": 6000.0,
    "kill_memory_mb": 12000.0,
    "memory_poll_s": 0.5,
    "cache_size": 4096,
    # One declaration per base import, `#check`ed right after the import (REPL start, every
    # restart, and the file back-end's import measurement). Added 2026-11-03: a REPL answered
    # `import Mathlib` with an env and no error, yet without Mathlib (10.7 s, 518 MB;
    # runs/20261103T143337Z_C5_dev_precheck_r1: `unknown namespace BigOperators`).
    # A module maps to one name or a list (pipeline.yaml → lean.probes overrides this).
    "probes": {"Mathlib": ["Real.sqrt", "Finset.sum_range_succ"], "Aesop": "Aesop.BuiltinRules.not_intro"},
}
DECLS_MARKER = "FORMAL_MAS_DECLS "
# Lists the constants added after the imports (`map₂`): the theorem plus whatever else the
# code declared. Fully qualified, so it works under any `open` in the header.
DECLS_COMMAND = (
    "#eval show Lean.CoreM Unit from do\n"
    "  let names := (← Lean.getEnv).constants.map₂.toList.map (·.1.toString)\n"
    f"  Lean.logInfo (\"{DECLS_MARKER}\" ++ (Lean.toJson names).compress)\n"
)

_IMPORT_RE = re.compile(r"^\s*(?:(?:public|private)\s+)?(?:meta\s+)?import\s+(?:all\s+)?(\S+)\s*(?:--.*)?$")
_AXIOMS_RE = re.compile(r"^'(.+)' (?:depends on axioms: \[(.*)\]|does not depend on any axioms)\s*$", re.S)
_DECLS_RE = re.compile(rf"^{DECLS_MARKER}(\[.*\])$", re.S)
# Auxiliary declarations Lean itself creates under a theorem `t`: t._proof_1, t.match_1,
# t._native.…, t.proof_1, t.eq_1 (seen on real proofs; anything else is a new declaration).
_AUX_SUFFIX_RE = re.compile(r"^(?:_.*|match_\d+(?:\..*)?|proof_\d+|eq_\d+|eq_def)$")
_SEVERITY = {"information": "info", "info": "info", "warning": "warning", "error": "error"}


def _event(level: int, event: str, **detail) -> None:
    LOG.log(level, json.dumps({"event": event, **detail}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Results and parsing


@dataclass(frozen=True)
class LeanMessage:
    severity: str  # error | warning | info
    line: int  # 1-based, in the full file
    column: int  # 0-based
    text: str
    end_line: int | None = None
    end_column: int | None = None


@dataclass
class LeanResult:
    """Outcome of one verification. `status` says whether Lean ran to the end; whether the
    proof is correct is decided by `accepted` (the messages may contain errors)."""

    status: str  # one of STATUSES
    backend: str  # repl | file
    messages: list[LeanMessage] = field(default_factory=list)
    sorries: list[dict] = field(default_factory=list)  # {line, column, goal} (REPL only)
    axioms: list[str] | None = None  # axioms of the checked theorem; None if not printed
    declarations: list[str] | None = None  # constants the code added; None if not listed
    theorem: str | None = None
    elapsed_s: float = 0.0
    cached: bool = False
    detail: str = ""

    @property
    def errors(self) -> list[LeanMessage]:
        return [m for m in self.messages if m.severity == "error"]

    @property
    def has_sorry(self) -> bool:
        return bool(self.sorries) or any(m.severity == "warning" and SORRY_WARNING in m.text for m in self.messages)


def _message(raw: dict) -> LeanMessage:
    pos, end = raw.get("pos") or {}, raw.get("endPos") or {}
    return LeanMessage(
        severity=_SEVERITY.get(raw.get("severity", "error"), "error"),
        line=int(pos.get("line", 0)),
        column=int(pos.get("column", 0)),
        text=raw.get("data", ""),
        end_line=end.get("line"),
        end_column=end.get("column"),
    )


def _plain_name(name: str) -> str:
    return name.replace("«", "").replace("»", "")


def _take_answer(messages: list[LeanMessage], line: int | None, pattern: re.Pattern) -> tuple[list[LeanMessage], re.Match | None]:
    """Remove the info messages on `line` that match `pattern`. The answer counts only if
    there is exactly one; otherwise the messages are kept and None is returned."""
    if line is None:
        return messages, None
    answers, rest = [], []
    for m in messages:
        match = pattern.match(m.text) if (m.severity == "info" and m.line == line) else None
        (answers if match else rest).append((m, match))
    rest = [m for m, _ in rest]
    if len(answers) != 1:
        return rest + [m for m, _ in answers], None
    return rest, answers[0][1]


def split_checks(messages: list[LeanMessage], axioms_line: int | None, decls_line: int | None = None,
                 theorem: str | None = None) -> tuple[list[LeanMessage], list[str] | None, list[str] | None]:
    """Take the answers of the runner's own checks out of the messages →
    (messages, axioms, declarations). An answer that is missing, repeated or about another
    theorem leaves its value unknown (None → not accepted)."""
    messages, ax = _take_answer(messages, axioms_line, _AXIOMS_RE)
    axioms = None
    if ax is not None:
        if theorem and _plain_name(ax.group(1)) != _plain_name(theorem):
            messages.append(LeanMessage("info", axioms_line, 0, ax.group(0)))
        else:
            axioms = [a.strip() for a in (ax.group(2) or "").split(",") if a.strip()]
    messages, dc = _take_answer(messages, decls_line, _DECLS_RE)
    decls = None
    if dc is not None:
        try:
            decls = [str(n) for n in json.loads(dc.group(1))]
        except json.JSONDecodeError:
            messages.append(LeanMessage("info", decls_line, 0, dc.group(0)))
    return messages, axioms, decls


def parse_repl_response(resp: dict, axioms_line: int | None = None, decls_line: int | None = None,
                        theorem: str | None = None) -> tuple[list[LeanMessage], list[dict], list[str] | None, list[str] | None]:
    """REPL command response → (messages, sorries, axioms, declarations)."""
    messages, axioms, decls = split_checks([_message(m) for m in resp.get("messages", [])],
                                           axioms_line, decls_line, theorem)
    sorries = [
        {"line": s.get("pos", {}).get("line"), "column": s.get("pos", {}).get("column"), "goal": s.get("goal", "")}
        for s in resp.get("sorries", [])
    ]
    return messages, sorries, axioms, decls


def parse_json_lines(stdout: str, axioms_line: int | None = None, decls_line: int | None = None,
                     theorem: str | None = None) -> tuple[list[LeanMessage], list[str] | None, list[str] | None]:
    """`lean --json` output (one message per line) → (messages, axioms, declarations).
    Non-JSON lines, e.g. a runtime abort, become error messages at line 0."""
    messages = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            messages.append(LeanMessage("error", 0, 0, line))
            continue
        messages.append(_message(raw))
    return split_checks(messages, axioms_line, decls_line, theorem)


def probe_lines(imports: list[str], probes: dict[str, str], first_line: int = 1) -> tuple[str, dict[int, str]]:
    """`#check @<name>` for the probe of every import that has one → (text, line → name)."""
    names = [n for m in imports if m in probes
             for n in ([probes[m]] if isinstance(probes[m], str) else list(probes[m]))]
    return "".join(f"#check @{n}\n" for n in names), {first_line + i: n for i, n in enumerate(names)}


def probe_ok(messages: list[LeanMessage], lines: dict[int, str], required: str | None,
             probes: dict[str, str]) -> tuple[bool, str]:
    """The imports are loaded iff nothing is an error, every probe line answers `<name> : …`
    (or `@<name> : …`) and, if `required` is given (the primary base import), it has a probe."""
    if required is not None and required not in probes:
        return False, f"no probe configured for {required!r} (lean.probes)"
    if errors := [m for m in messages if m.severity == "error"]:
        return False, f"line {errors[0].line}: {errors[0].text[:300]}"
    for line, name in lines.items():
        if not any(m.severity == "info" and m.line == line and re.match(rf"@?{re.escape(name)} :", m.text)
                   for m in messages):
            return False, f"no answer for `#check @{name}` (line {line})"
    return True, ""


def extra_declarations(declarations: list[str], theorem: str) -> list[str]:
    """Declarations other than the theorem and the auxiliaries Lean generates for it."""
    name = _plain_name(theorem)
    extra = []
    for d in map(_plain_name, declarations):
        if d == name or (d.startswith(name + ".") and _AUX_SUFFIX_RE.match(d[len(name) + 1:])):
            continue
        extra.append(d)
    return extra


def compiles(result: LeanResult) -> bool:
    """Lean finished without errors (`sorry` allowed): the test for skeletons."""
    return result.status == "finished" and not result.errors


def compiles_between(result: LeanResult, first: int, last: int) -> bool:
    """One lemma of a multi-lemma file (lines first..last) finished without errors and
    without `sorry`. A message outside every line (line 0, e.g. an abort) counts against
    every lemma. Only for files whose proofs the code wrote from a fixed tactic list
    (statement.refutation_file, closing_candidates_file): it reads no axioms."""
    if result.status != "finished":
        return False
    for m in result.messages:
        inside = first <= m.line <= last or m.line == 0
        if inside and (m.severity == "error" or (m.severity == "warning" and SORRY_WARNING in m.text)):
            return False
    return not any(s.get("line") is not None and first <= s["line"] <= last for s in result.sorries)


def accepted(result: LeanResult) -> tuple[bool, str]:
    """Acceptance predicate (Eq. 2): Lean finished, no errors, no `sorry`, the axioms of
    the theorem ⊆ {propext, Classical.choice, Quot.sound}, and the code declared nothing but
    the theorem (and Lean's auxiliaries for it). Returns (accepted, reason)."""
    if result.status != "finished":
        return False, result.status
    if result.errors:
        return False, "error"
    if result.has_sorry:
        return False, "sorry"
    if result.axioms is None:
        return False, "axioms_unknown"
    extra = sorted(set(result.axioms) - ALLOWED_AXIOMS)
    if extra:
        return False, "nonstandard_axioms: " + ", ".join(extra)
    if result.declarations is None or not result.theorem:
        return False, "declarations_unknown"
    extra = extra_declarations(result.declarations, result.theorem)
    if extra:
        return False, "extra_declarations: " + ", ".join(extra)
    return True, "accepted"


def split_imports(code: str) -> tuple[list[str], str]:
    """Leading `import` lines → (modules, code with those lines blanked)."""
    lines = code.split("\n")
    modules = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _IMPORT_RE.match(line)
        if not match:
            break
        modules.append(match.group(1))
        lines[i] = ""
    return modules, "\n".join(lines)


def with_checks(code: str, theorem: str | None) -> tuple[str, int | None, int | None]:
    """Append `#print axioms <theorem>` and the declarations listing →
    (code, line of `#print axioms`, line of the listing's `#eval`)."""
    if not theorem:
        return code, None, None
    code = code.rstrip("\n") + "\n\n"
    axioms_line = code.count("\n") + 1
    code += f"#print axioms {theorem}\n\n"
    decls_line = code.count("\n") + 1
    return code + DECLS_COMMAND, axioms_line, decls_line


# ---------------------------------------------------------------------------
# Processes


def descendants(pid: int) -> list:
    """psutil handles of a process' descendants ([] if it is gone or psutil fails)."""
    try:
        import psutil

        return psutil.Process(pid).children(recursive=True)
    except Exception:  # noqa: BLE001 — psutil.Error, ImportError
        return []


def kill_tree(proc: subprocess.Popen, known: list | None = None) -> None:
    """Kill a process and its children (`lake env` leaves `lean`/`repl` running otherwise),
    and wait until they are gone so a restarted REPL never overlaps the old one in memory.
    `known`: descendants recorded earlier, still killed if the parent has already exited."""
    children = list({p.pid: p for p in [*(known or []), *descendants(proc.pid)]}.values())
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
    for p in children:  # taskkill /F returns before the processes have exited
        try:
            p.kill()
        except Exception:  # noqa: BLE001 — already gone or not ours
            pass
    try:
        import psutil

        psutil.wait_procs(children, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(2):
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            proc.kill()


def tree_memory_mb(pid: int) -> float:
    """Resident memory of a process and its children (MB); 0 if it is gone."""
    try:
        import psutil

        parent = psutil.Process(pid)
        procs = [parent, *parent.children(recursive=True)]
    except Exception:  # noqa: BLE001 — psutil.Error, ImportError
        return 0.0
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except Exception:  # noqa: BLE001
            pass
    return total / 2**20


class ReplTimeout(Exception):
    pass


class ReplMemoryExceeded(Exception):
    pass


class ReplDied(Exception):
    pass


class ReplProcess:
    """One REPL process speaking JSON over stdin/stdout (requests and replies end with a
    blank line). Not thread-safe; LeanRunner serialises access."""

    def __init__(self, cmd: list[str], cwd: Path, kill_memory_mb: float, memory_poll_s: float):
        self.kill_memory_mb = kill_memory_mb
        self.memory_poll_s = memory_poll_s
        self.commands = 0
        self.start_commands = 0  # import + probe, set once the REPL is accepted
        self.children: list = []  # recorded after start-up (the repl under `lake env`)
        self.stderr_tail: deque[str] = deque(maxlen=50)
        self._replies: queue.Queue[str | None] = queue.Queue()
        self.proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        buf: list[str] = []
        for line in self.proc.stdout:
            if line.strip():
                buf.append(line)
            elif buf:
                self._replies.put("".join(buf))
                buf = []
        if buf:
            self._replies.put("".join(buf))
        self._replies.put(None)  # EOF

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip())

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def memory_mb(self) -> float:
        return tree_memory_mb(self.proc.pid)

    def request(self, payload: dict, timeout: float) -> dict:
        """Send one command and wait for its reply, watching the memory of the process tree."""
        if not self.alive:
            raise ReplDied(f"exit code {self.proc.returncode}")
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise ReplDied(repr(exc)) from exc
        self.commands += 1
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplTimeout(f"no reply after {timeout:.0f} s")
            try:
                raw = self._replies.get(timeout=min(self.memory_poll_s, remaining))
            except queue.Empty:
                if self.kill_memory_mb and (mem := self.memory_mb()) > self.kill_memory_mb:
                    raise ReplMemoryExceeded(f"{mem:.0f} MB > {self.kill_memory_mb:.0f} MB")
                continue
            if raw is None:
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                raise ReplDied(f"exit code {self.proc.returncode}: {' | '.join(self.stderr_tail)}"[:2000])
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ReplDied(f"unreadable reply: {raw[:500]}") from exc

    def kill(self) -> None:
        kill_tree(self.proc, self.children)


# ---------------------------------------------------------------------------
# Runner


class LeanRunner:
    """`verify(code, theorem) → LeanResult`. Never raises; thread-safe (one check at a time)."""

    def __init__(self, cfg: dict | None = None, root: Path = ROOT, repl_cmd: list[str] | None = None):
        cfg = {**DEFAULTS, **((cfg if cfg is not None else load_pipeline()).get("lean") or {})}
        self.root = root
        self.cfg = cfg
        self.backend = cfg["backend"]
        self.project_dir = root / cfg["project_dir"]
        self.base_imports = list(cfg["base_imports"])
        self.timeout_s = float(cfg["timeout_s"])
        self.startup_timeout_s = float(cfg["startup_timeout_s"])
        self.restart_every = int(cfg["restart_every"])
        self.restart_memory_mb = float(cfg["restart_memory_mb"])
        self.kill_memory_mb = float(cfg["kill_memory_mb"])
        self.memory_poll_s = float(cfg["memory_poll_s"])
        self.cache_size = int(cfg["cache_size"])
        self.probes = dict(cfg["probes"] or {})
        exe = root / cfg["repl_dir"] / ".lake" / "build" / "bin" / ("repl.exe" if sys.platform == "win32" else "repl")
        self.repl_cmd = repl_cmd or ["lake", "env", str(exe)]
        self.repl_exe = None if repl_cmd else exe
        self.starts = 0
        self.file_verifications = 0
        self.file_import_s: dict[str, float | None] = {}  # imports → measured import time (None = failed)
        self.cache_hits = 0
        self.warnings: list[dict] = []
        self._cache: OrderedDict[str, LeanResult] = OrderedDict()
        self._repl: ReplProcess | None = None
        self._env: int | None = None
        self._last_env: int | None = None  # REPL env ids only grow; anything else = desync
        self._repl_failed = self.backend != "repl"
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def _warn(self, reason: str, **detail) -> None:
        entry = {"reason": reason, **detail}
        self.warnings.append(entry)
        _event(logging.WARNING, "lean_runner_warning", **entry)

    def start(self) -> bool:
        """Start the REPL and load the base imports (env 0). False → file back-end."""
        with self._lock:
            return self._ensure_repl()

    def ensure_available(self) -> bool:
        """Start the REPL; if it cannot be used, measure the file back-end's import (with the same
        probes). False → Lean cannot check anything: callers must not start (run_eval stops
        before any LLM call). Never raises."""
        with self._lock:
            try:
                if self._ensure_repl():
                    return True
                return self._import_time(self.base_imports) is not None
            except Exception as exc:  # noqa: BLE001
                self._warn("availability_check_failed", error=repr(exc))
                return False

    @property
    def available(self) -> bool:
        """False once both back-ends are known to be unusable (the REPL was given up and the file
        back-end's import with the probes failed); checked by run_eval between problems."""
        return not (self._repl_failed and self.file_import_s.get("\n".join(self.base_imports), 0.0) is None)

    def _ensure_repl(self) -> bool:
        try:
            return self._start_repl()
        except Exception as exc:  # noqa: BLE001 — never raise; fall back to files
            self._stop_repl()
            return self._repl_unavailable("repl_start_failed", error=repr(exc))

    def _start_repl(self) -> bool:
        if self._repl_failed:
            return False
        if self._repl is not None and self._repl.alive:
            return True
        self._stop_repl()
        if self.repl_exe is not None and not self.repl_exe.exists():
            return self._repl_unavailable("repl_not_built", path=str(self.repl_exe), hint="bash scripts/build_repl.sh")
        # An import that fails, or that "succeeds" without the probes answering, is retried once
        # with a fresh process (team decision 2026-11-03); then the file back-end takes over.
        for attempt in (1, 2):
            outcome, reason, detail = self._launch_repl()
            if outcome == "ok":
                return True
            if outcome == "fatal" or attempt == 2:
                return self._repl_unavailable(reason, attempts=attempt, **detail)
            self._warn(reason, attempt=attempt, action="restart", **detail)
        return False  # not reached

    def _launch_repl(self) -> tuple[str, str, dict]:
        """One REPL process: import the base, then check the probes → ("ok" | "retry" | "fatal",
        warning reason, detail). A start timeout or memory kill is fatal (retrying would wait
        `startup_timeout_s` again); an import error, a dead process or a failed probe is retried."""
        t0 = time.perf_counter()
        try:
            repl = ReplProcess(self.repl_cmd, self.project_dir, self.kill_memory_mb, self.memory_poll_s)
        except OSError as exc:
            return "fatal", "repl_launch_failed", {"error": repr(exc)}
        try:
            header = "\n".join(f"import {m}" for m in self.base_imports)
            resp = repl.request({"cmd": header}, self.startup_timeout_s)
            errors = [m for m in resp.get("messages", []) if m.get("severity") == "error"]
            if not isinstance(resp.get("env"), int) or errors:
                repl.kill()
                return "retry", "repl_import_failed", {"reply": json.dumps(resp, ensure_ascii=False)[:2000]}
            text, lines = probe_lines(self.base_imports, self.probes)
            probe = repl.request({"cmd": text, "env": resp["env"]}, self.timeout_s) if text else {}
        except ReplDied as exc:
            repl.kill()
            return "retry", "repl_start_failed", {"error": f"{type(exc).__name__}: {exc}"}
        except (ReplTimeout, ReplMemoryExceeded) as exc:
            repl.kill()
            return "fatal", "repl_start_failed", {"error": f"{type(exc).__name__}: {exc}"}
        ok, why = probe_ok([_message(m) for m in probe.get("messages", [])], lines,
                           self.base_imports[0] if self.base_imports else None, self.probes)
        probe_env = probe.get("env")
        if ok and (not isinstance(probe_env, int) or probe_env <= resp["env"]):
            ok, why = False, f"unexpected env id {probe_env!r} after the probe"
        if not ok:
            seconds, memory = round(time.perf_counter() - t0, 2), round(repl.memory_mb())
            repl.kill()
            return "retry", "repl_probe_failed", {"detail": why, "seconds": seconds, "memory_mb": memory}
        repl.children = descendants(repl.proc.pid)
        repl.start_commands = repl.commands
        self._repl, self._env, self._last_env = repl, resp["env"], probe_env
        self.starts += 1
        _event(logging.INFO, "lean_runner_info", message="repl started", imports=self.base_imports,
               probes=list(lines.values()), seconds=round(time.perf_counter() - t0, 2),
               memory_mb=round(repl.memory_mb()), starts=self.starts)
        return "ok", "", {}

    def _repl_unavailable(self, reason: str, **detail) -> bool:
        self._repl_failed = True
        self._warn(reason, fallback="file", **detail)
        return False

    def _stop_repl(self) -> None:
        repl, self._repl, self._env, self._last_env = self._repl, None, None, None
        if repl is not None:
            try:
                repl.kill()
            except Exception as exc:  # noqa: BLE001
                self._warn("repl_kill_failed", error=repr(exc))

    def close(self) -> None:
        with self._lock:
            self._stop_repl()

    def __enter__(self) -> LeanRunner:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def run_info(self) -> dict:
        """For run_config.json (RNF-2): REPL version, back-end use and degradation."""
        return {**repl_env_info(self.root, {"lean": self.cfg}), "degraded": self.file_verifications > 0,
                "file_verifications": self.file_verifications, "file_import_s": dict(self.file_import_s),
                "repl_starts": self.starts, "cache_hits": self.cache_hits, "probes": self.probes,
                "repl_given_up": self._repl_failed and self.backend == "repl", "available": self.available}

    # -- verification -------------------------------------------------------

    def verify(self, code: str, theorem: str | None = None, timeout: float | None = None) -> LeanResult:
        """Compile `code` (a full file, header included). With `theorem`, also report its
        axioms and the declarations the code added (both needed by `accepted`)."""
        with self._lock:
            t0 = time.perf_counter()
            try:
                timeout = self.timeout_s if timeout is None else float(timeout)
                key = hashlib.sha256(json.dumps([code, theorem, timeout]).encode("utf-8")).hexdigest()
                if (hit := self._cache.get(key)) is not None:
                    self._cache.move_to_end(key)
                    self.cache_hits += 1
                    result = _copy(hit, cached=True)
                else:
                    result = self._verify(code, theorem, timeout)
                    result.theorem = theorem
                    if result.status == "finished" and self.cache_size > 0:
                        self._cache[key] = _copy(result)
                        if len(self._cache) > self.cache_size:
                            self._cache.popitem(last=False)
            except Exception as exc:  # noqa: BLE001 — never raise to the graph
                self._warn("verify_failed", error=repr(exc))
                self._stop_repl()  # does not raise either
                result = LeanResult("crash", self.backend, theorem=theorem, detail=repr(exc))
            if not result.cached:
                result.elapsed_s = round(time.perf_counter() - t0, 3)
        _event(logging.INFO, "lean_verify", backend=result.backend, status=result.status, cached=result.cached,
               errors=len(result.errors), sorry=result.has_sorry, axioms=result.axioms,
               declarations=result.declarations, seconds=result.elapsed_s)
        return result

    def _verify(self, code: str, theorem: str | None, timeout: float) -> LeanResult:
        modules, body = split_imports(code)
        if not self.fits_base(modules):
            self._warn("imports_not_in_base", imports=modules, base=self.base_imports, fallback="file")
            return self._verify_file(code, modules, theorem, timeout)
        if not self._ensure_repl():
            return self._verify_file(code, modules, theorem, timeout)
        if self._repl.commands - self._repl.start_commands >= self.restart_every:  # import and probe not counted
            _event(logging.INFO, "lean_runner_info", message="preventive restart", commands=self._repl.commands)
            self._stop_repl()
            if not self._ensure_repl():
                return self._verify_file(code, modules, theorem, timeout)
        return self._verify_repl(body, theorem, timeout)

    def fits_base(self, modules: list[str]) -> bool:
        """Env 0 sees the same declarations as the file iff the file imports the primary base
        module (`base_imports[0]`, whose closure holds the rest) and nothing outside the base."""
        return bool(self.base_imports) and self.base_imports[0] in modules and set(modules) <= set(self.base_imports)

    def _verify_repl(self, body: str, theorem: str | None, timeout: float) -> LeanResult:
        body, axioms_line, decls_line = with_checks(body, theorem)
        try:
            resp = self._repl.request({"cmd": body, "env": self._env}, timeout)
        except ReplTimeout as exc:
            self._stop_repl()  # started again before the next verification
            self._warn("repl_timeout", detail=str(exc))
            return LeanResult("timeout", "repl", detail=str(exc))
        except ReplMemoryExceeded as exc:
            self._stop_repl()
            self._warn("repl_memory_limit", detail=str(exc))
            return LeanResult("memory_limit", "repl", detail=str(exc))
        except ReplDied as exc:
            self._stop_repl()
            self._warn("repl_died", detail=str(exc))
            return LeanResult("crash", "repl", detail=str(exc))
        if "message" in resp and "env" not in resp:  # protocol-level error of the REPL
            self._stop_repl()
            self._warn("repl_error", detail=str(resp["message"])[:2000])
            return LeanResult("crash", "repl", detail=str(resp["message"]))
        env = resp.get("env")
        if not isinstance(env, int) or env <= self._last_env:  # e.g. code wrote to the REPL's stdout
            self._stop_repl()
            self._warn("repl_desync", env=env)
            return LeanResult("crash", "repl", detail=f"unexpected env id {env!r}")
        self._last_env = env
        messages, sorries, axioms, decls = parse_repl_response(resp, axioms_line, decls_line, theorem)
        if (mem := self._repl.memory_mb()) > self.restart_memory_mb:
            _event(logging.INFO, "lean_runner_info", message="preventive restart", memory_mb=round(mem))
            self._stop_repl()
        return LeanResult("finished", "repl", messages, sorries, axioms, decls)

    # -- file back-end --------------------------------------------------------

    def _run_file(self, code: str, timeout: float) -> tuple[str, int | None, str, str]:
        """`lake env lean --json` on a temporary file → (status, exit code, stdout, stderr);
        status is `finished` or `timeout`."""
        with tempfile.TemporaryDirectory(prefix="formal_mas_lean_") as tmp:
            path = Path(tmp) / "Check.lean"
            path.write_text(code, encoding="utf-8")
            cmd = ["lake", "env", "lean", "--json", f"--memory={int(self.kill_memory_mb)}", str(path)]
            proc = subprocess.Popen(cmd, cwd=self.project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                try:
                    proc.communicate(timeout=10)  # drain the pipes
                except (subprocess.TimeoutExpired, ValueError, OSError):
                    pass
                return "timeout", None, "", ""
        return "finished", proc.returncode, out, err

    def _import_time(self, modules: list[str]) -> float | None:
        """`lake env lean` time for a file with only these imports, measured once per set
        (a failure is remembered too, so a broken setup does not cost startup_timeout_s per check)."""
        key = "\n".join(modules)
        if key not in self.file_import_s:
            t0 = time.perf_counter()
            probe, lines = probe_lines(modules, self.probes, first_line=len(modules) + 1)
            status, rc, out, err = self._run_file("".join(f"import {m}\n" for m in modules) + probe,
                                                  self.startup_timeout_s)
            ok, why = (False, "")
            if status == "finished" and rc == 0:
                messages, _, _ = parse_json_lines(out)
                primary = self.base_imports[0] if self.base_imports and self.base_imports[0] in modules else None
                ok, why = probe_ok(messages, lines, primary, self.probes)
            if not ok:
                self._warn("file_import_failed", imports=modules, status=status, exit_code=rc,
                           detail=(why or (err or out).strip())[:2000])
                self.file_import_s[key] = None
                return None
            self.file_import_s[key] = round(time.perf_counter() - t0, 2)
            _event(logging.INFO, "lean_runner_info", message="file back-end import measured", imports=modules,
                   seconds=self.file_import_s[key])
        return self.file_import_s[key]

    def _verify_file(self, code: str, modules: list[str], theorem: str | None, timeout: float) -> LeanResult:
        """Plan B. Wall-clock limit: `timeout` + the measured import time."""
        self.file_verifications += 1
        import_s = self._import_time(modules)
        if import_s is None:
            return LeanResult("crash", "file", detail="the imports could not be loaded by `lake env lean`")
        code, axioms_line, decls_line = with_checks(code, theorem)
        try:
            status, rc, out, err = self._run_file(code, timeout + import_s)
        except OSError as exc:
            return LeanResult("crash", "file", detail=repr(exc))
        if status == "timeout":
            return LeanResult("timeout", "file", detail=f"no result after {timeout:.0f} s + {import_s:.0f} s import")
        return file_result(rc, out, err, axioms_line, decls_line, theorem)


def _copy(result: LeanResult, **changes) -> LeanResult:
    """A copy with fresh lists, so the cache and its callers never share mutable state."""
    return dataclasses.replace(
        result, messages=list(result.messages), sorries=[dict(s) for s in result.sorries],
        axioms=None if result.axioms is None else list(result.axioms),
        declarations=None if result.declarations is None else list(result.declarations), **changes)


def file_result(returncode: int, stdout: str, stderr: str, axioms_line: int | None,
                decls_line: int | None = None, theorem: str | None = None) -> LeanResult:
    """Interpret one `lake env lean --json` run (exit code 1 = Lean reported errors)."""
    if "memory_exception" in stderr or "excessive memory consumption" in stderr:
        return LeanResult("memory_limit", "file", detail=stderr.strip()[:2000])
    messages, axioms, decls = parse_json_lines(stdout, axioms_line, decls_line, theorem)
    if returncode not in (0, 1) or (returncode == 1 and not messages):
        return LeanResult("crash", "file", messages, axioms=axioms, declarations=decls,
                          detail=f"exit code {returncode}: {stderr.strip()[:2000]}")
    return LeanResult("finished", "file", messages, axioms=axioms, declarations=decls, theorem=theorem)


def repl_env_info(root: Path = ROOT, cfg: dict | None = None) -> dict:
    """REPL version info for run_config.json (RNF-2): tag, commit and local changes of lean_repl/."""
    cfg = {**DEFAULTS, **((cfg if cfg is not None else load_pipeline()).get("lean") or {})}
    repl_dir = root / cfg["repl_dir"]

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(repl_dir), *args], capture_output=True, text=True,
                                 encoding="utf-8", timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    status = git("status", "--porcelain", "--untracked-files=no")
    toolchain = repl_dir / "lean-toolchain"
    return {"backend": cfg["backend"], "repl_tag": git("describe", "--tags", "--exact-match") or None,
            "repl_commit": git("rev-parse", "HEAD") or None,
            "repl_modified": None if status is None else bool(status),
            "repl_toolchain": toolchain.read_text(encoding="utf-8").strip() if toolchain.exists() else None}
