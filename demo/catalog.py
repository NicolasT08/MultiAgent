"""Problems as the demo shows them (cards, filters), cost estimates, and the results tab.

Problems come from data/eval.jsonl (the study) and data/demo_problems.jsonl (extra problems,
never run before the demo: the "New" filter). A demo problem stops being "new" once any run in
runs/ contains it. Cards never show ids, splits or other internal names; PutnamBench informal
text is withheld (MAA permission).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from i18n import t

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ("new", "easy", "medium", "hard")
DIFFICULTIES = ("easy", "medium", "hard")
LATEX = [{"left": "$$", "right": "$$", "display": True}, {"left": "$", "right": "$", "display": False},
         {"left": "\\[", "right": "\\]", "display": True}, {"left": "\\(", "right": "\\)", "display": False}]
COMPETITIONS = {"amc12": "AMC 12", "amc12a": "AMC 12A", "amc12b": "AMC 12B", "aime": "AIME", "aimei": "AIME I",
                "aimeii": "AIME II", "imo": "IMO"}

__all__ = ["FILTERS", "LATEX", "card_markdown", "competition", "cost_line", "filter_problems", "load_problems",
           "results_markdown", "typical_costs"]


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ids_in_runs(root: Path = ROOT) -> set[str]:
    """Every problem id that appears in some runs/*/results.jsonl."""
    out: set[str] = set()
    for path in (root / "runs").glob("*/results.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.search(r'"problem_id":\s*"([^"]+)"', line)
            if m:
                out.add(m.group(1))
    return out


def load_problems(root: Path = ROOT) -> list[dict]:
    """Study problems and demo problems with presentation fields: `new`, `studied`."""
    seen = ids_in_runs(root)
    study = [{**r, "new": False, "studied": True} for r in _rows(root / "data" / "eval.jsonl")]
    demo = [{**r, "new": r["id"] not in seen, "studied": False} for r in _rows(root / "data" / "demo_problems.jsonl")]
    order = {d: i for i, d in enumerate(DIFFICULTIES)}
    return sorted(demo + study, key=lambda r: (not r["new"], order.get(r.get("difficulty"), 3), r["id"]))


def filter_problems(problems: list[dict], name: str) -> list[dict]:
    if name == "new":
        return [p for p in problems if p.get("new")]
    return [p for p in problems if p.get("difficulty") == name]


def competition(problem_id: str, lang: str) -> str:
    """'AMC 12A 2008 · Problem 25', 'IMO 1964 · Problem 1', 'Putnam 1966 · B4', 'Practice problem (MATH)'."""
    m = re.fullmatch(r"(amc12[ab]?|aime(?:I|II)?|imo)_(\d{4})_p(\d+)(?:_\d+)?", problem_id)
    if m:
        return t(lang, "comp.numbered", name=COMPETITIONS[m.group(1).lower()], year=m.group(2), n=m.group(3))
    m = re.fullmatch(r"putnam_(\d{4})_([ab]\d+)", problem_id)
    if m:
        return t(lang, "comp.putnam", year=m.group(1), n=m.group(2).upper())
    if problem_id.startswith("mathd_"):
        return t(lang, "comp.math")
    return t(lang, "comp.exercise")


def clean_informal(text: str) -> str:
    """Wiki links and the align* environment KaTeX does not take inline."""
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text or "")
    text = text.replace("\\begin{align*}", "\\begin{aligned}").replace("\\end{align*}", "\\end{aligned}")
    # miniF2F answers are often bare LaTeX ("Show that it is \frac{25}{8}."): put them in math mode
    text = re.sub(r"(Show that it is )([^$\n]*\\[^$\n]*?)(\.?\s*)$", r"\1$\2$\3", text.strip())
    return text.strip()


def _withheld(p: dict) -> bool:
    return "MAA" in str(p.get("license") or "")


def statement_of(p: dict) -> str:
    return (p.get("statement") or ((p.get("header") or "") + (p.get("formal_statement") or ""))).strip()


def card_markdown(p: dict, lang: str) -> str:
    """One problem card (Markdown + LaTeX): difficulty badge, area, competition, time, text, Lean."""
    diff = p.get("difficulty") if p.get("difficulty") in DIFFICULTIES else "custom"
    area_key = f"area.{p.get('area')}" if p.get("area") in ("algebra", "number_theory", "combinatorics") else None
    meta = [competition(p["id"], lang) if p.get("split") != "custom" else t(lang, "comp.custom")]
    if area_key:
        meta.insert(0, t(lang, area_key))
    eta = t(lang, f"eta.{diff}")
    head = (f'<span class="fm-badge fm-badge-{diff}">{html.escape(t(lang, f"difficulty.{diff}"))}</span> '
            f'<span class="fm-meta">{html.escape(" · ".join(meta))}</span>'
            f'<span class="fm-eta">{html.escape(t(lang, "card.eta", eta=eta))}</span>')
    if _withheld(p):
        body = f"_{t(lang, 'card.withheld')}_"
    elif p.get("informal"):
        body = clean_informal(p["informal"])
    else:
        body = f"_{t(lang, 'card.only_lean')}_"
    lean = (f'<details class="fm-lean"><summary>{html.escape(t(lang, "card.see_lean"))}</summary>\n\n'
            f"```lean\n{statement_of(p)}\n```\n\n</details>")
    return f"{head}\n\n{body}\n\n{lean}"


