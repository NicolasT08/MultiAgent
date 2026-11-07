"""Regenerate the test-split analysis tables (docs/analysis_plan.md) from runs/ (read-only).

Usage (from the repository root):
    .venv/Scripts/python.exe docs/results/make_tables.py            # test split -> docs/results/
    .venv/Scripts/python.exe docs/results/make_tables.py --dev OUT  # dry run on dev runs -> OUT/

Nothing under runs/, data/, src/, scripts/ or config/ is written. Imports from src/: the statistics
(formal_mas.analysis.stats) and the kappa classifier (formal_mas.orchestrator.policy.classify, for C1).
The statistics (Wilson, exact McNemar, Bonferroni, bootstrap) come from
src/formal_mas/analysis/stats.py, which has offline tests in tests/test_stats.py.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from formal_mas.analysis.stats import (N_BOOT, SEED, boot_mean, boot_ratio, bonferroni,  # noqa: E402
                                       bonferroni_reject, mcnemar_exact, paired_boot_diff, quartiles, wilson)
from formal_mas.orchestrator.policy import ERROR_CLASSES, Trace, classify  # noqa: E402

RUNS = ROOT / "runs"
CONFIGS = ["C1", "C2", "C3", "C4", "C5"]
VERIFIED = ["C1", "C3", "C4", "C5"]
GRAPH = ["C3", "C4", "C5"]
DIFFS = ["easy", "medium", "hard"]
AREAS = ["algebra", "number_theory", "combinatorics"]
AREA_LABEL = {"algebra": "Algebra", "number_theory": "Number theory", "combinatorics": "Combinatorics"}
PLANNED = [("H1", "C5", "C1"), ("H2", "C5", "C3"), ("H3", "C5", "C4")]
EXPLORATORY = [("E1", "C3", "C4"), ("E2", "C1", "C3"), ("E3", "C1", "C4")]
INFRA = ("provider_error", "budget_stop", "error", "node_error", "invalid_statement")

# Test split: exactly the sources named in docs/analysis_plan.md (Deviations) and by the team.
TEST_SOURCES = {
    "C5": [  # (run folder, ids to keep or None = all rows)
        ("20261103T174646Z_C5_experiment", None),
        ("20261103T212359Z_C5_experiment_resume", ["imo_1987_p6", "imo_1979_p1"]),
        ("20261103T225140Z_C5_experiment_resume2",
         ["imo_1984_p6", "imo_1964_p1_2", "putnam_1964_b2", "putnam_1995_b1", "putnam_1965_b2"]),
    ],
    "C4": [("20261103T235556Z_C4_experiment", None)],
    "C3": [("20261104T194056Z_C3_experiment", None)],
    "C2": [("20261104T232056Z_C2_experiment_check", None)],
}
C1_MERGED = ("20261107T200233Z_C1_experiment_resume", "C1_test_merged_results.jsonl")
C1_PARTS = ["20261105T131443Z_C1_experiment", "20261107T200233Z_C1_experiment_resume"]
# rows present in a source run but discarded by the Deviations entry (status checked)
DISCARDED = {("20261103T212359Z_C5_experiment_resume", "imo_1984_p6"): "budget_stop"}
# runs whose whole ledger spend counts in each configuration's real cost
LEDGER_RUNS = {
    "C5": [s[0] for s in TEST_SOURCES["C5"]], "C4": ["20261103T235556Z_C4_experiment"],
    "C3": ["20261104T194056Z_C3_experiment"], "C2": ["20261104T232056Z_C2_experiment_check"], "C1": C1_PARTS,
}
# Dev dry run (plan 7: analysis code exercised on dev before reading test). Arbitrary stand-ins on
# the same 15 dev problems; their numbers mean nothing.
DEV_SOURCES = {
    "C5": [("20261103T151038Z_C5_experiment_r1", None)],
    "C4": [("20261103T130158Z_C5_experiment", None)],
    "C3": [("20261103T130158Z_C5_experiment", None)],
    "C2": [("20261103T173028Z_C2_experiment_check", None)],
    "C1": [("20261103T151038Z_C5_experiment_r1", None)],
}


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_traces(run: str) -> dict[str, list[dict]]:
    by_pid: dict[str, list[dict]] = defaultdict(list)
    for e in read_jsonl(RUNS / run / "trace.jsonl"):
        if e.get("problem_id"):
            by_pid[e["problem_id"]].append(e)
    return by_pid


def select_rows(sources, notes: list[str]):
    """-> ({pid: row}, {pid: run}). Fails loudly on duplicates or missing requested ids."""
    rows, origin = {}, {}
    for run, keep in sources:
        by = {}
        for r in read_jsonl(RUNS / run / "results.jsonl"):
            if r["problem_id"] in by:
                raise SystemExit(f"duplicate row {r['problem_id']} in {run}")
            by[r["problem_id"]] = r
        for (drun, dpid), dstatus in DISCARDED.items():
            if drun == run and dpid in by:
                if by[dpid]["status"] != dstatus:
                    raise SystemExit(f"{run}/{dpid}: expected status {dstatus}, got {by[dpid]['status']}")
                notes.append(f"Discarded row `{dpid}` (status `{dstatus}`) of `{run}`, as the C5 Deviations entry says.")
        ids = list(by) if keep is None else keep
        for pid in ids:
            if pid not in by:
                raise SystemExit(f"{run}: requested id {pid} has no row")
            if pid in rows:
                raise SystemExit(f"{pid} kept twice ({origin[pid]} and {run})")
            rows[pid], origin[pid] = by[pid], run
        unused = [p for p in by if p not in ids and (run, p) not in DISCARDED]
        if unused:
            raise SystemExit(f"{run}: rows neither kept nor discarded: {unused}")
        notes.append(f"Kept {len(ids)} rows from `{run}`.")
    return rows, origin


def select_c1(notes: list[str]):
    merged = read_jsonl(RUNS / C1_MERGED[0] / C1_MERGED[1])
    parts = {run: {r["problem_id"]: r for r in read_jsonl(RUNS / run / "results.jsonl")} for run in C1_PARTS}
    rows, origin = {}, {}
    for r in merged:
        pid = r["problem_id"]
        if pid in rows:
            raise SystemExit(f"C1 merged: duplicate {pid}")
        src = [run for run, by in parts.items() if by.get(pid) == r]
        if len(src) != 1:
            raise SystemExit(f"C1 merged row {pid} matches {len(src)} source rows")
        rows[pid], origin[pid] = r, src[0]
    unmerged = [(run, p) for run, by in parts.items() for p in by if origin.get(p) != run]
    cnt = Counter(origin.values())
    notes.append("C1 merged file checked row by row: each row equals (as parsed JSON) exactly one source row; "
                 + ", ".join(f"{v} from `{k}`" for k, v in sorted(cnt.items()))
                 + (f". Source rows not in the merge: {unmerged}" if unmerged else ". No source row is left out") + ".")
    return rows, origin


def is_solved(cfg: str, r: dict) -> bool:
    return cfg != "C2" and r["status"] == "solved"


def kappa_attempts(cfg: str, r: dict) -> list[str]:
    """kappa classes of all failed attempts of the row (plan 4.5)."""
    if cfg == "C1":
        out = []
        for a in r.get("attempts", []):
            if a.get("reason") == "accepted":
                continue
            msgs = tuple((0, 0, t) for t in a.get("lean_errors") or [])
            out.append(classify(Trace(str(a.get("reason")), msgs, None)))
        return out
    if cfg == "C2":
        return list((r.get("check") or {}).get("error_classes") or [])
    return list(r.get("error_classes") or [])


def plan_segments(events: list[dict]) -> list[list[dict]]:
    """Split a problem's events at each new plan (plan_proposed / plan_fallback after work began)."""
    segs: list[list[dict]] = [[]]
    for e in events:
        if e["event"] in ("plan_proposed", "plan_fallback"):
            if any(x["event"] in ("subgoal_solved", "critique") for x in segs[-1]):
                segs.append([])
        segs[-1].append(e)
    return segs


