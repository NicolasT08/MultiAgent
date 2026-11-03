"""Pure logic for curating data/eval.jsonl (Phase 7): pools, seeded draw and stratified split.

The draw and the split depend only on the problem id and the seed, never on system results.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

AREAS = ("algebra", "number_theory", "combinatorics")
DIFFICULTIES = ("easy", "medium", "hard")
EXCLUDED_PUTNAM_TAGS = {"geometry", "analysis", "probability", "linear_algebra"}
CONVERSION_MARK = "big_operator_in_to_mem"

_BIG_OP_IN = re.compile(r"([∑∏][^,∑∏]*?)\sin\s")
_DOCSTRING = re.compile(r"/--.*?-/", re.S)
_LOCAL_DECL = re.compile(r"^\s*(def|abbrev|noncomputable|structure|inductive|instance|variable|namespace|section|set_option|notation|local|macro|syntax)\b", re.M)
_THEOREM = re.compile(r"^theorem\s", re.M)


def convert_big_operators(statement: str) -> str:
    """`∑ x in s, f x` → `∑ x ∈ s, f x` (same for ∏). Touches only ` in ` inside a binder."""
    previous = None
    while previous != statement:
        previous = statement
        statement = _BIG_OP_IN.sub(r"\1 ∈ ", statement)
    return statement


def draw_key(seed: int, problem_id: str) -> str:
    """Position of a problem in the seeded order; stable across Python versions."""
    return hashlib.sha256(f"{seed}:{problem_id}".encode("utf-8")).hexdigest()


def seeded_order(ids: list[str], seed: int) -> list[str]:
    return sorted(ids, key=lambda i: draw_key(seed, i))


def difficulty_of(problem_id: str) -> str:
    if problem_id.startswith("mathd_"):
        return "easy"
    if problem_id.startswith(("imo_", "imosl_", "putnam_")):
        return "hard"
    if problem_id.startswith(("amc12", "aime", "algebra_", "numbertheory_", "induction_")):
        return "medium"
    raise ValueError(f"no difficulty rule for {problem_id}")


def area_of(problem_id: str, labels: dict[str, str]) -> str | None:
    """Area of a miniF2F problem; None if out of scope. `labels` maps id to its label."""
    if problem_id.startswith("mathd_algebra"):
        return "algebra"
    if problem_id.startswith("mathd_numbertheory"):
        return "number_theory"
    label = labels.get(problem_id)
    if label is None:
        raise ValueError(f"unlabelled problem {problem_id}")
    return None if label == "out_of_scope" else label


def flatten_labels(labels: dict[str, str]) -> dict[str, str]:
    """{area: "id id id"} → {id: area}; rejects an id with two labels."""
    flat: dict[str, str] = {}
    for area, ids in labels.items():
        for problem_id in ids.split():
            if problem_id in flat:
                raise ValueError(f"{problem_id} has two labels")
            flat[problem_id] = area
    return flat


def check_discards(discards: dict[str, str]) -> None:
    """Every manual discard must state its reason, reached by the draw or not."""
    empty = [problem_id for problem_id, reason in discards.items() if not reason]
    if empty:
        raise ValueError(f"manual discards without a reason: {empty}")


def clean_informal(prefix: str) -> str:
    text = prefix.strip()
    text = text.removeprefix("/--").removesuffix("-/")
    return text.strip()


def load_minif2f(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{"id": r["name"], "origin": "minif2f", "source_split": r["split"],
             "informal": clean_informal(r["informal_prefix"]), "header": r["header"],
             "formal_statement": r["formal_statement"]} for r in rows]


def putnam_record(name: str, tags: list[str], informal: str, text: str) -> dict | None:
    """A PutnamBench file as a record, or None if it is not one self-contained theorem.

    The source ends in `:=\\nsorry`; that placeholder becomes `:= by`, the format of every record.
    """
    body = _DOCSTRING.sub("", text)
    if "_solution" in body or _LOCAL_DECL.search(body) or len(_THEOREM.findall(body)) != 1:
        return None
    start = _THEOREM.search(body).start()
    statement = body[start:].rstrip()
    if not re.search(r":=\s*sorry$", statement):
        return None
    statement = re.sub(r":=\s*sorry$", ":= by\n", statement)
    return {"id": name, "origin": "putnam", "source_split": None, "tags": tags, "informal": informal,
            "header": body[:start].strip() + "\n\n", "formal_statement": statement}


def putnam_eligible(record: dict) -> bool:
    tags = set(record["tags"])
    return "combinatorics" in tags and not tags & EXCLUDED_PUTNAM_TAGS


def dev_counts(grid: dict[str, dict[str, int]], total_dev: int, seed: int,
               minimum: dict[tuple[str, str], int] | None = None) -> dict[tuple[str, str], int]:
    """Largest-remainder allocation of `total_dev` over the cells; ties broken by the seed.
    Cells below `minimum` (their reserved problems) are served first in the remainder round."""
    minimum = minimum or {}
    total = sum(n for row in grid.values() for n in row.values())
    cells = [(d, a, grid[d][a]) for d in DIFFICULTIES for a in AREAS if grid[d][a]]
    counts = {(d, a): n * total_dev // total for d, a, n in cells}
    left = total_dev - sum(counts.values())
    ranked = sorted(cells, key=lambda c: (counts[(c[0], c[1])] >= minimum.get((c[0], c[1]), 0),
                                          -((c[2] * total_dev) % total), draw_key(seed, f"{c[0]}/{c[1]}")))
    for d, a, _ in ranked[:left]:
        counts[(d, a)] += 1
    return counts


def assign_split(cell_ids: list[str], n_dev: int, reserved: set[str], seed: int) -> dict[str, str]:
    """Reserved problems go to dev first; the rest of dev is filled in seeded order."""
    forced = [i for i in cell_ids if i in reserved]
    if len(forced) > n_dev:
        raise ValueError(f"{len(forced)} reserved problems but only {n_dev} dev slots: {forced}")
    rest = seeded_order([i for i in cell_ids if i not in reserved], seed)
    dev = set(forced) | set(rest[:n_dev - len(forced)])
    return {i: "dev" if i in dev else "test" for i in cell_ids}