def typical_costs(root: Path = ROOT) -> dict[str, float] | None:
    """Median cost per problem of C5 with the paid profile, by difficulty (finished study runs)."""
    costs: dict[str, list[float]] = {d: [] for d in DIFFICULTIES}
    for cfg_path in (root / "runs").glob("*_C5_experiment*/run_config.json"):
        if cfg_path.parent.name.endswith("_demo"):  # demo solves are not study runs
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if cfg.get("profile") != "experiment" or cfg.get("config") != "C5":
            continue
        for row in _rows(cfg_path.parent / "results.jsonl"):
            if row.get("difficulty") in costs and row.get("status") not in ("budget_stop",):
                costs[row["difficulty"]].append(float((row.get("llm") or {}).get("cost_usd") or 0.0))
    if not all(costs.values()):
        return None
    return {d: sorted(v)[len(v) // 2] for d, v in costs.items()}


def _money(lang: str, usd: float) -> str:
    cents = usd * 100
    if cents < 1:
        return t(lang, "cost.under_cent")
    return t(lang, "cost.cents", cents=f"{cents:.0f}")


def cost_line(lang: str, model: str, costs: dict[str, float] | None, cap: float) -> str:
    if not costs:
        return ""
    return t(lang, "solve.cost", model=model, cap=f"{cap:.2f}", **{d: _money(lang, costs[d]) for d in DIFFICULTIES})


# -- results tab --------------------------------------------------------------------------

def load_results(folder: Path) -> dict | None:
    path = folder / "results.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def results_markdown(data: dict | None, lang: str) -> str:
    """Tables 1 and 2 of the study with plain titles and one reading sentence each."""
    if not data:
        return t(lang, "res.missing")
    sr = {r["config"]: r for r in data.get("sr") or []}
    out = [t(lang, "res.intro"), f"### {t(lang, 'res.t1.title')}"]
    table = [f"| {t(lang, 'res.t1.version')} | {t(lang, 'res.t1.solved')} | {t(lang, 'res.t1.pct')} | {t(lang, 'res.t1.range')} |",
             "|---|---|---|---|"]  # a Markdown table: rows joined by single newlines
    for cfg in ("C5", "C1", "C3", "C4"):
        if cfg in sr:
            r = sr[cfg]
            name = f"**{t(lang, f'cfg.{cfg}')}**" if cfg == "C5" else t(lang, f"cfg.{cfg}")
            table.append(f"| {name} | {t(lang, 'res.t1.of', solved=r['solved'], n=r['n'])} | {r['pct']:.0f} % | "
                         f"{r['ci'][0]:.0f}–{r['ci'][1]:.0f} % |")
    out.append("\n".join(table))
    if "C5" in sr and "C4" in sr:
        out.append("\n" + t(lang, "res.t1.read", best=sr["C5"]["solved"], n=sr["C5"]["n"],
                            no_critic=t(lang, "res.t1.of", solved=sr["C4"]["solved"], n=sr["C4"]["n"])))
    c2 = data.get("c2")
    if c2:
        out += [f"### {t(lang, 'res.c2.title')}",
                t(lang, "res.c2.read", claimed=c2["claimed"], n=c2["n"], nc=c2["not_confirmed"],
                  pct=f"{c2['not_confirmed_pct']:.0f}")]
    tests = data.get("tests") or []
    if tests:
        out.append(f"### {t(lang, 'res.t2.title')}")
        table = [f"| {t(lang, 'res.t2.cmp')} | {t(lang, 'res.t2.diff')} | {t(lang, 'res.t2.clear')} |", "|---|---|---|"]
        for h in tests:
            clear = t(lang, "res.t2.yes" if h["significant"] else "res.t2.no", p=f"{h['p_adj']:.3f}")
            table.append(f"| {t(lang, 'cmp.' + h['id'])} | {t(lang, 'res.t2.points', d=h['delta_pp'])} | {clear} |")
        out.append("\n".join(table))
        sig = [h for h in tests if h["significant"]]
        out.append("\n" + (t(lang, "res.t2.read", clear=t(lang, f"cmp.{sig[0]['id']}.short")) if len(sig) == 1
                           else t(lang, "res.t2.read_none") if not sig
                           else t(lang, "res.t2.read", clear=", ".join(t(lang, f"cmp.{h['id']}.short") for h in sig))))
    out.append(f"\n<small>{t(lang, 'res.footnote')}</small>")
    return "\n\n".join(out)
