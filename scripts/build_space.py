"""Build the data of the static Hugging Face Space (space/data/) from the demo's recorded runs.

    python scripts/build_space.py           # write space/data/
    python scripts/build_space.py --check   # exit 1 if space/data/ differs from what this script builds

The Space (space/index.html + app.js + style.css) is a static page: no Gradio, no Python in the
browser. Everything it shows is produced here by the demo's own code, so the Space and the local
demo cannot drift apart:

  * demo/replays/*.json (scripts/export_replays.py): for every replay and both languages, the
    plain-language steps of demo/steps.py after every event (status + sentence), the technical
    details of demo/timeline.py (one HTML fragment per event), the example card of
    demo/catalog.py and the final result card of demo/steps.py;
  * Tables 1 and 2 of docs/results/results.md, read with export_replays.results_data and shown
    with catalog.results_markdown (plain titles, one reading sentence each);
  * every text of demo/i18n.py, exported as is to data/i18n.json (nothing is copied by hand).

Markdown becomes HTML with markdown-it-py; LaTeX ($…$, $$…$$, \\(…\\), \\[…\\]) is kept as
<span class="tex"> elements that the page renders with KaTeX. app.js only plays the frames back.

Output (paths relative to space/):
  data/i18n.json             {"default", "langs", "texts": {lang: {key: text}}}
  data/site.json             links, speeds, step icons, examples, pairs, results (HTML per language)
  data/replays/<key>.json    one recorded run, see replay_data()

Same rules as export_replays.py: only whitelisted replay fields are read, nothing from runs/;
before anything is written the output goes through publish_hf.scan (API keys and tokens, paths
of this machine, e-mail addresses, any window of PutnamBench informal text) and the build fails
if it finds anything.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
SPACE = ROOT / "space"
DATA = "data"
if str(DEMO) not in sys.path:
    sys.path.insert(0, str(DEMO))

import catalog  # noqa: E402
import i18n  # noqa: E402
import replay  # noqa: E402
import steps  # noqa: E402
import timeline  # noqa: E402

SPEEDS = ("1", "5", "20", "0")  # same choices as demo/app.py (SPEEDS), "0" = instant
DEFAULT_SPEED = "5"

__all__ = ["build", "md_html", "replay_data", "main"]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)  # dataclasses look the module up while it loads
    spec.loader.exec_module(mod)
    return sys.modules[name]


publish_hf = _load("publish_hf")
export_replays = _load("export_replays")


def links() -> dict[str, str]:
    """Hub pages linked from the Space (repo ids from publish_hf.REPOS)."""
    def url(target: str) -> str:
        repo, rtype = publish_hf.REPOS[target]
        return f"https://huggingface.co/{'datasets/' if rtype == 'dataset' else ''}{repo}"
    return {"code": url("code"), "eval": url("eval"), "index": url("index")}


# -- Markdown + LaTeX -> HTML -----------------------------------------------------------------

_MD = MarkdownIt("commonmark", {"html": True}).enable("table")
_CODE = re.compile(r"(^[ \t]*```.*?^[ \t]*```[ \t]*$|`[^`\n]+`)", re.M | re.S)
_MATH = [  # (pattern, display), tried in this order
    (re.compile(r"\$\$(.+?)\$\$", re.S), True),
    (re.compile(r"\\\[(.+?)\\\]", re.S), True),
    (re.compile(r"\\\((.+?)\\\)", re.S), False),
    (re.compile(r"\$([^$\n]+?)\$"), False),
]
_SLOT = "XQMATHSLOT{}QX"


def md_html(text: str) -> str:
    """Markdown (with inline HTML) to HTML; math outside code is kept verbatim for KaTeX."""
    maths: list[tuple[str, bool]] = []

    def protect(segment: str) -> str:
        for rx, display in _MATH:
            def keep(m, display=display):
                tex = re.sub(r"\n[ \t]*>[ \t]?", "\n", m.group(1))  # math inside a blockquote
                maths.append((tex.strip(), display))
                return _SLOT.format(len(maths) - 1)
            segment = rx.sub(keep, segment)
        return segment

    parts = _CODE.split(text or "")
    text = "".join(p if i % 2 else protect(p) for i, p in enumerate(parts))
    out = _MD.render(text)
    for i, (tex, display) in enumerate(maths):
        # a <span> even for display math: it may sit inside a <p> (KaTeX makes it a block)
        out = out.replace(_SLOT.format(i), f'<span class="tex" data-display="{int(display)}">{html.escape(tex)}</span>')
    return out.strip()


# -- replays ------------------------------------------------------------------------------------

def _fragments(step_events: list[dict]) -> list[str]:
    """timeline.render of a step's events, cut into the Markdown each event adds."""
    out, before = [], ""
    for j in range(len(step_events)):
        now = timeline.render(step_events[:j + 1]).strip()
        if not now.startswith(before):
            raise ValueError("timeline.render is not append-only for this step")
        out.append(now[len(before):].strip())
        before = now
    return out