def subgoal_attempts(events: list[dict]) -> list[dict]:
    """Per (plan segment, LLM-attempted subgoal): attempt at which it was solved (or None), max attempt reached."""
    out = []
    for seg in plan_segments(events):
        state: dict[int, dict] = {}
        for e in seg:
            if e["event"] == "subgoal_solved" and e.get("solved_by") == "llm":
                s = state.setdefault(e["subgoal"], {"solved_at": None, "reached": 0})
                s["solved_at"] = e["attempts"]
                s["reached"] = max(s["reached"], e["attempts"])
            elif e["event"] == "critique" and e.get("subgoal") is not None:
                s = state.setdefault(e["subgoal"], {"solved_at": None, "reached": 0})
                s["reached"] = max(s["reached"], e["attempts"])
        out.extend(state.values())
    return out


def accepted_plan(events: list[dict]):
    """(max attempts, subgoal_solved events) over the last plan segment of a solved problem."""
    last = plan_segments(events)[-1]
    sg = [e for e in last if e["event"] == "subgoal_solved"]
    return max([e.get("attempts", 0) for e in sg], default=0), sg


# ----------------------------------------------------------------------------- formatting
def pct(x: float) -> str:
    return "—" if x != x else f"{100 * x:.1f}"


def ci_pct(lo: float, hi: float) -> str:
    return f"[{pct(lo)}, {pct(hi)}]"


def pval(p: float) -> str:
    return f"{p:.4f}" if p >= 1e-4 else f"{p:.1e}"


def num(x: float, d: int = 2) -> str:
    return "—" if x != x else f"{x:,.{d}f}"


def md_table(header, rows) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


TEX_MAP = [("\\", r"\textbackslash{}"), ("_", r"\_"), ("%", r"\%"), ("&", r"\&"), ("#", r"\#"),
           ("Δ̂", r"$\hat\Delta$"), ("Δ", r"$\Delta$"), ("κ", r"$\kappa$"), ("≤", r"$\le$"), ("≥", r"$\ge$"),
           ("—", "---"), ("–", "--"), ("×", r"$\times$"), ("−", "$-$"), ("p̂", r"$\hat p$"),
           ("B⁺", r"$B^{+}$"), ("C̄", r"$\bar C$"), ("±", r"$\pm$"), ("·", r"$\cdot$"), ("^(k−1)", r"$^{k-1}$"),
           ("<", "$<$"), (">", "$>$"), ("[", "{[}"), ("]", "{]}")]


def tex_escape(s: str) -> str:
    import re
    s = s.replace("^(k−1)", "\x00")
    s = re.sub(r"(?<![A-Za-z])(SR|FPR|Δ|C̄)_([A-Za-z0-9]+)", lambda m: m.group(1) + "\x01" + m.group(2) + "\x02", s)
    for a, b in TEX_MAP:
        s = s.replace(a, b)
    s = s.replace("\x01", r"$_{\mathrm{").replace("\x02", "}}$")
    return s.replace("\x00", r"$^{k-1}$").replace("$$", "")


