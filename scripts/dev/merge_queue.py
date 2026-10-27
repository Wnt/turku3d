"""Merge-queue daemon: land finished tasks in finish order, then integrate main asynchronously.

Each round (every 5 s) lands DONE tasks, each through a `codex-task land <task>` child
(rebase, fast gate, fast-forward), so a landing always runs main's current lander, and,
when main has moved past the last integration and none is running, starts one
`integrator.py run` child. The queue never resumes a worker: a returned or failed landing
is recorded and the task gets an inbox request. When a landing changes main's
`scripts/dev/` tree, the daemon re-executes itself once its integration child is done.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import coordination as coord
import integrator
import sandbox as s
from landing_class import published_root

# Rows in these states wait for a new DONE (a new meta.finished) before the queue acts again.
TERMINAL = ("landed", "returned")
# A landing held by an unacknowledged inbox request is retried at this interval.
HELD_SECONDS = 60
# The lander's own budget is 180 s; this also covers waiting for land.lock.
LAND_TIMEOUT_SECONDS = 600


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
        if not Path(meta.get("repo", "")).is_dir():
            # Nothing left to land: the sandbox was removed (landed before the lander, or by hand).
            continue
        result = path.parent / "last.md"
        if not result.exists() or re.findall(r"^## Result: (.+)$", result.read_text(), re.M) != [
            "DONE"
        ]:
            continue
        row = record["tasks"].get(name)
        if row and row.get("done") == meta.get("finished"):
            continue
        record["tasks"][name] = {
            "done": meta.get("finished"),
            "first_finished": meta.get("first_finished", meta.get("finished")),
            "enqueue": time.time(),
        }
        event(record, name, "enqueued")


def ready(record):
    """Tasks to land, in first-finished order."""
    rows = []
    for name, row in record["tasks"].items():
        if any(key in row for key in TERMINAL):
            continue
        if time.time() - row.get("held", 0) < HELD_SECONDS:
            continue
        meta = s.read_json(s.tasks_root() / name / "meta.json", {})
        if meta.get("landed"):
            # Landed outside the daemon (a manual `codex-task land`): close the row.
            row["landed"] = meta["landed"].get("commit", True)
            continue
        if meta.get("state") == "done" and meta.get("exit_code") == 0:
            rows.append((row.get("first_finished") or row["enqueue"], row["enqueue"], name))
    return [name for *_, name in sorted(rows)]


def returned(name, record=None):
    """Why the queue returned a task, or None."""
    row = (record or state())["tasks"].get(name, {})
    if "returned" not in row:
        return None
    reason = (row.get("returned_reason") or "").strip().splitlines()
    return (reason[0][:160] if reason else "") or "see merge queue events"


def give_back(name, reason, record, notify=True):
    record["tasks"].setdefault(name, {})["returned_reason"] = reason
    event(record, name, "returned", reason=reason)
    if not notify:
        return
    try:
        coord.send(
            name,
            "request",
            f"merge queue returned {name}",
            f"The lander could not land this branch: {reason}\n"
            "Rebase onto current main, fix it, run scripts/dev/gate --fast --base main, "
            "ack this message, then run codex-task finish again. Until that new finish "
            "the queue leaves the sandbox alone.",
        )
    except (ValueError, OSError) as exc:
        event(record, name, "notify_failed", reason=str(exc))


def failure(output):
    """The reason a `codex-task land` child printed: its `codex-task:` error, else its tail."""
    lines = [line for line in output.splitlines() if line.strip()]
    errors = [i for i, line in enumerate(lines) if line.startswith("codex-task: ")]
    if errors:
        lines = lines[errors[-1] :]
        lines[0] = lines[0][len("codex-task: ") :]
    return "\n".join(lines[-20:])[-1000:] or "landing exited without output"


def land(name, record):
    """Land one task through a `codex-task land` child running main's current lander."""
    event(record, name, "started")
    command = [sys.executable, str(s.REPO / "scripts/dev/codex-task"), "land", name]
    result = s.run_group(
        command,
        cwd=s.REPO,
        env=dict(os.environ),
        log=s.tasks_root() / name / "land.log",
        timeout=LAND_TIMEOUT_SECONDS,
    )
    meta = s.read_json(s.tasks_root() / name / "meta.json", {})
    if result.returncode == 0 and meta.get("landed"):
        row = meta["landed"]
        event(
            record,
            name,
            "landed",
            commit=row["commit"],
            merge_wall_seconds=row.get("merge_wall_seconds"),
            fast_gate_seconds=row.get("fast_gate_seconds"),
            **{key: row.get(key) for key in ("class", "tiles", "data_delta")},
        )
        return
    reason = failure(result.stdout)
    if result.returncode == 124:
        reason = f"landing exceeded {LAND_TIMEOUT_SECONDS}s: {reason}"
    if "unacknowledged request/warning" in reason:
        event(record, name, "held", reason=reason)
        return
    # A rebase conflict or a returned batch member is returned by the lander itself,
    # inbox request included.
    give_back(name, reason, record, notify=meta.get("state") != "returned")