def replay_data(key: str, rep: dict) -> dict:
    """One replay for the page.

    t[i]       seconds since problem_start of event i (n events)
    owner[i]   index in `steps` of the step whose technical details show event i (-1: none)
    tech[i]    HTML that event i adds to those details ("" if nothing)
    frames     {lang: [frame_0 … frame_{n+1}]}: frame_p = [[status, sentence] per step] after p
               events while running; frame_{n+1} = the finished run (with its result)
    card, problem, result, title, side   per-language HTML/text (problem: the bare problem card)
    """
    events = rep.get("events") or []
    result = rep.get("result") or {}
    n = len(events)
    final = steps.build_steps(events, result, i18n.DEFAULT_LANG)
    keys = [s.key for s in final]
    owner = [-1] * n
    pos = {id(e): i for i, e in enumerate(events)}
    for k, s in enumerate(final):
        for e in s.events:
            owner[pos[id(e)]] = k
    tech = [""] * n
    for k, s in enumerate(final):
        for e, frag in zip(s.events, _fragments(s.events)):
            tech[pos[id(e)]] = md_html(frag) if frag else ""

    frames: dict[str, list] = {}
    for lang in i18n.LANGS:
        frames[lang] = []
        # frame 0 (nothing happened yet): every step waiting; before problem_start the demo
        # cannot know the configuration (Baseline B has other steps), so the final ones are used
        frames[lang].append([["pending", i18n.t(lang, "s.pending")] for _ in keys])
        for p in range(1, n + 2):
            shown, res = (events[:p], None) if p <= n else (events, result)
            built = steps.build_steps(shown, res, lang)
            if [s.key for s in built] != keys:
                raise ValueError(f"{key}: the steps change during the run ({[s.key for s in built]})")
            for k, s in enumerate(built):  # details shown by the page == demo's details at this point
                expected = [events[i] for i in range(len(shown)) if owner[i] == k]
                if s.events != expected:
                    raise ValueError(f"{key}: step {s.key} owns other events at frame {p}")
            frames[lang].append([[s.status, s.sentence] for s in built])

    problem = rep.get("problem") or {}
    return {
        "key": key, "config": rep.get("config"), "steps": keys, "t": [round(float(e.get("t") or 0.0), 3) for e in events], "owner": owner, "tech": tech,
        "frames": frames,
        "title": {L: catalog.replay_text(L, key, rep, "title") for L in i18n.LANGS},
        "side": {L: catalog.side_title(L, rep.get("config")) for L in i18n.LANGS},
        "card": {L: md_html(catalog.example_card(L, key, rep)) for L in i18n.LANGS},
        "problem": {L: md_html(catalog.card_markdown(dict(problem), L)) for L in i18n.LANGS},
        "result": {L: md_html(steps.result_html(result, events, L)) for L in i18n.LANGS},
    }


# -- site ---------------------------------------------------------------------------------------

def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def build() -> dict[str, bytes]:
    """space-relative path -> content of every generated file (nothing is written here)."""
    replays = replay.load_replays()
    if not replays:
        raise SystemExit("no replays in demo/replays/ (run scripts/export_replays.py)")
    results = export_replays.results_data((ROOT / "docs" / "results" / "results.md").read_text(encoding="utf-8"))
    if not (results["sr"] and results["c2"] and results["tests"]):
        raise SystemExit("could not read Tables 1 and 2 of docs/results/results.md")

    out = {f"{DATA}/i18n.json": _dump({"default": i18n.DEFAULT_LANG, "langs": list(i18n.LANGS), "texts": i18n.TEXTS})}
    examples = []
    for key, rep in replays.items():
        data = replay_data(key, rep)
        out[f"{DATA}/replays/{key}.json"] = _dump(data)
        examples.append({"key": key, "config": data["config"], "title": data["title"]})
    site = {
        "links": links(),
        "speeds": list(SPEEDS), "default_speed": DEFAULT_SPEED, "max_gap_s": replay.MAX_GAP_S,
        "icons": steps.STATUS_ICONS,
        "examples": examples,
        "pairs": [[a, b] for a, b in replay.pairs(replays)],
        "results": {L: md_html(catalog.results_markdown(results, L)) for L in i18n.LANGS},
        "results_data": results,
    }
    out[f"{DATA}/site.json"] = _dump(site)
    problems = check(out)
    if problems:
        raise SystemExit("the generated Space data contains content that must not be published:\n  "
                         + "\n  ".join(problems))
    return out


def check(files: dict[str, bytes]) -> list[str]:
    """publish_hf.scan over the generated files: secrets, user paths, e-mails, PutnamBench text."""
    staged = [publish_hf.Staged(path, data=data) for path, data in files.items()]
    return publish_hf.scan(staged, publish_hf.putnam_informal())


def _on_disk() -> dict[str, bytes]:
    """space/data/ as committed: CRLF is read as LF (a Windows checkout with core.autocrlf)."""
    base = SPACE / DATA
    if not base.is_dir():
        return {}
    return {p.relative_to(SPACE).as_posix(): p.read_bytes().replace(b"\r\n", b"\n")
            for p in sorted(base.rglob("*")) if p.is_file()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare with space/data/ instead of writing")
    a = ap.parse_args(argv)
    files = build()
    current = _on_disk()
    stale = sorted(p for p in files if current.get(p) != files[p])
    extra = sorted(set(current) - set(files))
    if a.check:
        for p in stale:
            print(f"out of date: space/{p}")
        for p in extra:
            print(f"not generated by this script: space/{p}")
        print("space/data/ is up to date." if not (stale or extra) else "run: python scripts/build_space.py")
        return 1 if stale or extra else 0
    for p in extra:
        (SPACE / p).unlink()
    for p, data in files.items():
        path = SPACE / p
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    size = sum(len(d) for d in files.values())
    print(f"space/{DATA}/: {len(files)} files, {size / 1024:.0f} KB ({len(stale)} changed, {len(extra)} removed); "
          "scan clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
