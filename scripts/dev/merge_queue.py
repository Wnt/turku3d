"""Durable, single-owner merge queue for completed Codex tasks."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time

import sandbox as s


def state_path():
    return s.root() / ".registry/merge-queue.json"


def state():
    return s.read_json(state_path(), {"tasks": {}, "events": []})


def save(record):
    state_path().parent.mkdir(parents=True, exist_ok=True)
    s.write_json(state_path(), record)


def event(record, name, kind, **details):
    now = time.time()
    row = {"time": now, "task": name, "kind": kind, **details}
    record["events"].append(row)
    record["events"] = record["events"][-1000:]
    record["tasks"].setdefault(name, {})[kind] = now
    save(record)


def discover(record):
    for path in sorted(s.tasks_root().glob("*/meta.json")):
        name = path.parent.name
        meta = s.read_json(path)
        if meta.get("landed") or meta.get("state") != "done" or meta.get("exit_code") != 0:
            continue
        result = path.parent / "last.md"
        if not result.exists() or re.findall(r"^## Result: (.+)$", result.read_text(), re.M) != [
            "DONE"
        ]:
            continue
        row = record["tasks"].get(name)
        if row and row.get("done") == meta.get("finished"):
            continue
        record["tasks"][name] = {"done": meta.get("finished"), "enqueue": time.time()}
        event(record, name, "enqueued")


def ready(record):
    rows = []
    for name, row in record["tasks"].items():
        if "published" in row or "resumed" in row or "failed" in row:
            continue
        meta = s.read_json(s.tasks_root() / name / "meta.json", {})
        if meta.get("state") == "done" and meta.get("exit_code") == 0:
            rows.append((row["enqueue"], name))
    return [name for _, name in sorted(rows)]


def resume(name, reason, record):
    from task_runner import launch

    try:
        launch(
            name,
            followup=(
                "Merge queue could not integrate your branch. Rebase onto current main, "
                "resolve the issue, run scripts/dev/codex-task precheck, commit, "
                f"and finish with DONE again. Queue detail: {reason}"
            ),
            route_name="task_rebase",
        )
    except (ValueError, OSError) as exc:
        event(record, name, "failed", reason=f"resume failed: {exc}; {reason}")
    else:
        event(record, name, "resumed", reason=reason)


def rebase(name, record):
    repo = s.sandbox(name) / "repo"
    if s.git("status", "--porcelain", cwd=repo):
        resume(name, "worktree is dirty", record)
        return False
    try:
        s.git("rebase", "main", cwd=repo)
    except subprocess.CalledProcessError as exc:
        s.git("rebase", "--abort", cwd=repo, check=False)
        resume(name, f"rebase conflict: {exc.stderr.strip()[-500:]}", record)
        return False
    return True


def process(record, limit=4):
    from landing import batch

    discover(record)
    names = []
    for name in ready(record):
        if rebase(name, record):
            names.append(name)
        if len(names) >= limit:
            break
    if not names:
        return False
    for name in names:
        event(record, name, "started")
    try:
        batch(names, force=True)
    except (ValueError, subprocess.CalledProcessError) as exc:
        detail = str(exc)
        if "base gate fails" in detail or "main changed" in detail:
            record["last_error"] = {"time": time.time(), "detail": detail}
            save(record)
            return True
        # Existing batch landing identifies the first conflicting or failing-prefix task.
        match = re.search(r"([a-z0-9_-]+): integration conflict", detail)
        if not match:
            match = re.search(r"batch bisected failing prefix ends at ([a-z0-9_-]+)", detail)
        if match and match.group(1) in names:
            culprit = match.group(1)
            resume(culprit, detail[-1000:], record)
            for name in names:
                if name != culprit:
                    event(record, name, "retry", reason=detail[-300:])
            return True
        # A live-build failure has no gate prefix. Run members independently so
        # successful work still publishes and the actual failing member resumes.
        for name in names:
            try:
                batch([name], force=True)
            except (ValueError, subprocess.CalledProcessError) as member_error:
                resume(name, str(member_error)[-1000:], record)
            else:
                published(record, name)
        return True
    for name in names:
        published(record, name)
    return True


def published(record, name):
    meta = s.read_json(s.tasks_root() / name / "meta.json")
    event(
        record,
        name,
        "published",
        commit=meta["landed"]["commit"],
        live_build=meta["landed"].get("live_build"),
    )


def status():
    record = state()
    discover(record)
    lock = s.root() / ".registry/merge-queue.lock"
    running = False
    if lock.exists():
        with lock.open("a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(file, fcntl.LOCK_UN)
            except BlockingIOError:
                running = True
    times = sorted(
        row["published"] - row["done"]
        for row in record["tasks"].values()
        if row.get("published") and row.get("done")
    )
    median = (times[(len(times) - 1) // 2] + times[len(times) // 2]) / 2 if times else None
    print(
        json.dumps(
            {
                "ready": ready(record),
                "running": running,
                "tasks": record["tasks"],
                "median_done_to_live_seconds": median,
                "max_done_to_live_seconds": times[-1] if times else None,
                "last_error": record.get("last_error"),
            },
            indent=2,
        )
    )


def run(interval=5, once=False, limit=4):
    lock = s.root() / ".registry/merge-queue.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("merge queue is already running") from None
        file.seek(0)
        file.truncate()
        file.write(f"{os.getpid()}\n")
        file.flush()
        while True:
            record = state()
            try:
                process(record, limit)
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                record["last_error"] = {"time": time.time(), "detail": str(exc)}
                save(record)
            if once:
                return
            time.sleep(interval)