_child = None


def integrate(record, published):
    """Start one integration child when main or the data root moved past the last run."""
    global _child
    if (_child and _child.poll() is None) or integrator.running():
        return False
    main = s.git("rev-parse", "main")
    if integrator.settled(published, main):
        return False
    _child = integrator.start(published)
    record["integration_started"] = {"time": time.time(), "to": main, "pid": _child.pid}
    save(record)
    return True


def integrating():
    """True while this daemon's integration child runs."""
    return bool(_child and _child.poll() is None)


def tooling_tree():
    """Main's tree hash for scripts/dev/: the code this daemon imported."""
    return s.git("rev-parse", "main:scripts/dev")


def reload():
    """Replace this process with a fresh `queue run` that imports main's current tooling."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])


def reload_if_changed(started):
    """Re-execute when main's scripts/dev/ tree moved since `started` and no child integrates."""
    if integrating():
        return
    try:
        current = tooling_tree()
    except subprocess.CalledProcessError:
        return
    if current == started:
        return
    record = state()
    record["events"].append(
        {"time": time.time(), "kind": "reloaded", "from": started, "to": current}
    )
    save(record)
    print(f"merge queue: reloaded {started} -> {current}", flush=True)
    # exec closes the queue lock's fd (not inheritable); the new process takes the lock again.
    reload()


def process(record, limit=4, published=None):
    discover(record)
    for name in ready(record)[:limit]:
        land(name, record)
    integrate(record, published or published_root())


def status():
    record = state()
    published = published_root()
    lock = s.root() / ".registry/merge-queue.lock"
    running = False
    if lock.exists():
        with lock.open("a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(file, fcntl.LOCK_UN)
            except BlockingIOError:
                running = True
    landed = [row for row in record["events"] if row["kind"] == "landed"]
    rows = [
        row
        for row in integrator.read_rows(integrator.ledger_path())
        if row.get("published") == str(published)
    ]
    green = max((i for i, row in enumerate(rows) if row["result"] == "green"), default=-1)
    live = integrator.live_commit(published)
    main = s.git("rev-parse", "main")
    print(
        json.dumps(
            {
                "daemon_running": running,
                "waiting_to_land": ready(record),
                "last_landing": landed[-1] if landed else None,
                "integration": integrator.running(),
                "main": main,
                "live": live,
                "live_behind_main": int(s.git("rev-list", "--count", f"{live}..{main}"))
                if live
                else None,
                "last_integration": rows[-1] if rows else None,
                "red_awaiting_fix": [
                    {
                        key: row.get(key)
                        for key in (
                            "id",
                            "result",
                            "culprit",
                            "culprit_task",
                            "failure",
                            "revert",
                            "to",
                        )
                    }
                    for row in rows[green + 1 :]
                    if row["result"] in ("red", "data-red")
                ],
            },
            indent=2,
        )
    )


def run(interval=5, once=False, limit=4):
    if limit < 1:
        raise ValueError("queue limit must be positive")
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
        started_tree = tooling_tree()
        while True:
            record = state()
            try:
                process(record, limit)
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                record["last_error"] = {"time": time.time(), "detail": str(exc)}
                save(record)
            if once:
                return
            reload_if_changed(started_tree)
            time.sleep(interval)
