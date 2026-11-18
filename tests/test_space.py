"""Offline tests of the static Space (space/) and scripts/build_space.py: no network, no browser."""

from __future__ import annotations

import importlib.util
import json
import re
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPACE = ROOT / "space"
pytest.importorskip("markdown_it")


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bs = _load("build_space")
publish_hf = bs.publish_hf
import i18n  # noqa: E402  (demo/ is on sys.path after build_space)
import steps  # noqa: E402

RAW_KEY = re.compile(r"\b(?:s|step|why|route|strategy|result|action|res|ex|cfg|cmp|space|links)\.[a-z_]+\b")


@pytest.fixture(scope="module")
def built() -> dict[str, bytes]:
    return bs.build()


def _json(built, path):
    return json.loads(built[path].decode("utf-8"))


def _replays(built):
    return {p: _json(built, p) for p in built if p.startswith("data/replays/")}


# -- the generated data -----------------------------------------------------------------------

def test_committed_data_is_what_the_script_builds(built):
    on_disk = bs._on_disk()  # CRLF of a Windows checkout read as LF
    assert set(on_disk) == set(built)
    assert [p for p in built if on_disk[p] != built[p]] == []  # else: python scripts/build_space.py


def test_generated_data_has_no_secrets_paths_emails_or_putnam_text(built):
    putnam = publish_hf.putnam_informal()
    assert len(putnam) == 5  # the scan below would be vacuous without the local PutnamBench text
    assert bs.check(built) == []
    text = "\n".join(d.decode("utf-8") for d in built.values())
    assert not re.search(r"(?i)\b[a-z]:[\\/]", text)  # no Windows path of any kind
    assert "runs/" not in text and "run_id" not in text


def test_the_scan_would_catch_a_planted_secret_and_putnam_text(built):
    putnam = publish_hf.putnam_informal()
    pid, statement = next(iter(putnam.items()))
    files = dict(built)
    fake_key = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz"  # assembled: the code target's scan reads this file
    files["data/site.json"] = built["data/site.json"] + f'\n"{fake_key}"\n'.encode("utf-8")
    files["data/replays/x.json"] = json.dumps({"informal": statement}, ensure_ascii=False).encode("utf-8")
    problems = bs.check(files)
    assert any("openai-style key" in p for p in problems)
    assert any(pid in p for p in problems)


def test_i18n_json_is_the_demo_texts_and_every_text_is_translated(built):
    data = _json(built, "data/i18n.json")
    assert data["texts"] == i18n.TEXTS and data["default"] == "es" and data["langs"] == ["es", "en"]
    es, en = data["texts"]["es"], data["texts"]["en"]
    assert set(es) == set(en), sorted(set(es) ^ set(en))
    for key in es:
        fields = [{f for _, f, _, _ in string.Formatter().parse(s) if f} for s in (es[key], en[key])]
        assert fields[0] == fields[1], key
        assert es[key].strip() and en[key].strip(), key