def tex_table(header, rows, caption, label, align=None, wide=False, notes=None) -> str:
    env = "table*" if wide else "table"
    align = align or ("l" + "c" * (len(header) - 1))
    size = [r"\scriptsize", r"\setlength{\tabcolsep}{3pt}"] if wide else [r"\footnotesize", r"\setlength{\tabcolsep}{3pt}"]
    lines = [rf"\begin{{{env}}}[t]", r"\centering", *size,
             rf"\caption{{{tex_escape(caption)}}}", rf"\label{{tab:{label}}}",
             rf"\begin{{tabular}}{{{align}}}", r"\toprule",
             " & ".join(tex_escape(h) for h in header) + r" \\", r"\midrule"]
    for r in rows:
        lines.append(r"\midrule" if r == "MIDRULE" else " & ".join(tex_escape(c) for c in r) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    if notes:
        lines += [r"", r"\vspace{2pt}", rf"\parbox{{\linewidth}}{{\scriptsize {tex_escape(notes)}}}"]
    lines.append(rf"\end{{{env}}}")
    return "\n".join(lines)


class Out:
    def __init__(self):
        self.md: list[str] = []
        self.tex: list[str] = []

    def table(self, title, header, rows, caption, label, *, note=None, wide=False, align=None):
        self.md += [f"### {title}", "", md_table(header, [r for r in rows if r != "MIDRULE"]), ""]
        if note:
            self.md += [f"*Note.* {note}", ""]
        self.tex += [tex_table(header, rows, caption, label, align=align, wide=wide, notes=note), ""]

    def text(self, s: str, tex: str | None = None):
        self.md += [s, ""]
        if tex:
            self.tex += [tex, ""]


# ----------------------------------------------------------------------------- loading + checks
def load(mode: str, notes: list[str]) -> dict:
    meta = {p["id"]: p for p in read_jsonl(ROOT / "data" / "eval.jsonl")}
    sources = TEST_SOURCES if mode == "test" else DEV_SOURCES
    data, origin = {}, {}
    for cfg in CONFIGS:
        if cfg == "C1" and mode == "test":
            data[cfg], origin[cfg] = select_c1(notes)
        else:
            data[cfg], origin[cfg] = select_rows(sources[cfg], notes)
    expected = sorted(p for p, m in meta.items() if m.get("split") == mode)
    checks = []
    for cfg in CONFIGS:
        ids = sorted(data[cfg])
        if mode == "test" and ids != expected:
            raise SystemExit(f"{cfg}: ids differ from the {len(expected)} test ids: missing "
                             f"{sorted(set(expected) - set(ids))}, extra {sorted(set(ids) - set(expected))}")
        rs = data[cfg].values()
        checks.append([cfg, str(len(ids)), ", ".join(f"{s}: {v}" for s, v in sorted(Counter(r["status"] for r in rs).items())),
                       str(sum(1 for r in rs if r.get("lean_down"))),
                       str(sum(1 for r in rs if r.get("split") != mode or r.get("config") != cfg)),
                       str(sum(1 for p, r in data[cfg].items()
                               if (r.get("area"), r.get("difficulty")) != (meta[p]["area"], meta[p]["difficulty"])))])
    if mode != "test":  # dev stand-ins: restrict the verified configs to their common ids
        common = sorted(set.intersection(*(set(data[c]) for c in VERIFIED)))
        for cfg in VERIFIED:
            data[cfg] = {p: data[cfg][p] for p in common}
        c2_missing = [p for p in common if p not in data["C2"]]
        for p in c2_missing:  # dev only: C2 stand-in covers 3 problems; pad as not claimed
            data["C2"][p] = {"problem_id": p, "status": "not_claimed", "claimed_solved": False, "check": None,
                             "llm": {"llm_calls": 0, "cached_calls": 0, "tokens": {"prompt": 0, "completion": 0},
                                     "cost_usd": 0.0, "cost_usd_nominal": 0.0}, "wall_s": 0.0}
            origin["C2"][p] = DEV_SOURCES["C2"][0][0]
        data["C2"] = {p: data["C2"][p] for p in common}
        expected = common
    runs_used = sorted({run for cfg in CONFIGS for run in set(origin[cfg].values())})
    rc_rows, ref = [], None
    for run in runs_used:
        c = json.load(open(RUNS / run / "run_config.json", encoding="utf-8"))
        models = json.dumps(c["models"]["roles"], sort_keys=True)
        key = (c["profile"], c["seed"], c["dataset"]["split"], c["lean"]["lean_toolchain"], c["lean"]["mathlib_rev"], models)
        ref = ref or key
        rc_rows.append([run, c["config"], c["profile"], str(c["seed"]), c["dataset"]["split"], c["git"]["commit"][:7],
                        ("dirty (plan file only)" if c["git"]["changed_files"] == ["docs/analysis_plan.md"]
                         else "dirty: " + ", ".join(c["git"]["changed_files"])) if c["git"]["dirty"] else "clean",
                        str(c["lean"]["runner"].get("degraded")), str(c.get("status", "?")),
                        "same" if key == ref else "DIFFERENT"])
        if key != ref:
            notes.append(f"run_config mismatch in `{run}` (profile/seed/split/Lean/models).")
    traces = {run: load_traces(run) for run in runs_used}
    ids = expected
    return {"mode": mode, "meta": meta, "data": data, "origin": origin, "ids": ids, "n": len(ids),
            "checks": checks, "rc_rows": rc_rows, "traces": traces,
            "solved": {c: np.array([is_solved(c, data[c][p]) for p in ids]) for c in CONFIGS}}


def events(ctx, c, p):
    return ctx["traces"][ctx["origin"][c][p]].get(p, [])


def c2_claims(ctx) -> dict:
    ids, c2 = ctx["ids"], ctx["data"]["C2"]
    claimed = [p for p in ids if c2[p].get("claimed_solved")]
    st = {p: (c2[p].get("check") or {}).get("status") for p in claimed}
    left = [p for p in claimed if st[p] in ("provider_error", "budget_stop", "error", None)
            or c2[p].get("lean_down") or (c2[p].get("check") or {}).get("lean_down")]
    ver = [p for p in claimed if st[p] == "verified" and p not in left]
    nver = [p for p in claimed if p not in ver and p not in left]
    return {"claimed": claimed, "status": Counter(st.values()), "verified": ver, "not_verified": nver, "left_out": left}


# ----------------------------------------------------------------------------- tables
def t_success(ctx, out):
    n, solved, b = ctx["n"], ctx["solved"], c2_claims(ctx)
    rows = []
    for c in VERIFIED:
        k = int(solved[c].sum())
        rows.append([c, f"{k}/{n}", pct(k / n), ci_pct(*wilson(k, n)), "—", "—"])
    k2, nv, fn = len(b["claimed"]), len(b["not_verified"]), len(b["verified"]) + len(b["not_verified"])
    rows.append(["C2 (Baseline B)", "—", "—", "—", f"{k2}/{n} = {pct(k2 / n)} {ci_pct(*wilson(k2, n))}",
                 f"{nv}/{fn} = {pct(nv / fn if fn else float('nan'))} {ci_pct(*wilson(nv, fn))}"])
    out.table("Table 1. Verified success rate (SR) and Baseline B",
              ["Config", "Solved", "SR (%)", "Wilson 95 % CI (%)", "C2 claimed rate (%) [Wilson]", "FPR_B (%) [Wilson]"], rows,
              "Verified success rate SR with Wilson 95 % intervals (C1, C3–C5; pass@1 = SR, one run per problem), and the "
              "claimed rate and FPR_B (Eq. 16) of Baseline B (C2).", "sr", wide=True,
              note=(f"C2 is not verified, so it has no SR; it enters only through its claimed rate B⁺/n and FPR_B (plan §7.1). "
                    f"Of the {k2} claimed proofs the formal check verified {len(b['verified'])} and did not verify {nv}; "
                    f"{len(b['left_out'])} left out of FPR_B."))
    pool = sorted(b["not_verified"])
    if len(pool) <= 10:
        review, how = pool, f"all {len(pool)} (≤ 10)"
    else:
        review, how = sorted(random.Random(SEED).sample(pool, 10)), "10 drawn with random.Random(2026).sample(sorted ids, 10)"
    st = ", ".join(f"{s}: {v}" for s, v in sorted(b["status"].items(), key=lambda x: str(x[0])))
    out.table("Table 1b. Baseline B claim check (FPR_B, Eq. 16)", ["Quantity", "Value"],
              [["Problems", str(n)], ["Claimed (B⁺)", str(k2)], ["Not claimed", str(n - k2)],
               ["Check status among B⁺", st], ["Verified (Acc on the assembled theorem)", str(len(b["verified"]))],
               ["Not verified (budget_exhausted included)", str(nv)],
               ["Left out of FPR_B (provider_error, budget_stop, error, lean_down)", str(len(b["left_out"]))],
               ["FPR_B = not verified / (verified + not verified)", f"{nv}/{fn} = {pct(nv / fn if fn else float('nan'))} % {ci_pct(*wilson(nv, fn))}"],
               ["B⁺ problems for the manual review (§4.2)", how + " (listed below)"]],
              "Baseline B: claimed proofs and their formal check. FPR_B is an upper bound on the true false-positive rate.",
              "fprb", align="ll", wide=True, note="Manual review set (B⁺ not verified): " + ", ".join(review) + ".")


def comparison_rows(ctx, comps, corrected):
    solved, rows = ctx["solved"], []
    for hid, a, b in comps:
        nb, nc, p = mcnemar_exact(solved[a], solved[b])
        d = solved[a].mean() - solved[b].mean()
        row = [hid, f"{a} vs {b}", str(nb), str(nc), f"{100 * d:+.1f}"]
        if corrected:
            lo, hi = paired_boot_diff(solved[a].astype(float), solved[b].astype(float))
            row += [f"[{100 * lo:+.1f}, {100 * hi:+.1f}]", pval(p), pval(bonferroni(p, len(PLANNED))), "yes" if bonferroni_reject(p, len(PLANNED)) else "no"]
        else:
            row += [pval(p)]
        rows.append(row)
    return rows


def t_mcnemar(ctx, out):
    out.table("Table 2. Planned comparisons H1–H3 (exact McNemar, Bonferroni ×3)",
              ["ID", "Comparison", "b", "c", "ΔSR (pp)", "Paired bootstrap 95 % CI (pp)", "Exact p", "Bonferroni p", "Reject at 0.05/3"],
              comparison_rows(ctx, PLANNED, True),
              "Planned comparisons: two-sided exact McNemar test on outcomes paired by problem, Bonferroni-corrected for 3 tests "
              "(reject at p < 0.0167; adjusted p = min(1, 3p)). b: first configuration solved and second not; c: the reverse. "
              "ΔSR = SR(first) − SR(second), percentage points, with a paired bootstrap 95 % CI (10 000 resamples of problems, "
              "seed 2026, percentile method).", "mcnemar", wide=True,
              note=f"n = {ctx['n']}, so power is low: a non-significant result is no evidence of a difference, not evidence of no difference. "
                   "H2 is Δ_RAG = SR_C5 − SR_C3; H3 is the Critic contribution SR_C5 − SR_C4.")
    out.table("Table 2b. Exploratory comparisons (uncorrected, no claims)",
              ["ID", "Comparison", "b", "c", "ΔSR (pp)", "Exact p (uncorrected)"], comparison_rows(ctx, EXPLORATORY, False),
              "Exploratory comparisons listed in plan §3: exact McNemar, no correction, no claims.", "mcnemar-expl")


def t_cells(ctx, out):
    ids, meta, data = ctx["ids"], ctx["meta"], ctx["data"]
    cfgs = VERIFIED + ["C2"]

    def val(c, pids):
        if c == "C2":
            return sum(1 for p in pids if data["C2"][p].get("claimed_solved"))
        return sum(1 for p in pids if is_solved(c, data[c][p]))
    rows = []
    for d in DIFFS:
        for a in AREAS:
            pids = [p for p in ids if meta[p]["difficulty"] == d and meta[p]["area"] == a]
            rows.append([d, AREA_LABEL[a], str(len(pids))] + [("—" if not pids else f"{val(c, pids)}/{len(pids)}") for c in cfgs])
    out.table("Table 3. Solved / n per cell of the 3 × 3 grid (descriptive)",
              ["Difficulty", "Area", "n"] + [c if c != "C2" else "C2 (claimed)" for c in cfgs], rows,
              "Per-cell solved/n (descriptive: no tests, no intervals per cell). C2 counts claimed (self-assessed) solutions, "
              "not verified ones; — marks an empty cell.", "cells", align="llc" + "c" * len(cfgs))
    groups = [("difficulty", v) for v in DIFFS] + [("area", v) for v in AREAS]
    hdr = ["Config"] + [f"{AREA_LABEL.get(v, v)} (n = {sum(1 for p in ids if meta[p][k] == v)})" for k, v in groups]
    rows = []
    for c in cfgs:
        r1, r2 = [c if c != "C2" else "C2 (claimed)"], [""]
        for k, v in groups:
            pids = [p for p in ids if meta[p][k] == v]
            x = val(c, pids)
            r1.append(f"{x}/{len(pids)}")
            r2.append(f"{pct(x / len(pids))} {ci_pct(*wilson(x, len(pids)))}")
        rows += [r1, r2]
    out.table("Table 3b. SR by difficulty and by area (margins, Wilson 95 % CI)", hdr, rows,
              "Margins of the grid by difficulty and by area: solved/n, then SR % [Wilson 95 % CI] (descriptive; no tests). "
              "C2: claimed solutions.", "margins", wide=True)


def _ts(e):
    from datetime import datetime
    return datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))


