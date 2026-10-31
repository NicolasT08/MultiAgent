"""Automation pre-pass (Phase 5): try a fixed list of tactics before any LLM call.

    found = prepass(thm, targets, runner, tracer, cfg)   # cfg = pipeline.yaml → prepass

Each target is a lemma the code builds (statement.py): the theorem itself (`theorem`) or a
subgoal lemma `<name>_sg<i>` (`sg<i>`). One file holds every (target, tactic) pair as its
own lemma `<lemma>_auto<j>` with a heartbeat limit (statement.automation_file), compiled in
one REPL command; for each target the first tactic (in list order) whose lemma compiles is
then confirmed alone with `accepted` (Eq. 2) on the very file the pipeline will use
(statement.proof_file / subgoal_lemma). No LLM calls, deterministic, can be turned off
(`prepass.enabled: false`, for the ablation). Never raises: on any error nothing is found.
"""

from __future__ import annotations

from dataclasses import dataclass

from formal_mas.lean.runner import accepted, compiles_between
from formal_mas.lean.statement import Theorem, automation_file, proof_file, subgoal_lemma, subgoal_statement

NODE = "prepass"
__all__ = ["Target", "theorem_target", "subgoal_targets", "prepass"]


@dataclass(frozen=True)
class Target:
    key: str  # "theorem" | "sg<i>"
    lemma: str  # lemma name in the automation file
    extra: tuple[str, ...]  # binders after the theorem's
    goal: str

    def confirm(self, thm: Theorem, subgoals: list[str], tactic: str) -> tuple[str, str]:
        """(lemma name, file) of the target proved by `tactic`, as the pipeline will use it."""
        if self.key == "theorem":
            return thm.name, proof_file(thm, tactic)
        return subgoal_lemma(thm, subgoals, int(self.key[2:]), tactic)


def theorem_target(thm: Theorem) -> Target:
    return Target("theorem", thm.name, (), thm.goal)


def subgoal_targets(thm: Theorem, subgoals: list[str], indices: list[int]) -> list[Target]:
    names = thm.hypothesis_names(len(subgoals))
    out = []
    for i in indices:
        name, _ = subgoal_statement(thm, subgoals, i)  # checks the propositions (UnsafeLean)
        extra = tuple(f"({h} : {p})" for h, p in zip(names[:i - 1], subgoals[:i - 1]))
        out.append(Target(f"sg{i}", name, extra, subgoals[i - 1]))
    return out


def prepass(thm: Theorem, subgoals: list[str], targets: list[Target], runner, tracer, cfg: dict | None) -> dict:
    """{target key: tactic} for the targets an automatic tactic proves (accepted)."""
    if not cfg or not cfg.get("enabled", True) or not targets:
        return {}
    try:
        return _prepass(thm, subgoals, targets, runner, tracer, cfg)
    except Exception as exc:  # noqa: BLE001 — never raises; nothing found
        tracer.event("node_error", level="error", node=NODE, error=repr(exc)[:1000])
        return {}


def _prepass(thm, subgoals, targets, runner, tracer, cfg) -> dict:
    tactics = [t.strip() for t in cfg["tactics"]]
    code, ranges = automation_file(thm, [(t.lemma, t.extra, t.goal) for t in targets], tactics, int(cfg["heartbeats"]))
    result = runner.verify(code, None, timeout=float(cfg["timeout_s"]))
    found, compiled = {}, {}
    for t in targets:
        ok = [tac for j, tac in enumerate(tactics, start=1) if compiles_between(result, *ranges[f"{t.lemma}_auto{j}"])]
        compiled[t.key] = ok
        for tac in ok:
            name, file = t.confirm(thm, subgoals, tac)
            if accepted(runner.verify(file, name))[0]:
                found[t.key] = tac
                break
    tracer.event("prepass", node=NODE, targets=[t.key for t in targets], lean_status=result.status,
                 seconds=result.elapsed_s, compiles=compiled, found=found, cached_lean=result.cached)
    return found