def test_every_key_the_page_uses_exists_in_both_languages(built):
    page = (SPACE / "index.html").read_text(encoding="utf-8")
    js = (SPACE / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-aria)?="([a-z0-9_.]+)"', page))
    used |= {k for k in re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']""", js) if not k.endswith(".")}
    site = _json(built, "data/site.json")
    used |= {f"speed.{s}" for s in site["speeds"]}
    for rep in _replays(built).values():
        used |= {f"step.{k}" for k in rep["steps"]}
    assert {"space.note", "space.note_link", "tab.examples", "tab.results", "ex.play", "steps.details"} <= used
    for lang in ("es", "en"):
        missing = used - set(_json(built, "data/i18n.json")["texts"][lang])
        assert not missing, (lang, sorted(missing))


def test_every_frame_is_complete_and_shows_no_raw_keys(built):
    site = _json(built, "data/site.json")
    reps = _replays(built)
    assert [f"data/replays/{e['key']}.json" for e in site["examples"]] == sorted(reps)
    for path, rep in reps.items():
        n = len(rep["t"])
        assert n > 0 and len(rep["owner"]) == n == len(rep["tech"])
        assert rep["t"] == sorted(rep["t"])
        for lang in ("es", "en"):
            frames = rep["frames"][lang]
            assert len(frames) == n + 2, path
            assert all(status == "pending" for status, _ in frames[0])
            for frame in frames:
                assert len(frame) == len(rep["steps"])
                for status, sentence in frame:
                    assert status in site["icons"] and sentence.strip(), (path, lang)
                    assert not RAW_KEY.search(sentence), (path, lang, sentence)
            assert all(status not in ("running", "pending") for status, _ in frames[-1]), path
            for field in ("card", "result", "title", "side", "problem"):
                visible = re.sub(r"<details.*?</details>", "", rep[field][lang], flags=re.S)
                assert visible.strip() and not RAW_KEY.search(visible), (path, field, lang)


def test_the_frames_are_the_demo_steps(built):
    """Spot check: frame p of the page == demo/steps.py on the first p events (and the end)."""
    import replay
    for key, rep in replay.load_replays().items():
        data = _json(built, f"data/replays/{key}.json")
        events = rep["events"]
        for p in (1, len(events) // 2, len(events)):
            for lang in ("es", "en"):
                expected = [[s.status, s.sentence] for s in steps.build_steps(events[:p], None, lang)]
                assert data["frames"][lang][p] == expected, (key, p, lang)
        final = [[s.status, s.sentence] for s in steps.build_steps(events, rep["result"], "es")]
        assert data["frames"]["es"][-1] == final


def test_result_cards_say_what_lean_decided(built):
    reps = _replays(built)
    c2 = reps["data/replays/4_imo_1979_p1_C2.json"]
    assert i18n.t("es", "result.claimed") in c2["result"]["es"] and "fm-warn" in c2["result"]["es"]
    assert i18n.t("en", "result.claimed") in c2["result"]["en"]
    assert i18n.t("es", "result.fail") not in c2["result"]["es"]  # not confirmed, not "wrong"
    c5 = reps["data/replays/3_algebra_absapb_C5.json"]
    assert i18n.t("es", "result.success") in c5["result"]["es"] and "fm-proof" in c5["result"]["es"]
    c4 = reps["data/replays/3b_algebra_absapb_C4.json"]
    assert i18n.t("en", "result.fail") in c4["result"]["en"]
    site = _json(built, "data/site.json")
    assert site["pairs"] == [["3b_algebra_absapb_C4", "3_algebra_absapb_C5"]]
    assert c4["side"]["es"] == i18n.t("es", "ex.side.C4") and c5["side"]["es"] == i18n.t("es", "ex.side.C5")


def test_results_tab_has_the_main_table_and_h1_to_h3(built):
    site = _json(built, "data/site.json")
    for lang in ("es", "en"):
        html = site["results"][lang]
        assert html.count("<table>") == 2
        for key in ("res.t1.title", "res.c2.title", "res.t2.title", "cmp.H1", "cmp.H2", "cmp.H3", "cfg.C5"):
            assert i18n.t(lang, key) in html, (lang, key)
    assert [h["id"] for h in site["results_data"]["tests"]] == ["H1", "H2", "H3"]
    assert {r["config"] for r in site["results_data"]["sr"]} == {"C1", "C3", "C4", "C5"}


# -- Markdown + LaTeX ----------------------------------------------------------------------------

def test_md_html_keeps_math_and_leaves_code_alone():
    out = bs.md_html("Let $a_1 = b_2$ and \\[x^2\\]; code `$y_1$`.\n\n```lean\nexample : 1 = 1 := by simp -- $z$\n```")
    assert '<span class="tex" data-display="0">a_1 = b_2</span>' in out
    assert '<span class="tex" data-display="1">x^2</span>' in out
    assert "<code>$y_1$</code>" in out and "-- $z$" in out
    assert "<em>" not in out


# -- the page -------------------------------------------------------------------------------------

def test_the_only_external_dependency_is_a_pinned_katex():
    page = (SPACE / "index.html").read_text(encoding="utf-8")
    urls = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    assert urls and all(re.fullmatch(r"https://cdn\.jsdelivr\.net/npm/katex@\d+\.\d+\.\d+/dist/katex\.min\.(?:js|css)", u)
                        for u in urls), urls
    assert page.count('integrity="sha384-') == len(urls)
    for name in ("app.js", "style.css"):
        assert "http://" not in (SPACE / name).read_text(encoding="utf-8")
        assert "https://" not in (SPACE / name).read_text(encoding="utf-8").split("*/", 1)[-1]


def test_links_point_to_the_published_repos(built):
    links = _json(built, "data/site.json")["links"]
    assert links == {"code": "https://huggingface.co/NicolasT08/formal-mas",
                     "eval": "https://huggingface.co/datasets/NicolasT08/formal-mas-eval",
                     "index": "https://huggingface.co/datasets/NicolasT08/formal-mas-index"}


# -- publishing ----------------------------------------------------------------------------------

def test_space_files_list_is_exactly_what_space_holds():
    present = {p.relative_to(SPACE).as_posix() for p in SPACE.rglob("*") if p.is_file()}
    assert set(publish_hf.SPACE_FILES) == present
    assert publish_hf.REPOS["space"] == ("NicolasT08/formal-mas-demo", "space")
    for suffix in (".html", ".js", ".css"):
        assert publish_hf.is_text("x" + suffix)  # scanned like every other text file


def test_stage_space_from_the_working_tree_is_clean():
    putnam = publish_hf.putnam_informal()
    staged = publish_hf.stage_space(None, putnam)
    assert [s.path for s in staged] == [*publish_hf.SPACE_FILES, "README.md"]
    readme = staged[-1].data.decode("utf-8")
    assert "\nsdk: static\n" in readme and "app_file: index.html" in readme
    assert publish_hf.scan(staged, putnam) == []


def test_stage_space_refuses_stray_files(tmp_path, monkeypatch):
    for p in publish_hf.SPACE_FILES:
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_bytes((SPACE / p).read_bytes())
    (tmp_path / "notes.txt").write_text("draft", encoding="utf-8")
    monkeypatch.setattr(publish_hf, "SPACE", tmp_path)
    with pytest.raises(publish_hf.PublishError, match="notes.txt"):
        publish_hf.stage_space(None, {})
    (tmp_path / "notes.txt").unlink()
    (tmp_path / "data" / "site.json").write_text("{}", encoding="utf-8")
    with pytest.raises(publish_hf.PublishError, match="build_space"):
        publish_hf.stage_space(None, {})


def test_worktree_is_never_uploaded():
    with pytest.raises(SystemExit):
        publish_hf.main(["space", "--worktree", "--upload"])
