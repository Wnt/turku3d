"""Durable, single-owner merge queue for completed Codex and external-worker tasks."""

from __future__ import annotations

import concurrent.futures
import contextlib
import fcntl
import json
import os
import re
import subprocess
import time

import coordination as coord
import sandbox as s

try:
    import host_slots
except ImportError:  # The host scheduler lands independently of this tool.
    host_slots = None


# Retry delays after a queue-side failure (slow recheck, failed Codex resume), by attempt.
RETRY_SECONDS = (300, 600, 1200, 1800)
# Consecutive slow rechecks after which the task goes back to its worker.
SLOW_LIMIT = 2
# Rows in these states wait for a new DONE (a new meta.finished) before the queue acts again.
TERMINAL = ("published", "resumed", "returned")


class Retry(ValueError):
    """A queue-side failure the queue retries with backoff instead of returning the task."""


class Slow(Retry):
    """A bounded recheck exceeded its deadline."""


def backoff(retries):
    return RETRY_SECONDS[min(max(retries, 1), len(RETRY_SECONDS)) - 1]


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
    import queue_sweep

    rows = []
    for name, row in record["tasks"].items():
        if any(key in row for key in TERMINAL):
            continue
        if "failed" in row and time.time() - row["failed"] < backoff(row.get("retries", 1)):
            continue
        meta = s.read_json(s.tasks_root() / name / "meta.json", {})
        if meta.get("state") == "done" and meta.get("exit_code") == 0:
            try:
                receipt = s.read_json(s.tasks_root() / name / "precheck.json", {})
                if queue_sweep.blocked(s.sandbox(name) / "repo", receipt):
                    continue
            except ValueError:
                continue
            rows.append((row["enqueue"], name))
    return [name for _, name in sorted(rows)]


def retry(name, reason, record):
    row = record["tasks"].setdefault(name, {})
    row["retries"] = row.get("retries", 0) + 1
    event(record, name, "failed", reason=reason, retry_seconds=backoff(row["retries"]))


def slow(name, reason, record):
    row = record["tasks"].setdefault(name, {})
    row["slow_streak"] = row.get("slow_streak", 0) + 1
    if row["slow_streak"] >= SLOW_LIMIT:
        return_to_worker(name, f"{SLOW_LIMIT} consecutive slow rechecks: {reason}", record)
    else:
        retry(name, reason, record)


def returned(name, record=None):
    """Why the queue returned an external worker's task, or None."""
    row = (record or state())["tasks"].get(name, {})
    if "returned" not in row:
        return None
    reason = (row.get("returned_reason") or "").strip().splitlines()
    return (reason[0][:160] if reason else "") or "see merge queue events"


def return_to_worker(name, reason, record):
    """Codex tasks resume through Codex; an external worker gets a request and `returned`."""
    from task_runner import launch

    if s.read_json(s.tasks_root() / name / "meta.json", {}).get("worker") == "external":
        record["tasks"].setdefault(name, {})["returned_reason"] = reason
        event(record, name, "returned", reason=reason)
        try:
            coord.send(
                name,
                "request",
                f"merge queue returned {name}",
                f"The merge queue cannot land this branch: {reason}\n"
                "Rebase onto current main, run scripts/dev/codex-task precheck in the sandbox, "
                "ack this message, then run codex-task finish again. Until that new finish "
                "the queue leaves the sandbox alone.",
            )
        except (ValueError, OSError) as exc:
            event(record, name, "notify_failed", reason=str(exc))
        return
    try:
        launch(
            name,
            followup=(
                "Merge queue could not integrate your branch. Rebase onto current main, "
                "resolve the issue, run scripts/dev/codex-task precheck, commit, "
                f"and finish with DONE again. Queue detail: {reason}"
            ),
            route_name="task_rebase",
            resume_reason="main-moved",
        )
    except (ValueError, OSError) as exc:
        retry(name, f"resume failed: {exc}; {reason}", record)
    else:
        event(record, name, "resumed", reason=reason)


def last_kind(record, name):
    return next((row["kind"] for row in reversed(record["events"]) if row["task"] == name), None)


