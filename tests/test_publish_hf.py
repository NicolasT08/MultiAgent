"""Offline tests of the Hub publishing helpers (scripts/publish_hf.py) and of scripts/setup.py's
pure parts. No network: nothing here talks to the Hub, Lean or an LLM."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pub = _load("publish_hf")
setup = _load("setup")

# A made-up statement: never real PutnamBench text in this file (the scanner of publish_hf.py would
# rightly refuse to publish it). Paths and e-mails are assembled at run time for the same reason.
PUTNAM = {"putnam_1965_b2": "Invented test sentence: a finite gadget has $n > 1$ widgets $W_1, \\dots, W_n$, "
                            "each of which is painted exactly once."}
WIN_USER = "C:" + "\\" + "Us" + "ers" + "\\someone"
MAIL = "someone" + "@" + "gmail.com"


def test_putnam_reference_format():
    assert pub.putnam_reference("putnam_1995_b1") == (
        "[Informal statement not redistributed: PutnamBench states it is available with permission from the MAA. "
        "Problem: Putnam 1995 B1; source: PutnamBench informal/putnam.json, commit a635813f.]")


def test_redact_jsonl_marks_only_putnam_records():
    rows = [{"id": "putnam_1965_b2", "informal": PUTNAM["putnam_1965_b2"], "split": "test"},
            {"id": "mathd_algebra_1", "informal": "Solve $x$.", "split": "dev"}]
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    out = [json.loads(line) for line in pub.redact_jsonl(text).splitlines()]
    assert out[0]["informal"] == pub.putnam_reference("putnam_1965_b2") and out[0]["informal_redacted"] is True
    assert out[1] == rows[1]
    assert pub.redact_jsonl(pub.redact_jsonl(text)) == pub.redact_jsonl(text)  # idempotent


def test_redact_text_handles_json_escaped_forms():
    s = PUTNAM["putnam_1965_b2"]
    text = "md: " + s + "\njson: " + json.dumps({"x": s})
    out = pub.redact_text(text, PUTNAM)
    assert "gadget" not in out and out.count("Putnam 1965 B2") == 2


def test_scan_flags_secrets_paths_emails_and_putnam():
    bad = pub.Staged("x.md", data=("key sk-" + "a" * 32 + "\n"
                                   "path " + WIN_USER + "\\file\n"
                                   "mail " + MAIL + "\n"
                                   + PUTNAM["putnam_1965_b2"]).encode("utf-8"))
    found = " ".join(pub.scan([bad], PUTNAM))
    for what in ("openai-style key", "user folder path", "e-mail", "PutnamBench informal text"):
        assert what in found
    ok = pub.Staged("y.md", data=b"Co-Authored-By: x <noreply@anthropic.com>\nimport Mathlib\n")
    assert pub.scan([ok], PUTNAM) == []
    binary = pub.Staged("z.npy", data=b"sk-" + b"a" * 40)
    assert pub.scan([binary], PUTNAM) == []  # binary files are not text-scanned


def test_sanitize_run_text_makes_paths_relative():
    root = str(pub.ROOT)
    cfg = {"argv": ["scripts/run_eval.py", "--data",
                    WIN_USER.replace("\\", "/") + "/AppData/Local/Temp/x/test_remaining.jsonl"],
           "dataset": {"path": root + "\\data\\eval.jsonl"},
           "other": WIN_USER + "\\scratch\\test_remaining.jsonl"}
    out = json.loads(pub.sanitize_run_text(json.dumps(cfg)))
    assert out["dataset"]["path"] == "data\\eval.jsonl"
    assert out["argv"][2] == "<tmp>/test_remaining.jsonl"
    assert out["other"] == "<tmp>/test_remaining.jsonl"
    assert pub.scan([pub.Staged("r.json", data=json.dumps(out).encode())], {}) == []


def test_diff_uses_the_hash_kind_of_the_remote():
    a = pub.Staged("a.txt", data=b"hello")
    b = pub.Staged("b.bin", data=b"\x00\x01")
    c = pub.Staged("c.txt", data=b"new")
    remote = {"a.txt": ("sha1", a.git_sha1()), "b.bin": ("sha256", "0" * 64), "gone.txt": ("sha1", "x")}
    added, changed, same, remote_only = pub.diff([a, b, c], remote)
    assert [s.path for s in added] == ["c.txt"] and [s.path for s in changed] == ["b.bin"]
    assert [s.path for s in same] == ["a.txt"] and remote_only == ["gone.txt"]


def test_code_excludes_and_keeps():
    assert "docs/report/Proyecto_Inteligencia.pdf" in pub.EXCLUDED_CODE
    assert {".gitattributes", "LICENSE"} <= pub.KEEP_REMOTE


def test_runs_published_are_the_ones_make_tables_reads():
    mt = _load_make_tables()
    needed = {run for src in mt.TEST_SOURCES.values() for run, _ in src}
    needed |= set(mt.C1_PARTS) | {mt.C1_MERGED[0], mt.PRO_RUN}
    needed |= {run for src in mt.DEV_SOURCES.values() for run, _ in src}
    assert needed <= set(pub.RUN_FOLDERS)


def _load_make_tables():
    spec = importlib.util.spec_from_file_location("_make_tables", ROOT / "docs" / "results" / "make_tables.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_tables_ledger_resolution(tmp_path, monkeypatch):
    mt = _load_make_tables()
    live, published = tmp_path / "live.jsonl", tmp_path / "pub.jsonl"
    monkeypatch.setattr(mt, "LEDGER_LIVE", live)
    monkeypatch.setattr(mt, "LEDGER_PUBLISHED", published)
    monkeypatch.setattr(mt, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        mt.resolve_ledger(None)
    study = [{"run_id": r, "cost_usd": 0.0} for runs in mt.LEDGER_RUNS.values() for r in runs]
    published.write_text("".join(json.dumps(e) + "\n" for e in study), encoding="utf-8")
    live.write_text(json.dumps({"run_id": "20991231T000000Z_C5_dev_demo", "cost_usd": 0.1}) + "\n", encoding="utf-8")
    assert mt.resolve_ledger(None) == published       # a new installation's own ledger is not used
    live.write_text(published.read_text(encoding="utf-8"), encoding="utf-8")
    assert mt.resolve_ledger(None) == live            # the authors' live ledger has the study runs
    assert mt.resolve_ledger(str(published)) == published


# -- scripts/setup.py --------------------------------------------------------------------------

def test_setup_reads_repl_pins_from_build_repl_sh():
    url, tag, commit = setup.repl_pins()
    assert url == "https://github.com/leanprover-community/repl"
    assert tag == (ROOT / "lean_project" / "lean-toolchain").read_text(encoding="utf-8").strip().split(":")[1]
    assert len(commit) == 40


def test_setup_mathlib_rev_matches_manifest():
    assert len(setup.manifest_mathlib_rev()) == 40


def test_setup_elan_instructions_name_the_installer():
    text = setup.elan_instructions()
    assert "elan-init" in text and "new terminal" in text.lower()


def test_setup_index_manifest_lists_the_retriever_files():
    path = ROOT / "data" / "index_manifest.json"
    if not path.exists():
        pytest.skip("data/index_manifest.json is written by `publish_hf.py index --upload`")
    man = json.loads(path.read_text(encoding="utf-8"))
    assert man["repo_id"] == pub.REPOS["index"][0] and len(man["revision"]) == 40
    assert set(man["files"]) == set(pub.INDEX_FILES)
    assert all(len(v["sha256"]) == 64 and v["size"] > 0 for v in man["files"].values())
