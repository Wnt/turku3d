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
    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "ledger.jsonl"))
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\nexec {sys.executable} {HERE / "fake_codex.py"} "$@"\n')
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
        {
            "id": "lab",
            "prompt": "label $what",
            "vars": {"what": "facade"},
            "images": ["a.jpg"],
            "schema": "schema.json",
        },
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
    recs2 = {
        json.loads(x)["id"]: json.loads(x) for x in (out2 / "out.jsonl").read_text().splitlines()
    }
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
    jf.write_text(
        json.dumps({"id": "a", "prompt": "x", "images": ["a.png"], "prompt_version": "v2"}) + "\n"
    )
    assert load_jobs(jf)[0].key() != k1


def test_parse_json_answer():
    assert parse_json_answer('{"a": 1}') == {"a": 1}
    assert parse_json_answer('text\n```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json_answer('done: {"notes": "x"}') == {"notes": "x"}
    assert parse_json_answer("nothing") is None


def test_rollout_old_style_direct_call(tmp_path):
    p = tmp_path / "rollout-2026-01-01T00-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "image_gen.imagegen",
                "call_id": "c",
                "input": json.dumps({"prompt": "p1", "referenced_image_paths": ["/x.png"]}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "c",
                "output": "Generated images are saved to /g as /g/exec-1.png by default.",
            },
        },
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = ro.parse_rollout(p)
    assert r.thread_id == "11111111-2222-3333-4444-555555555555"
    assert r.calls[0].prompt == "p1" and r.calls[0].referenced_images == ["/x.png"]
    assert r.calls[0].outputs == ["/g/exec-1.png"]


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_usage_takes_highest_window(tmp_path):
    """A plan with a 5 h primary and a weekly secondary: the weekly 100 % must not be hidden."""
    from turkutex.codex import usage

    srv = tmp_path / "srv.py"
    srv.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    r = {'rateLimits': {'primary': {'usedPercent': 3, 'windowDurationMins': 300},\n"
        "                        'secondary': {'usedPercent': 100, 'windowDurationMins': 10080}}}\n"
        "    print(json.dumps({'id': m['id'], 'result': r if m.get('method') == 'account/rateLimits/read' else {}}), flush=True)\n"
    )
    b = _fake_bin(tmp_path, "codex2", f"#!/bin/sh\nexec {sys.executable} {srv}\n")
    assert usage.used_percent(b) == 100


def test_usage_hanging_app_server_times_out(tmp_path, monkeypatch):
    """app-server that never answers: used_percent() must return None (fail closed), not block forever."""
    import queue
    from unittest.mock import patch

    from turkutex.codex import usage

    b = _fake_bin(tmp_path, "codexhang", "#!/bin/sh\nexec sleep 60\n")
    orig = usage._rpc
    monkeypatch.setattr(usage, "_rpc", lambda m, c="codex", timeout=25.0: orig(m, c, timeout=1.0))
    with patch.object(queue.Queue, "get", side_effect=queue.Empty) as wait:
        assert usage.used_percent(b) is None
        assert 0 <= wait.call_args.kwargs["timeout"] <= 1.0


def test_usage_cmd_missing_is_unknown(monkeypatch):
    from turkutex.codex import usage

    monkeypatch.setenv("TURKUTEX_USAGE_CMD", "/nonexistent/usage-cmd")
    assert usage.used_percent() is None


def test_schema_validation_rejects_wrong_json(env):
    tmp, jf = env
    (tmp / "schema.json").write_text(json.dumps({"type": "object", "required": ["missing_field"]}))
    jobs = load_jobs(jf)[:1]
    runner = Runner(tmp / "strict", retries=0)
    assert runner.run(jobs) == 1
    assert "schema validation" in runner.done_records()["lab"]["error"]


def test_deleted_image_is_not_a_resume_hit(env):
    tmp, jf = env
    job = load_jobs(jf)[1]
    runner = Runner(tmp / "repair", retries=0)
    assert runner.run([job]) == 0
    before = len(calls(tmp))
    Path(runner.done_records()["gen"]["primary"]).unlink()
    assert runner.run([job]) == 0
    assert len(calls(tmp)) == before + 1


def test_accounting_idempotent_unknown_and_subsets(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from turkutex.codex import accounting, cost

    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "ledger"))
    events = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "thread.started", "thread_id": "real-thread"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                },
            },
        ]
    )

    def write(_):
        return accounting.record_call(
            source="fixture", model="m", effort="low", job_type="label", events=events
        )

    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(write, range(12))) == 1
    accounting.record_call(source="fixture", model="m", effort="low", job_type="label", ok=False)
    result = cost.summary(accounting.rows(accounting.ledger_path()), "job_type")[0]
    assert result["calls"] == 2 and result["unknown_usage"] == 1
    assert result["total_tokens"] == 120  # cached input and reasoning must not be added twice