def claim(name, record, held):
    """Hold the task lock for the round; a worker precheck in flight holds it already."""
    try:
        held.enter_context(s.lock(s.tasks_root() / name / "lock", wait=False))
    except s.Busy:
        if last_kind(record, name) != "busy":
            event(record, name, "busy")
        return False
    return True


def rebase(name, record):
    repo = s.sandbox(name) / "repo"
    if s.git("status", "--porcelain", cwd=repo):
        return_to_worker(name, "worktree is dirty", record)
        return False
    try:
        s.rebase("main", repo)
    except subprocess.CalledProcessError as exc:
        return_to_worker(name, f"rebase conflict: {exc.stderr.strip()[-500:]}", record)
        return False
    return True


def reverify(name, record):
    """Refresh a stale receipt using shared check caches under the queue deadline."""
    repo = s.sandbox(name) / "repo"
    path = s.tasks_root() / name
    old = s.read_json(path / "precheck.json", {})
    if not old:
        raise ValueError("missing worker precheck; return to worker")
    previous = old.get("main")
    changed = s.git("diff", "--name-only", previous, "HEAD", cwd=repo).splitlines()
    # Code/data producers invalidate every tile and can exceed the queue budget.
    if any(p.startswith(("pipeline/", "textures/")) for p in changed):
        raise ValueError("all-tile producer changed; worker precheck required")
    started = time.monotonic()
    refreshed = run_precheck(name, "main", old)
    event(
        record,
        name,
        "reverified",
        seconds=round(time.monotonic() - started, 3),
        cache_hits=refreshed.get("live_build", {}).get("cache_hits", []),
    )
    return refreshed


def run_precheck(name, base, old):
    repo = s.sandbox(name) / "repo"
    path = s.tasks_root() / name
    env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
    started = time.monotonic()
    deadline = started + 210
    env["TURKU3D_CHECK_DEADLINE"] = str(deadline)
    if old.get("release_id"):
        env["TURKU3D_PREVIOUS_RELEASE_ID"] = old["release_id"]
    # The child precheck holds the queue's task lock, so a worker precheck cannot interleave.
    result = s.run_group(
        [str(repo / "scripts/dev/codex-task"), "precheck", name, "--base", base],
        cwd=repo,
        env=env,
        hand_over=[path / "lock"],
    )
    detail = s.failure_detail(result.stdout)
    if result.returncode == 124 or "\nSLOW: " in result.stdout or time.monotonic() > deadline:
        raise Slow(f"affected recheck slow (over {deadline - started:.0f} s): {detail}")
    if result.returncode:
        raise ValueError(f"affected recheck failed ({result.returncode}): {detail}")
    refreshed = s.read_json(path / "precheck.json", {})
    if refreshed.get("main") != s.git("rev-parse", base, cwd=repo):
        raise Retry("base moved during affected recheck")
    return refreshed


def speculative(names, record, pool):
    """Prepare disjoint cumulative prefixes while the first member publishes."""
    if len(names) < 2:
        return {}
    base = s.git("rev-parse", "main")
    receipts = {name: s.read_json(s.tasks_root() / name / "precheck.json", {}) for name in names}
    if any(row.get("main") != base for row in receipts.values()):
        return {}
    domains = set()
    for name in names:
        repo = s.sandbox(name) / "repo"
        paths = s.git("diff", "--name-only", f"{base}...HEAD", cwd=repo).splitlines()
        touched = {"/".join(path.split("/")[:2]) for path in paths}
        if (
            not touched
            or touched & domains
            or any(path.startswith(("pipeline/", "textures/")) for path in paths)
        ):
            return {}
        domains.update(touched)
    for name in names:
        if not rebase(name, record):
            return {}
    previous = s.git("rev-parse", "HEAD", cwd=s.sandbox(names[0]) / "repo")
    futures = {}
    for name in names[1:]:
        repo = s.sandbox(name) / "repo"
        try:
            s.git("rebase", "--onto", previous, base, cwd=repo)
        except subprocess.CalledProcessError as exc:
            s.git("rebase", "--abort", cwd=repo, check=False)
            return_to_worker(name, f"speculative prefix conflict: {exc.stderr[-500:]}", record)
            break
        futures[name] = pool.submit(run_precheck, name, previous, receipts[name])
        previous = s.git("rev-parse", "HEAD", cwd=repo)
    return futures


