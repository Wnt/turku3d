import json
import os
import stat
import sys
from pathlib import Path

import pytest

from turkutex.codex import rollout as ro
from turkutex.codex.runner import EXIT_QUOTA, Runner, load_jobs, parse_json_answer

HERE = Path(__file__).parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_codex.py'} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))
    monkeypatch.setenv("TURKUTEX_CODEX_BIN", str(fake))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_USAGE", "5")
    monkeypatch.delenv("TURKUTEX_USAGE_CMD", raising=False)
    (tmp_path / "a.jpg").write_bytes(b"img-a")
    (tmp_path / "schema.json").write_text('{"type":"object"}')
    jobs = [
        {"id": "lab", "prompt": "label $what", "vars": {"what": "facade"}, "images": ["a.jpg"], "schema": "schema.json"},
        {"id": "gen", "prompt": "GENERATE TWICE", "images": ["a.jpg"], "expect_images": 1},
        {"id": "bad", "prompt": "FAIL please"},
    ]
    jf = tmp_path / "jobs.jsonl"
    jf.write_text("".join(json.dumps(j) + "\n" for j in jobs))
    return tmp_path, jf


def calls(tmp):
    p = tmp / "calls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_full_run_cache_and_resume(env):
    tmp, jf = env
    out = tmp / "run1"
    r = Runner(out, parallel=3, cache_dir=tmp / "cache", retries=1)
    rc = r.run(load_jobs(jf))
    assert rc == 1  # one job failed
    recs = {}
    for line in (out / "out.jsonl").read_text().splitlines():
        x = json.loads(line)
        recs[x["id"]] = x
    assert recs["lab"]["ok"] and recs["lab"]["answer"] == {"facade_id": "x", "bays": 5}
    assert recs["lab"]["usage_before"] == 5 and recs["lab"]["usage_after_rollout"] == 1.5
    g = recs["gen"]
    assert g["ok"] and len(g["images"]) == 2 and g["note"].startswith("2 PNGs")
    assert sum(i["primary"] for i in g["images"]) == 1
    prim = next(i for i in g["images"] if i["primary"])
    assert prim["referenced"] and Path(g["primary"]).exists()
    assert g["imagegen_calls"][0]["prompt"].startswith("fake prompt 0: GENERATE")
    assert g["imagegen_calls"][0]["referenced_images"][0].endswith("a.jpg")
    assert not recs["bad"]["ok"] and "codex exit 1" in recs["bad"]["error"]
    failed = json.loads((out / "failed.json").read_text())
    assert [f["id"] for f in failed["failed"]] == ["bad"]
    n1 = len(calls(tmp))
    assert n1 == 4  # lab, gen, bad x2 (one retry)
    # the prompt went in on stdin with vars substituted; images attached after '-'
    lab_call = next(c for c in calls(tmp) if c["prompt"].startswith("label"))
    assert lab_call["prompt"] == "label facade"
    args = lab_call["args"]
    assert args[args.index("-") + 1] == "-i" and "--output-schema" in args and "read-only" in args

    # resume in the same dir: only the failed job is retried
    rc = Runner(out, parallel=2, cache_dir=tmp / "cache", retries=0).run(load_jobs(jf))
    assert rc == 1 and len(calls(tmp)) == n1 + 1

    # new dir, same cache: ok jobs are cache hits (no codex call)
    out2 = tmp / "run2"
    Runner(out2, parallel=2, cache_dir=tmp / "cache", retries=0).run(load_jobs(jf))
    assert len(calls(tmp)) == n1 + 2  # only 'bad' again
    recs2 = {json.loads(x)["id"]: json.loads(x) for x in (out2 / "out.jsonl").read_text().splitlines()}
    assert recs2["gen"]["cached"] and len(recs2["gen"]["images"]) == 2
    assert Path(recs2["gen"]["primary"]).exists()


def test_quota_stop_is_resumable(env, monkeypatch):
    tmp, jf = env
    monkeypatch.setenv("FAKE_USAGE", "100")
    out = tmp / "run"
    rc = Runner(out, parallel=2, cache_dir=tmp / "cache", max_pct=100).run(load_jobs(jf))
    assert rc == EXIT_QUOTA and calls(tmp) == []
    f = json.loads((out / "failed.json").read_text())
    assert sorted(f["not_started_quota"]) == ["bad", "gen", "lab"] and "100" in f["stop_reason"]
    monkeypatch.setenv("FAKE_USAGE", "40")
    rc = Runner(out, parallel=2, cache_dir=tmp / "cache", max_pct=50, retries=0).run(load_jobs(jf))
    assert rc == 1 and len(calls(tmp)) == 3


def test_unknown_usage_stops(env, monkeypatch):
    tmp, jf = env
    monkeypatch.setenv("TURKUTEX_USAGE_CMD", "echo unknown")
    rc = Runner(tmp / "r", cache_dir=None).run(load_jobs(jf))
    assert rc == EXIT_QUOTA and calls(tmp) == []


def test_schema_job_with_non_json_answer_fails(env, tmp_path):
    tmp, _ = env
    jf = tmp / "j2.jsonl"
    jf.write_text(json.dumps({"id": "x", "prompt": "NOJSON", "schema": "schema.json"}) + "\n")
    rc = Runner(tmp / "r", cache_dir=None, retries=0).run(load_jobs(jf))
    assert rc == 1
    rec = json.loads((tmp / "r" / "out.jsonl").read_text().splitlines()[0])
    assert rec["error"] == "schema job but last message is not JSON"


def test_job_validation(tmp_path):
    jf = tmp_path / "j.jsonl"
    jf.write_text(json.dumps({"id": "a b", "prompt": "x"}) + "\n")
    with pytest.raises(ValueError):
        load_jobs(jf)
    jf.write_text(json.dumps({"id": "a", "prompt": "x", "images": ["missing.png"]}) + "\n")
    with pytest.raises(FileNotFoundError):
        load_jobs(jf)


def test_key_changes_with_inputs(tmp_path):
    (tmp_path / "a.png").write_bytes(b"1")
    jf = tmp_path / "j.jsonl"
    jf.write_text(json.dumps({"id": "a", "prompt": "x", "images": ["a.png"]}) + "\n")
    k1 = load_jobs(jf)[0].key()
    (tmp_path / "a.png").write_bytes(b"2")
    assert load_jobs(jf)[0].key() != k1
    jf.write_text(json.dumps({"id": "a", "prompt": "x", "images": ["a.png"], "prompt_version": "v2"}) + "\n")
    assert load_jobs(jf)[0].key() != k1


def test_parse_json_answer():
    assert parse_json_answer('{"a": 1}') == {"a": 1}
    assert parse_json_answer('text\n```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json_answer('done: {"notes": "x"}') == {"notes": "x"}
    assert parse_json_answer("nothing") is None


def test_rollout_old_style_direct_call(tmp_path):
    p = tmp_path / "rollout-2026-01-01T00-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    rows = [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "image_gen.imagegen", "call_id": "c",
                                                   "input": json.dumps({"prompt": "p1", "referenced_image_paths": ["/x.png"]})}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c",
                                                   "output": "Generated images are saved to /g as /g/exec-1.png by default."}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = ro.parse_rollout(p)
    assert r.thread_id == "11111111-2222-3333-4444-555555555555"
    assert r.calls[0].prompt == "p1" and r.calls[0].referenced_images == ["/x.png"]
    assert r.calls[0].outputs == ["/g/exec-1.png"]