def own_wall(ctx, c, p) -> float:
    """Wall seconds of the configuration itself. C2: up to claim_check_start, since C2 efficiency figures
    exclude the claim check (Clarification 2026-11-03); its row wall_s includes the check."""
    r = ctx["data"][c][p]
    if c != "C2":
        return r["wall_s"]
    ev = events(ctx, c, p)
    st = [e for e in ev if e["event"] == "problem_start"]
    cs = [e for e in ev if e["event"] == "claim_check_start"]
    if st and cs:
        return (_ts(cs[0]) - _ts(st[0])).total_seconds()
    return r["wall_s"]


def ledger_index():
    led, cnt = defaultdict(float), Counter()
    for e in read_jsonl(ROOT / "data" / "spend_ledger.jsonl"):
        led[(e["run_id"], e.get("problem_id"))] += e["cost_usd"]
        cnt[(e["run_id"], e.get("problem_id"))] += 1
    return led, cnt


def t_efficiency(ctx, out, notes):
    ids, data, origin, n = ctx["ids"], ctx["data"], ctx["origin"], ctx["n"]
    led, led_n = ledger_index()
    per_solved, per_problem, totals, medians = [], [], [], []
    for c in CONFIGS:
        R = [data[c][p] for p in ids]
        s = (ctx["solved"][c] if c != "C2" else np.array([bool(r.get("claimed_solved")) for r in R])).astype(float)
        calls = np.array([r["llm"]["llm_calls"] for r in R], dtype=float)
        cached = np.array([r["llm"]["cached_calls"] for r in R], dtype=float)
        toks = np.array([r["llm"]["tokens"]["prompt"] + r["llm"]["tokens"]["completion"] for r in R], dtype=float)
        nom = np.array([r["llm"]["cost_usd_nominal"] for r in R], dtype=float)
        real = np.array([r["llm"]["cost_usd"] for r in R], dtype=float)
        wall = np.array([own_wall(ctx, c, p) for p in ids], dtype=float) / 60
        lf = sum(r["llm"].get("length_finishes", 0) for r in R)
        lfc = sum((((r.get("check") or {}).get("llm") or {}).get("length_finishes", 0)) for r in R)
        if lf or lfc:
            notes.append(f"{c}: {lf} LLM responses ended at max_tokens (finish_reason length) in "
                         f"{sum(1 for r in R if r['llm'].get('length_finishes', 0))} rows"
                         + (f"; {lfc} more in the claim check" if c == "C2" else "") + ".")
        ns = int(s.sum())
        drops = []

        def ratio(x, d=2):
            lo, hi, drop = boot_ratio(x, s)
            drops.append(drop)
            return f"{num(x.sum() / ns if ns else float('nan'), d)} [{num(lo, d)}, {num(hi, d)}]"

        def mean_ci(x, d=2):
            return f"{num(x.mean(), d)} [{num(boot_mean(x)[0], d)}, {num(boot_mean(x)[1], d)}]"
        per_solved.append([c if c != "C2" else "C2 (per claimed)", str(ns), ratio(calls), ratio(toks / 1000, 1),
                           ratio(nom, 4), ratio(real, 4), ratio(wall, 1)])
        per_problem.append([c, mean_ci(calls), mean_ci(toks / 1000, 1), mean_ci(nom, 4), mean_ci(real, 4), mean_ci(wall, 1)])
        chk = [((r.get("check") or {}).get("llm") or {}) for r in R] if c == "C2" else []
        led_total = sum(v for (run, _), v in led.items() if run in LEDGER_RUNS[c]) if ctx["mode"] == "test" else float("nan")
        totals.append([c, str(int(calls.sum())), str(int(cached.sum())), f"{100 * cached.sum() / calls.sum():.1f}",
                       num(toks.sum() / 1e6, 3), num(nom.sum(), 4), num(real.sum(), 4), num(led_total, 4),
                       (f"{sum(x.get('llm_calls', 0) for x in chk)} calls ({sum(x.get('cached_calls', 0) for x in chk)} cached), "
                        f"{num(sum(x.get('cost_usd_nominal', 0.0) for x in chk), 4)} nominal, "
                        f"{num(sum(x.get('cost_usd', 0.0) for x in chk), 4)} real") if c == "C2" else "—"])
        m, q1, q3 = quartiles(calls)
        w, w1, w3 = quartiles(wall)
        medians.append([c, f"{num(m, 1)} [{num(q1, 1)}, {num(q3, 1)}]", f"{num(w, 1)} [{num(w1, 1)}, {num(w3, 1)}]"])
        if max(drops):
            notes.append(f"{c}: {max(drops)} of {N_BOOT} bootstrap resamples had no solved problem; dropped from its per-solved CIs.")
        if ctx["mode"] == "test":
            diffs = []
            for p in ids:
                lv = led.get((origin[c][p], p), 0.0)
                rv = data[c][p]["llm"]["cost_usd"] + ((((data[c][p].get("check") or {}).get("llm") or {}).get("cost_usd", 0.0)) if c == "C2" else 0.0)
                if abs(lv - rv) > 1e-6:
                    diffs.append(f"{p}: row {rv:.6f} vs ledger {lv:.6f}")
            if diffs:
                notes.append(f"{c}: real cost of {len(diffs)} kept rows differs from the ledger: " + "; ".join(diffs[:8]))
            extra = [(run, pp, v) for (run, pp), v in sorted(led.items())
                     if run in LEDGER_RUNS[c] and origin[c].get(pp) != run]
            if extra:
                notes.append(f"{c}: ledger spend in no kept row = {sum(v for *_, v in extra):.5f} USD (real cost only): "
                             + "; ".join(f"`{r}` / {pp}: {v:.5f} USD, {led_n[(r, pp)]} paid calls" for r, pp, v in extra) + ".")
    out.table("Table 4. Efficiency per solved problem (RNF-3)",
              ["Config", "Solved", "Calls / solved", "k tokens / solved", "Nominal USD / solved", "Real USD / solved", "Wall min / solved"],
              per_solved,
              "Efficiency per solved problem: sum over all 30 problems (failed ones included) divided by the number solved, with 95 % "
              "bootstrap CIs (10 000 resamples of problems, seed 2026, percentile). Nominal cost (cache hits priced as fresh calls) "
              "is the compared figure (plan §1); real cost is from the kept rows only.", "eff-solved", wide=True,
              note="C2 has no verified solutions: its ratio uses claimed solutions and excludes the claim check (calls, tokens, cost "
                   "and wall time up to claim_check_start).")
    out.table("Table 4b. Efficiency per problem (means)",
              ["Config", "Calls / problem", "k tokens / problem", "Nominal USD / problem", "Real USD / problem", "Wall min / problem"],
              per_problem, "Mean per problem with 95 % bootstrap CIs (C2 excludes the claim check).", "eff-problem", wide=True)
    out.table("Table 4c. Totals, cache use and real cost",
              ["Config", "LLM calls", "Cached calls", "Cached (%)", "Tokens (M)", "Nominal USD (rows)", "Real USD (rows)",
               "Real USD (ledger, all runs)"], [t[:-1] for t in totals],
              "Totals over the 30 kept rows. The ledger column sums every paid call of the configuration runs, including attempts "
              "discarded by the merge (Deviations) and, for C2, the claim check.", "totals", wide=True,
              note="C2 claim check (not in the C2 figures of the other columns): " + totals[1][-1] + ".")
    c5c, c5s = sum(data["C5"][p]["llm"]["llm_calls"] for p in ids), int(ctx["solved"]["C5"].sum())
    v = c5c / c5s if c5s else float("nan")
    out.text(f"**RNF-3 (C̄_solved ≤ 20 for C5):** C̄_solved = {c5c}/{c5s} = {num(v, 2)} calls per solved problem: "
             f"**{'met' if v <= 20 else 'not met'}**.",
             tex=rf"% RNF-3: C5 calls per solved = {c5c}/{c5s} = {num(v, 2)} ({'met' if v <= 20 else 'not met'})")
    out.table("Table 4d. Median [IQR] of LLM calls and wall time per problem (supplementary)",
              ["Config", "Calls, median [Q1, Q3]", "Wall min, median [Q1, Q3]"], medians,
              "Median and interquartile range of LLM calls and wall time per problem (supplementary; not in the plan).", "medians")
    rows = []
    for c in CONFIGS:
        row = [c]
        for d in DIFFS:
            w = [own_wall(ctx, c, p) / 60 for p in ids if ctx["meta"][p]["difficulty"] == d]
            if not w:
                row.append("—")
                continue
            m, q1, q3 = quartiles(w)
            row.append(f"{num(statistics.mean(w), 1)} / {num(m, 1)} [{num(q1, 1)}, {num(q3, 1)}]")
        rows.append(row)
    out.table("Table 5. Wall time per problem by difficulty, minutes (descriptive)",
              ["Config"] + [f"{d} (n = {sum(1 for p in ids if ctx['meta'][p]['difficulty'] == d)}): mean / median [Q1, Q3]" for d in DIFFS],
              rows, "Wall-clock time per problem by difficulty in minutes, mean / median [Q1, Q3] (descriptive; not in the plan). "
              "C2 excludes the claim check (time to claim_check_start in trace.jsonl).", "wall-diff", wide=True)


