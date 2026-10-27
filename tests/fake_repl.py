"""Stand-in for the Lean REPL in offline tests: same JSON protocol, scripted behaviour.

Commands (the `cmd` text):
    import ...          → {"env": 0}  (`import Broken` → an error message, no env)
    contains HANG       → never answers
    contains DIE        → exits with code 3
    contains BIGMEM     → allocates ~400 MB, then never answers
    contains PROTOCOL   → {"message": "..."} (REPL-level error)
    contains DESYNC     → a reply with env 0 again (out of sequence)
    otherwise           → info message "'X' depends on axioms: [propext]" on the line of
                          `#print axioms X`, and `FORMAL_MAS_DECLS ["X"]` on the line of the
                          runner's declarations listing, if present
Flags: --fail-start (exit at once), --slow-start SECONDS (delay before reading).
"""

import json
import sys
import time


def reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n\n")
    sys.stdout.flush()


def handle(req: dict, env_counter: list[int]) -> None:
    cmd = req.get("cmd", "")
    if cmd.startswith("import"):
        if "Broken" in cmd:
            reply({"messages": [{"severity": "error", "pos": {"line": 1, "column": 0}, "endPos": None,
                                 "data": "unknown module prefix 'Broken'"}], "env": None})
            return
        reply({"env": 0})
        return
    if "HANG" in cmd:
        time.sleep(3600)
    if "DIE" in cmd:
        sys.exit(3)
    if "BIGMEM" in cmd:
        hog = bytearray(400 * 2**20)  # noqa: F841 — keep it alive
        hog[::4096] = b"x" * len(hog[::4096])
        time.sleep(3600)
    if "PROTOCOL" in cmd:
        reply({"message": "Unknown environment."})
        return
    if "DESYNC" in cmd:  # a stale or injected reply: env id that does not grow
        reply({"messages": [], "env": 0})
        return
    env_counter[0] += 1
    messages, name = [], None
    for i, line in enumerate(cmd.split("\n"), start=1):
        if line.startswith("#print axioms "):
            name = line.split()[-1]
            messages.append({"severity": "info", "pos": {"line": i, "column": 0}, "endPos": {"line": i, "column": 6},
                             "data": f"'{name}' depends on axioms: [propext]"})
        elif line.startswith("#eval show Lean.CoreM") and name:
            messages.append({"severity": "info", "pos": {"line": i, "column": 0}, "endPos": {"line": i, "column": 5},
                             "data": f"FORMAL_MAS_DECLS {json.dumps([name])}"})
    reply({"messages": messages, "env": env_counter[0]})


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    if "--fail-start" in sys.argv:
        sys.exit(1)
    if "--slow-start" in sys.argv:
        time.sleep(float(sys.argv[sys.argv.index("--slow-start") + 1]))
    env_counter = [0]
    buf: list[str] = []
    for line in sys.stdin:
        if line.strip():
            buf.append(line)
        elif buf:
            handle(json.loads("".join(buf)), env_counter)
            buf = []


if __name__ == "__main__":
    main()
