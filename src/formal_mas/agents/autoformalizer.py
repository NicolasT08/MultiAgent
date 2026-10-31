"""Autoformalizer (Phase 5): subgoal lemma + winning sketch + lemmas → tactic block.

    out = run_autoformalizer(problem, plan, i, sketch, client, runner, retriever, budget, tracer,
                             syntax=checker)

1. The lemma `<name>_sg<i>` (binders, h1 … h(i-1), Pᵢ) is built by statement.subgoal_statement;
   the model never writes it (CLAUDE.md §1.3).
2. The Retriever's top-k lemmas for that lemma (the same query the Generator used).
3. One `autoformalizer` LLM call. The prompt depends on the role's model
   (models.yaml → prompt_style):
       chat    prompts/autoformalizer.md with the cheat sheet; JSON {"tactics"} (TacticProof),
               one repair call if the JSON is bad;
       prover  prompts/autoformalizer_prover.md ("Complete the following Lean 4 code", for
               whole-proof models such as Goedel-Prover); the answer is a ```lean4 block and
               only the tactics after `:= by` of the lemma are kept (`prover_tactics`).
4. The block passes lean/sanitize.py and, if given, lean/syntax_check.py (parser +
   whitelist); then the lemma is compiled with the runner and must be *accepted* (Eq. 2).
One attempt (retries and name repair are Phase 6). Never raises: the result is
{status, index, lemma, tactics, reason, detail, lemmas, errors}. status ∈ ok | failed |
budget_exhausted | budget_stop | provider_error | error; `reason` says why it failed:
invalid_output | no_tactics | sanitize:<reason> | lean:<accepted reason>.
"""

from __future__ import annotations

import re
import textwrap

from formal_mas.agents.contracts import Plan, TacticProof
from formal_mas.agents.planner import lemma_block
from formal_mas.agents.prompting import document, render
from formal_mas.lean.runner import accepted
from formal_mas.lean.statement import (UnsafeLean, _top_level, clean_tactics, parse_theorem, subgoal_lemma,
                                       subgoal_statement)
from formal_mas.llm.client import LLMClient, LLMOutputError, ProviderError, SpendLimitReached
from formal_mas.tracing import Budget, BudgetExhausted, Tracer

ROLE = "autoformalizer"
STYLES = ("chat", "prover")
__all__ = ["run_autoformalizer", "prover_tactics", "prompt_style"]

_LEAN_BLOCK_RE = re.compile(r"```(?:lean4|lean)?[ \t]*\n(.*?)(?:```|\Z)", re.S)


def prompt_style(client: LLMClient, role: str = ROLE) -> str:
    """The role's primary model's prompt_style (models.yaml)."""
    style = client.roles[role].models[0].prompt_style
    if style not in STYLES:
        raise ValueError(f"unknown prompt_style {style!r} for {role} (expected one of {STYLES})")
    return style


def prover_tactics(text: str | None, lemma: str) -> str | None:
    """The tactic block after `:= by` of `theorem|lemma <lemma>` in the last Lean code block
    of a prover's answer (the whole answer if it has no block); None if not found. Whatever
    statement the model wrote is discarded."""
    blocks = _LEAN_BLOCK_RE.findall(text or "")
    for code in reversed(blocks or [text or ""]):
        m = None
        for m in re.finditer(rf"(?<![\w.'])(?:theorem|lemma)\s+{re.escape(lemma)}(?![\w'.])", code):
            pass
        if m is None:
            continue
        tail = code[m.end():]
        ends = [e for e in _top_level(tail, ":=") if re.match(r"\s*by\b", tail[e + 2:])]
        if not ends:
            continue
        first, _, more = re.sub(r"^\s*by\b[ \t]*", "", tail[ends[0] + 2:], count=1).partition("\n")
        kept = []
        for line in more.split("\n"):
            if line.strip() and not line[:1].isspace():  # a top-level line: the proof has ended
                break
            kept.append(line)
        body = "\n".join(p for p in (first.strip(), textwrap.dedent("\n".join(kept)).strip("\n")) if p)
        return body.rstrip() or None
    return None