def test_effort_changes_cache_key(env):
    _, path = env
    job = load_jobs(path)[0]
    first = job.key()
    job.effort = "low"
    assert job.key() != first


def test_benchmark_metrics_and_budget():
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "qa"))
    sys.path.insert(0, str(root / "bench/codex"))
    spec = importlib.util.spec_from_file_location("costbench", root / "bench/codex/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"start": 72, "budget": 3, "cap": 80, "reset": 123}
    module.check_budget(state, {"usedPercent": 73, "resetsAt": 123})
    for current in [{"usedPercent": 74, "resetsAt": 123}, {"usedPercent": 72, "resetsAt": 124}, {}]:
        with pytest.raises(module.QuotaStop):
            module.check_budget(state, current)
    elevated = {"start": 93, "budget": 3, "cap": 95, "reset": 123}
    module.check_budget(elevated, {"usedPercent": 93, "resetsAt": 123})
    with pytest.raises(module.QuotaStop):
        module.check_budget(elevated, {"usedPercent": 94, "resetsAt": 123})
    issue = {"category": "texture", "bbox": [0, 0, 1, 1]}
    score = module.metrics.qa({"issues": [issue, issue]}, [issue])
    assert score["recall"] == 1 and score["false_positives"] == 1
    assert module.metrics.labels({"bays": 4}, {"bays": 5})["accuracy"] == 0


def test_backfill_retry_dedup_and_unit_cost(tmp_path, monkeypatch):
    from turkutex.codex import accounting, cost

    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "ledger"))
    run = tmp_path / "runs"
    run.mkdir()
    records = [
        {
            "id": "f.improve",
            "thread_id": f"thread-{n}",
            "model": "m",
            "meta": {"kind": "improve", "facade_id": "f"},
            "tokens": {"input_tokens": 100, "output_tokens": 20},
            "seconds": 2,
            "ok": n == 1,
        }
        for n in range(2)
    ]
    (run / "attempts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (run / "out.jsonl").write_text(json.dumps(records[-1]) + "\n")
    assert cost.backfill([run]) == 2
    assert cost.backfill([run]) == 0
    result = cost.summary(accounting.rows(accounting.ledger_path()), "job_type")[0]
    assert result["calls"] == 2 and result["tokens_per_facade"] == 240


def test_ledger_survives_partial_tail(tmp_path, monkeypatch):
    from turkutex.codex import accounting

    path = tmp_path / "ledger"
    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(path))
    path.write_text('{"interrupted":')
    assert accounting.record_call(source="s", model="m", effort="low", job_type="task") == 1
    assert len(list(accounting.rows(path))) == 1


def test_accounting_void_and_failure_identity(tmp_path, monkeypatch):
    from turkutex.codex import accounting, cost

    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "ledger"))
    accounting.record_call(
        source="fixture", model="m", effort="low", job_type="qa", fallback_id="failed-attempt"
    )
    assert (
        accounting.record_call(
            source="fixture", model="m", effort="low", job_type="qa", fallback_id="failed-attempt"
        )
        == 0
    )
    accounting.append(
        {"call_id": "void:failed-attempt", "record_type": "void", "voids": "failed-attempt"}
    )
    assert cost.summary(accounting.rows(accounting.ledger_path()), "job_type") == []


def test_backfill_multiturn_matches_live_ledger(tmp_path, monkeypatch):
    from turkutex.codex import accounting, cost

    monkeypatch.setenv("TURKU3D_CODEX_LEDGER", str(tmp_path / "ledger"))
    run = tmp_path / "runs"
    (run / "logs").mkdir(parents=True)
    events = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "thread.started", "thread_id": "multi"},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
            {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 3}},
        ]
    )
    accounting.record_call(source="live", model="m", effort="low", job_type="label", events=events)
    (run / "logs/f.events.jsonl").write_text(events)
    (run / "out.jsonl").write_text(
        json.dumps({"id": "f", "thread_id": "multi", "model": "m"}) + "\n"
    )
    assert cost.backfill([run]) == 0
    assert len(list(accounting.rows(accounting.ledger_path()))) == 2


def test_routing_override_precedence(monkeypatch, tmp_path):
    from turkutex.codex.routing import route

    config = tmp_path / "routes.toml"
    config.write_text(
        '[default]\nmodel="fallback"\neffort="medium"\n[light]\nmodel="small"\neffort="low"\n'
    )
    monkeypatch.setenv("TURKU3D_CODEX_ROUTING", str(config))
    monkeypatch.delenv("TURKU3D_CODEX_MODEL", raising=False)
    monkeypatch.delenv("TURKU3D_CODEX_EFFORT", raising=False)
    assert route("light") == ("small", "low")
    assert route("unregistered") == ("fallback", "medium")
    monkeypatch.setenv("TURKU3D_CODEX_MODEL", "environment")
    monkeypatch.setenv("TURKU3D_CODEX_EFFORT", "high")
    assert route("light") == ("environment", "high")
    assert route("light", "explicit", "low") == ("explicit", "low")