def process(record, limit=4):
    from landing import fast

    discover(record)
    if record.get("last_error") and time.time() - record["last_error"]["time"] < 300:
        return False
    with contextlib.ExitStack() as held:
        names = [name for name in ready(record)[:limit] if claim(name, record, held)]
        integrate(names, record, fast)
    return bool(names)


def integrate(names, record, fast):
    """Rebase, recheck and publish claimed tasks; the caller holds their task locks."""
    import queue_sweep

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(names) - 1)) as pool:
        futures = speculative(names, record, pool)
        for name in names:
            if any(key in record["tasks"].get(name, {}) for key in TERMINAL):
                continue
            event(record, name, "started")
            if name not in futures and not rebase(name, record):
                continue
            try:
                if name in futures:
                    futures[name].result()
                else:
                    receipt = s.read_json(s.tasks_root() / name / "precheck.json", {})
                    repo = s.sandbox(name) / "repo"
                    if receipt.get("main") != s.git("rev-parse", "main", cwd=repo):
                        reverify(name, record)
                record["tasks"][name].pop("slow_streak", None)
                result = fast(name)
            except Slow as exc:
                slow(name, str(exc)[-1000:], record)
            except Retry as exc:
                retry(name, str(exc)[-1000:], record)
            except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as exc:
                if "unacknowledged request/warning" in str(exc):
                    event(record, name, "held", reason=str(exc)[-1000:])
                else:
                    return_to_worker(name, str(exc)[-1000:], record)
            else:
                event(
                    record,
                    name,
                    "published",
                    commit=result["commit"],
                    merge_wall_seconds=result["merge_wall_seconds"],
                    verification=result["verification"],
                )
                try:
                    queue_sweep.start()
                except OSError as exc:
                    event(record, name, "sweep_start_failed", reason=str(exc))


def admitted_process(record, limit):
    if host_slots is None:
        return process(record, limit)
    with host_slots.slot("land"):
        prior = os.environ.get("TURKU3D_LAND_OWNER")
        os.environ["TURKU3D_LAND_OWNER"] = f"{os.getpid()}:{host_slots.identity(os.getpid())}"
        try:
            return process(record, limit)
        finally:
            if prior is None:
                os.environ.pop("TURKU3D_LAND_OWNER", None)
            else:
                os.environ["TURKU3D_LAND_OWNER"] = prior


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
    import queue_sweep

    record = state()
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
    merges = sorted(
        row["merge_wall_seconds"]
        for row in record["tasks"].values()
        if row.get("merge_wall_seconds") is not None
    )
    merge_median = (
        (merges[(len(merges) - 1) // 2] + merges[len(merges) // 2]) / 2 if merges else None
    )
    print(
        json.dumps(
            {
                "ready": ready(record),
                "running": running,
                "tasks": record["tasks"],
                "median_done_to_live_seconds": median,
                "max_done_to_live_seconds": times[-1] if times else None,
                "median_merge_wall_seconds": merge_median,
                "max_merge_wall_seconds": merges[-1] if merges else None,
                "last_error": record.get("last_error"),
                "full_sweep": s.read_json(queue_sweep.path(), {}),
            },
            indent=2,
        )
    )


def run(interval=5, once=False, limit=4):
    import warm_queue

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
        while True:
            with warm_queue.service() as browser_state:
                if browser_state:
                    os.environ["TURKU3D_WARM_BROWSER_STATE"] = str(browser_state)
                try:
                    while True:
                        record = state()
                        try:
                            admitted_process(record, limit)
                        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                            record["last_error"] = {"time": time.time(), "detail": str(exc)}
                            save(record)
                        if once:
                            return
                        if not browser_state or not warm_queue.alive(browser_state):
                            break
                        time.sleep(interval)
                finally:
                    os.environ.pop("TURKU3D_WARM_BROWSER_STATE", None)
            if once:
                return
            time.sleep(interval)