def t_refinement(ctx, out, notes):
    ids, n, solved = ctx["ids"], ctx["n"], ctx["solved"]
    maxatt = {p: accepted_plan(events(ctx, "C5", p))[0] for i, p in enumerate(ids) if solved["C5"][i]}
    srk = {k: sum(1 for m in maxatt.values() if m <= k) for k in range(4)}
    subs = [s for p in ids for s in subgoal_attempts(events(ctx, "C5", p))]
    first = [s for s in subs if s["reached"] >= 1]
    k1 = sum(1 for s in first if s["solved_at"] == 1)
    p_hat = k1 / len(first) if first else float("nan")
    rows = []
    for k in (1, 2, 3):
        reached = sum(1 for s in first if s["reached"] >= k)
        at_k = sum(1 for s in first if s["solved_at"] == k)
        rows.append([str(k), f"{srk[k]}/{n} = {pct(srk[k] / n)} {ci_pct(*wilson(srk[k], n))}",
                     f"{100 * (srk[k] - srk[k - 1]) / n:+.1f}", pct(p_hat * (1 - p_hat) ** (k - 1)),
                     f"{at_k}/{len(first)} = {pct(at_k / len(first)) if first else '—'}",
                     f"{at_k}/{reached} = {pct(at_k / reached) if reached else '—'}"])
    k4 = int(solved["C4"].sum())
    out.table("Table 6. Refinement robustness: SR(k) of C5 vs the independence curve (Eq. 14)",
              ["k", "SR_C5(k) [Wilson] (%)", "Δ̂(k) = SR(k) − SR(k−1) (pp)", "Indep. Δ(k) = p̂(1−p̂)^(k−1) (%)",
               "Subgoals solved at attempt k", "Solved at k / reached k"], rows,
              "Refinement robustness. A C5 problem counts as solved at k if every subgoal of its accepted plan used at most k "
              "attempts (subgoal_solved events in trace.jsonl). p̂ is the first-attempt success rate of the LLM-attempted subgoals "
              "of C5 (all plans, all problems).", "srk", wide=True,
              note=(f"SR_C5(0) = {srk[0]}/{n} (accepted plan with no LLM subgoal attempt). p̂ = {k1}/{len(first)} = {pct(p_hat)} %. "
                    f"k = 1 from C4 (no retries, no Critic advice): {k4}/{n} = {pct(k4 / n)} % {ci_pct(*wilson(k4, n))}. "
                    "The last two columns are subgoal-level diagnostics (supplementary)."))
    if srk[3] != int(solved["C5"].sum()):
        notes.append(f"SR_C5(3) = {srk[3]}/{n} differs from the C5 solved count {int(solved['C5'].sum())}: "
                     f"max attempts per solved problem {maxatt}.")
    over = {p: m for p, m in maxatt.items() if m > 3}
    if over:
        notes.append(f"C5 accepted plans with more than 3 attempts on a subgoal: {over}.")
    ctx["maxatt"] = maxatt
    ctx["srk"] = {"srk": srk, "n": n, "p_hat": p_hat, "c4": k4}


BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#1f1f1e", "#5f5e58", "#e4e3de"  # validated pair (dataviz)


def figure_srk(ctx, outdir: Path) -> list[Path]:
    """Plan §7.5: SR(k) of C5 and Δ̂(k) against the independence curve of Eq. 14 (PDF + PNG).

    Same quantities as Table 6: (a) SR_C5(k), k = 0..3, with Wilson 95 % CIs, and k = 1 from C4;
    (b) Δ̂(k) = SR(k) − SR(k−1) (problem level) vs Δ(k) = p̂(1 − p̂)^(k−1) (subgoal level).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = ctx["srk"]
    srk, n, p_hat = d["srk"], d["n"], d["p_hat"]
    ks = [0, 1, 2, 3]
    sr = [100 * srk[k] / n for k in ks]
    ci = [wilson(srk[k], n) for k in ks]
    err = [[s - 100 * lo for s, (lo, _) in zip(sr, ci)], [100 * hi - s for s, (_, hi) in zip(sr, ci)]]
    c4 = 100 * d["c4"] / n
    c4_lo, c4_hi = wilson(d["c4"], n)
    gain = [100 * (srk[k] - srk[k - 1]) / n for k in (1, 2, 3)]
    indep = [100 * p_hat * (1 - p_hat) ** (k - 1) for k in (1, 2, 3)]

    plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK, "axes.linewidth": 0.6,
                         "pdf.fonttype": 42})
    fig, (a, b) = plt.subplots(1, 2, figsize=(3.5, 1.9), constrained_layout=True)
    for ax in (a, b):
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    a.errorbar(ks, sr, yerr=err, color=BLUE, marker="o", markersize=4, linewidth=1.5, elinewidth=0.8, capsize=2,
               label="C5")
    a.errorbar([1.12], [c4], yerr=[[c4 - 100 * c4_lo], [100 * c4_hi - c4]], color=ORANGE, marker="s",
               markersize=4, linestyle="none", elinewidth=0.8, capsize=2, label="C4 (k = 1)")
    a.set(xticks=ks, xlabel="k (max attempts per subgoal)", ylabel="SR (%)", ylim=(0, 100))
    a.set_title("(a) SR(k)", fontsize=8)
    a.legend(frameon=False, loc="upper left", fontsize=7, handlelength=1.2)
    kk = [1, 2, 3]
    b.plot(kk, gain, color=BLUE, marker="o", markersize=4, linewidth=1.5, label=r"$\hat\Delta(k)$, C5")
    b.plot(kk, indep, color=ORANGE, marker="s", markersize=4, linewidth=1.5, linestyle="--",
           label=r"$\hat p(1-\hat p)^{k-1}$")
    b.set(xticks=kk, xlabel="k", ylabel="gain (pp)", ylim=(0, max(50, 10 * math.ceil(max(gain + indep) / 10))))
    b.set_title("(b) marginal gain", fontsize=8)
    b.legend(frameon=False, loc="upper right", fontsize=7, handlelength=1.6)
    figdir = outdir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    paths = [figdir / "srk_curve.pdf", figdir / "srk_curve.png"]
    fig.savefig(paths[0], metadata={"CreationDate": None, "ModDate": None, "Producer": None, "Creator": None})
    fig.savefig(paths[1], dpi=300, metadata={"Software": None})
    plt.close(fig)
    return paths


def t_kappa(ctx, out):
    ids, meta, data = ctx["ids"], ctx["meta"], ctx["data"]
    rows, arows = [], []
    for c in CONFIGS:
        cl, by_area = Counter(), defaultdict(Counter)
        for p in ids:
            ks = kappa_attempts(c, data[c][p])
            cl.update(ks)
            by_area[meta[p]["area"]].update(ks)
        lab = c if c != "C2" else "C2 (claim check)"
        tot = sum(cl.values())
        rows.append([lab, str(tot)] + [f"{cl[e]} ({pct(cl[e] / tot) if tot else '—'})" for e in ERROR_CLASSES])
        if arows:
            arows.append("MIDRULE")
        for a in AREAS:
            t = sum(by_area[a].values())
            arows.append([lab, AREA_LABEL[a], str(t)] + [f"{by_area[a][e]} ({pct(by_area[a][e] / t) if t else '—'})" for e in ERROR_CLASSES])
    hdr = [e.replace("_", " ") for e in ERROR_CLASSES]
    out.table("Table 7. κ error taxonomy over all failed attempts (Eq. 11)", ["Config", "Failed attempts"] + hdr, rows,
              "Error taxonomy: count (relative frequency, %) of each κ class of Eq. 11 over all failed attempts in the kept rows.",
              "kappa", wide=True,
              note="C3–C5: classes recorded by the Critic. C1: attempts classified offline with policy.classify (first 3 Lean "
                   "messages, 300 characters each, as stored; no k_max escalation). C2 has no verified attempts of its own: its row "
                   "is the claim-check loop.")
    out.table("Table 7b. κ taxonomy per area", ["Config", "Area", "Failed attempts"] + hdr, arows,
              "Error taxonomy per configuration and area: count (relative frequency within the row, %).", "kappa-area", wide=True)
    rows = []
    for c in CONFIGS:
        f = Counter()
        for p in ids:
            r = data[c][p]
            if c == "C2":
                f[f"claimed, check {(r.get('check') or {}).get('status')}" if r.get("claimed_solved") else r["status"]] += 1
            elif not is_solved(c, r):
                st = r.get("stage")
                st = st.split(":")[0] if isinstance(st, str) else None
                f[r["status"] + (f" @ {st}" if st else "")] += 1
        rows.append([c, str(sum(f.values())), "; ".join(f"{k}: {v}" for k, v in sorted(f.items(), key=lambda x: (-x[1], x[0])))])
    out.table("Table 7c. Final failure reason per problem (status @ stage)", ["Config", "Problems", "status @ stage: count"], rows,
              "Final status and stage of the unsolved problems (C2: claim and check outcome of all problems). Stages "
              "verify:sgN, formalize:sgN, ... are grouped by prefix.", "final-fail", align=r"lcp{0.7\linewidth}", wide=True)


def t_prepass(ctx, out):
    rows = []
    for c in GRAPH:
        acc, allv, zero = Counter(), Counter(), 0
        for p in ctx["ids"]:
            ev = events(ctx, c, p)
            allv.update(x.get("solved_by") for x in ev if x["event"] == "subgoal_solved")
            if is_solved(c, ctx["data"][c][p]):
                sg = accepted_plan(ev)[1]
                acc.update(x.get("solved_by") for x in sg)
                zero += not any(x.get("solved_by") == "llm" for x in sg)
        ta, tl = sum(acc.values()), sum(allv.values())
        rows.append([c, f"{acc['prepass']}/{ta} = {pct(acc['prepass'] / ta) if ta else '—'}",
                     f"{acc['llm']}/{ta} = {pct(acc['llm'] / ta) if ta else '—'}",
                     f"{allv['prepass']}/{tl} = {pct(allv['prepass'] / tl) if tl else '—'}", str(zero), str(int(ctx["solved"][c].sum()))])
        other = set(acc) | set(allv) - {"prepass", "llm"}
        if other - {"prepass", "llm"}:
            rows[-1][-1] += f" (other solved_by: {sorted(map(str, other - {'prepass', 'llm'}))})"
    out.table("Table 8. Subgoals closed by the tactic pre-pass vs the LLM (supplementary)",
              ["Config", "Pre-pass (accepted plans)", "LLM (accepted plans)", "Pre-pass (all subgoal_solved)",
               "Solved with no LLM subgoal", "Solved"], rows,
              "Share of subgoals closed by the deterministic tactic pre-pass vs the LLM (solved_by in trace.jsonl): a possible "
              "confound shared by C3–C5 (supplementary; not in the plan).", "prepass", wide=True)


def t_status(ctx, out, notes):
    ids, data, meta = ctx["ids"], ctx["data"], ctx["meta"]
    rows = []
    for c in CONFIGS:
        st = Counter(r["status"] for r in data[c].values())
        rows.append([c] + [str(st.get(s, 0)) for s in INFRA] + [str(sum(1 for r in data[c].values() if r.get("lean_down")))])
    out.table("Table 9. Infrastructure and error statuses in the kept rows (plan §5, §7.7)", ["Config"] + list(INFRA) + ["lean_down"],
              rows, "Counts of provider_error, budget_stop, error-type statuses and lean_down rows in the analysed rows "
              "(degraded runs: see the runs table).", "infra", wide=True)
    try:
        import yaml
        review = yaml.safe_load(open(ROOT / "data" / "curation" / "fidelity_review.yaml", encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        review = {}
        notes.append(f"fidelity_review.yaml not read: {exc!r}")
    fid = review.get("notes") or {}
    post = review.get("post_test_review") or {}
    verdicts = post.get("verdicts") or {}

    def reviewed(p: str) -> str:
        v = verdicts.get(p)
        if v is None:
            return "pending"
        out_ = f"{post.get('date')}: {'counts as solved' if v.get('counts') else 'not counted as a solution'}"
        return out_ + (f"; report note: {' '.join(str(v['report_note']).split())}" if v.get("report_note") else "; no note")

    rows = [[p, ", ".join(c for c in VERIFIED if is_solved(c, data[c][p])), reviewed(p)]
            for p in ids if p in fid and any(is_solved(c, data[c][p]) for c in VERIFIED)]
    out.table("Table 10. Solved test problems with a fidelity note (plan §6)", ["Problem", "Solved by", "Manual review"],
              rows or [["—", "—", "—"]],
              "Problems solved on test by some configuration that carry a note in data/curation/fidelity_review.yaml, and the "
              "team's manual review (post_test_review in that file). The verified SR is unchanged by the review.", "fidelity",
              align=r"llp{0.5\linewidth}")
    rows = []
    for p in ids:
        r2 = data["C2"][p]
        c2s = f"claimed, {(r2.get('check') or {}).get('status')}" if r2.get("claimed_solved") else "not claimed"
        rows.append([p, meta[p]["difficulty"], AREA_LABEL[meta[p]["area"]]]
                    + ["1" if is_solved(c, data[c][p]) else "0" for c in VERIFIED] + [c2s])
    out.table("Table 11. Per-problem outcomes", ["Problem", "Difficulty", "Area"] + VERIFIED + ["C2 (claim, check)"], rows,
              "Per-problem outcomes on test (1 = verified solved).", "per-problem", wide=True, align="lll" + "c" * 4 + "l")


def t_hard(ctx, out):
    """Post hoc, descriptive view of the hard problems per configuration (not in the analysis plan)."""
    ids, data, meta = ctx["ids"], ctx["data"], ctx["meta"]
    hard = [p for p in ids if meta[p]["difficulty"] == "hard"]
    solved = {c: {p for p in hard if is_solved(c, data[c][p])} for c in VERIFIED}
    rows = [[p, AREA_LABEL[meta[p]["area"]]] + ["1" if p in solved[c] else "0" for c in VERIFIED]
            + [str(sum(p in solved[c] for c in VERIFIED))] for p in hard]
    rows += ["MIDRULE", ["Solved", ""] + [f"{len(solved[c])}/{len(hard)}" for c in VERIFIED] + [""]]
    none = [p for p in hard if not any(p in solved[c] for c in VERIFIED)]
    same = "the same" if solved["C1"] == solved["C5"] else "different"
    only = lambda a, b: ", ".join(sorted(solved[a] - solved[b])) or "none"  # noqa: E731
    note = (f"Post hoc, descriptive (not in the analysis plan): no tests, no intervals. 1 = verified solved. "
            f"C1 and C5 solve {same} hard problems ({len(solved['C1'])} and {len(solved['C5'])}); "
            f"{len(none)} hard problems are solved by no configuration ({', '.join(none)}); "
            f"C3 and C5 solve {len(solved['C3'])} and {len(solved['C5'])} hard problems, but only C3 solves "
            f"{only('C3', 'C5')} and only C5 solves {only('C5', 'C3')}.")
    out.table("Table 11b. Hard problems per configuration (supplementary; post hoc, descriptive, not in the analysis plan)",
              ["Problem", "Area"] + VERIFIED + ["Solved by"], rows,
              "Hard test problems per verified configuration (supplementary; post hoc, descriptive, not in the analysis plan).",
              "hard", note=note, align="ll" + "c" * (len(VERIFIED) + 1))


def t_candidates(ctx, out, notes):
    ids, data, s = ctx["ids"], ctx["data"], lambda c, p: is_solved(c, ctx["data"][c][p])
    cand = {
        "C5 solved, C4 not (Critic loop)": [p for p in ids if s("C5", p) and not s("C4", p)],
        "C4 solved, C5 not": [p for p in ids if s("C4", p) and not s("C5", p)],
        "C5 solved, C3 not (RAG)": [p for p in ids if s("C5", p) and not s("C3", p)],
        "C3 solved, C5 not": [p for p in ids if s("C3", p) and not s("C5", p)],
        "C5 solved, C1 not": [p for p in ids if s("C5", p) and not s("C1", p)],
        "C1 solved, C5 not": [p for p in ids if s("C1", p) and not s("C5", p)],
        "C2 claimed, check did not verify": c2_claims(ctx)["not_verified"],
    }
    lines = []
    for k, v in cand.items():
        det = []
        for p in v:
            r = data["C5"][p]
            det.append(f"{p} (C5: {r['status']}, {r['llm']['llm_calls']} calls, replans {r.get('replans')}, "
                       f"max attempts {ctx.get('maxatt', {}).get(p, '-')})")
        lines.append(f"- {k}: " + ("; ".join(det) if det else "—"))
    out.text("### Discordant pairs (candidates for illustrative traces)\n\n" + "\n".join(lines))
    for c in GRAPH:
        bad = [p for p in ids if len(data[c][p].get("error_classes") or []) != sum(1 for x in events(ctx, c, p) if x["event"] == "critique")]
        if bad:
            notes.append(f"{c}: length of error_classes differs from the number of critique events for {bad}.")
    for c in CONFIGS:
        missing = [p for p in ids if not events(ctx, c, p)]
        if missing:
            notes.append(f"{c}: no trace events for {missing}.")


def analyse(mode: str, outdir: Path) -> None:
    notes: list[str] = []
    ctx = load(mode, notes)
    out = Out()
    out.text(f"# Tables for the `{mode}` split (n = {ctx['n']})\n\nGenerated by `docs/results/make_tables.py` from `runs/` "
             "(read-only), following `docs/analysis_plan.md`. Rates in %, intervals 95 %.")
    out.table("Runs used", ["Run", "Config", "Profile", "Seed", "Split", "Commit", "Tree", "Degraded", "Run status", "vs first run"],
              ctx["rc_rows"], "Runs used and their recorded configuration (run_config.json).", "runs", wide=True, align="llcccclccc")
    out.table("Row checks", ["Config", "Rows", "Status counts", "lean_down", "split/config mismatch", "area/difficulty mismatch"],
              ctx["checks"], "Integrity checks of the analysed rows.", "checks", align="lclccc", wide=True)
    t_success(ctx, out)
    t_mcnemar(ctx, out)
    t_cells(ctx, out)
    t_efficiency(ctx, out, notes)
    t_refinement(ctx, out, notes)
    t_kappa(ctx, out)
    t_prepass(ctx, out)
    t_status(ctx, out, notes)
    t_hard(ctx, out)
    t_candidates(ctx, out, notes)
    out.text("## Generated data notes\n\n" + "\n".join(f"- {x}" for x in notes))
    outdir.mkdir(parents=True, exist_ok=True)
    md = "\n".join(out.md) + "\n"
    notes_file = Path(__file__).resolve().parent / "method_notes.md"
    if mode == "test" and notes_file.exists():  # hand-written interpretation notes, appended verbatim
        md += "\n" + notes_file.read_text(encoding="utf-8")
    (outdir / "results.md").write_text(md, encoding="utf-8")
    head = "% Generated by docs/results/make_tables.py from runs/ (read-only). Needs only \\usepackage{booktabs}.\n\n"
    (outdir / "tables.tex").write_text(head + "\n".join(out.tex) + "\n", encoding="utf-8")
    figs = figure_srk(ctx, outdir)
    print(f"wrote {outdir / 'results.md'}, {outdir / 'tables.tex'} and {', '.join(str(f) for f in figs)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenerate the analysis tables from runs/ (read-only).")
    ap.add_argument("--dev", metavar="OUTDIR", help="dry run on dev stand-in runs, writing to OUTDIR")
    a = ap.parse_args()
    if a.dev:
        analyse("dev", Path(a.dev))
    else:
        analyse("test", Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