def _sketch_text(sketch: dict | None) -> tuple[str, str]:
    if not sketch:
        return "none", "(no sketch)"
    return sketch.get("strategy") or "other", sketch.get("sketch") or "(no sketch)"


def run_autoformalizer(problem: dict, plan: Plan | dict, index: int, sketch: dict | None, client: LLMClient,
                       runner, retriever, budget: Budget, tracer: Tracer, *, retrieve: bool = True,
                       syntax=None) -> dict:
    out = {"status": "error", "index": index, "lemma": None, "tactics": None, "reason": None, "detail": None,
           "lemmas": [], "errors": []}
    try:
        _run(problem, plan, index, sketch, client, runner, retriever, budget, tracer, retrieve, syntax, out)
    except Exception as exc:  # noqa: BLE001 — a node never raises to the caller
        tracer.event("node_error", level="error", node=ROLE, subgoal=index, error=repr(exc)[:1000])
        out.update(status="error", reason="error", detail=repr(exc)[:1000])
    return out


def _run(problem, plan, i, sketch, client, runner, retriever, budget, tracer, retrieve, syntax, out) -> None:
    thm = parse_theorem(problem["statement"])
    plan = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
    name, decl = subgoal_statement(thm, plan.subgoals, i)
    out["lemma"] = name
    hits = retriever.retrieve(decl) if (retrieve and retriever is not None) else []
    out["lemmas"] = [h.name for h in hits]
    strategy, text = _sketch_text(sketch)
    style = prompt_style(client)

    def fail(status: str, reason: str, detail: str = "") -> None:
        out.update(status=status, reason=reason, detail=detail[:1000] or None)
        tracer.event("formalize_failed", level="warning", node=ROLE, subgoal=i, status=status, reason=reason,
                     detail=detail[:500], tactics=out["tactics"])

    try:
        if style == "chat":
            messages = render("autoformalizer", cheatsheet=document("lean4_cheatsheet"), header=thm.header.strip(),
                              lemma=decl, strategy=strategy, sketch=text, index=i, count=len(plan.subgoals),
                              theorem=thm.declaration(), lemmas=lemma_block(hits))
            proof, _ = client.complete_json(ROLE, messages, TacticProof, budget)
            tactics = clean_tactics(proof.tactics)
        else:
            messages = render("autoformalizer_prover", header=thm.header.strip(), lemma=decl,
                              strategy=strategy, sketch=text.replace("-/", "- /"),
                              lemma_names=", ".join(out["lemmas"]) or "(none)")
            resp = client.complete(ROLE, messages, budget)
            tactics = prover_tactics(resp.content, name)
            if tactics is None:
                return fail("failed", "no_tactics", (resp.content or "")[-500:])
    except SpendLimitReached as exc:
        return fail("budget_stop", "budget_stop", str(exc))
    except BudgetExhausted:
        return fail("budget_exhausted", "budget_exhausted")
    except ProviderError as exc:
        return fail("provider_error", "provider_error", str(exc))
    except LLMOutputError as exc:
        return fail("failed", "invalid_output", str(exc))
    tactics = textwrap.dedent(clean_tactics(tactics)).strip("\n")
    out["tactics"] = tactics

    if syntax is not None:
        res = syntax.check(thm.header, tactics=[tactics])[0]
        if not res.ok:
            return fail("failed", f"sanitize:{res.reason}", res.detail)
    try:
        name, code = subgoal_lemma(thm, plan.subgoals, i, tactics)
    except UnsafeLean as exc:
        return fail("failed", f"sanitize:{exc.result.reason}", exc.result.detail)
    result = runner.verify(code, name)
    ok, why = accepted(result)
    out["errors"] = [f"line {m.line}: {m.text[:300]}" for m in result.errors[:3]]
    tracer.event("formalize_verified" if ok else "formalize_failed", node=ROLE, subgoal=i, style=style,
                 accepted=ok, reason=why, tactics=tactics, errors=out["errors"], cached_lean=result.cached)
    if ok:
        out.update(status="ok", reason=None)
    else:
        out.update(status="failed", reason=f"lean:{why}", detail=(out["errors"] or [None])[0])
