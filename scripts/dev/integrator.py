"""Build, verify and publish the live release for main once per landed batch.

The lander fast-forwards main after the fast gate. This integrator then builds the batch
G..M (G = commit of the published live release, M = main head) once in its own sandbox.
Green publishes M. Red bisects the batch's landings, reverts the culprit on main through
the land lock and the fast gate, tells its author and leaves live at G.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import coordination as coord
import host_slots
import live_build
import live_cache
import sandbox as s
from gate_lib import matches

NAME = "integration"
# Tooling and docs reach live only through declared stage inputs (live_cache.INPUTS).
NON_DOMAIN = ("scripts/dev/*", "docs/*", ".githooks/*")


def registry():
    return s.root() / ".registry"


def ledger_path():
    return registry() / "integrations.jsonl"


def status_path():
    return registry() / "integration.json"


def lock_path():
    return registry() / "integration.lock"


def read_rows(path):
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def last(published):
    """The latest integration row for this published root, or {}."""
    rows = [row for row in read_rows(ledger_path()) if row.get("published") == str(published)]
    return rows[-1] if rows else {}


def live_commit(published):
    record = published / "live/live-build.json"
    return json.loads(record.read_text())["commit"] if record.is_file() else None


def domains(changed):
    """Top-level domains a batch diff can affect in the live build; empty builds nothing.

    A path counts when a live stage declares it as an input (live_cache.INPUTS, the stage
    cache key), or when it is neither gate prose nor tooling and docs. Unknown paths
    therefore count, so a new data or game directory is never skipped.
    """
    config = tomllib.loads((s.REPO / "scripts/dev/gate.toml").read_text())
    prose = config["gate"].get("prose", ["*.md"])
    live = sorted({pattern for patterns in live_cache.INPUTS.values() for pattern in patterns})
    return sorted(
        {
            "/".join(name.split("/")[:2])
            for name in changed
            if matches(name, live) or not (matches(name, prose) or matches(name, NON_DOMAIN))
        }
    )


def landings(start, end, repo):
    """Bisection points in start..end: landed heads from the ledger, always ending at end."""
    commits = s.git(
        "rev-list", "--reverse", "--first-parent", f"{start}..{end}", cwd=repo
    ).splitlines()
    heads = {row.get("commit") for row in read_rows(registry() / "landed.jsonl")}
    points = [commit for commit in commits if commit in heads]
    return points if points[-1:] == [end] else [*points, end]


def facade_reason(start, end, repo):
    """The reported facade regression reason of any landing in start..end, or None."""
    commits = set(s.git("rev-list", f"{start}..{end}", cwd=repo).splitlines())
    return next(
        (
            row["facade_regression_reason"]
            for row in read_rows(registry() / "landed.jsonl")
            if row.get("commit") in commits and row.get("facade_regression_reason")
        ),
        None,
    )


def author(commit):
    """The task whose landing produced commit, from landed.jsonl."""
    return next(
        (
            row.get("name")
            for row in reversed(read_rows(registry() / "landed.jsonl"))
            if row.get("commit") == commit
        ),
        None,
    )


def checkout(commit, name):
    """The reused integration sandbox, reset to commit."""
    try:
        repo = s.sandbox(name) / "repo"
    except ValueError:
        repo = s.new(name, commit)
    if s.git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise ValueError(f"integration sandbox has tracked changes: {repo}")
    s.git("reset", "--hard", commit, cwd=repo)
    return repo


def environment(repo, published):
    env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
    env["TURKU3D_DATA"] = str(published)
    owner = os.environ.get("TURKU3D_INTEGRATION_OWNER")
    if owner:
        env["TURKU3D_INTEGRATION_OWNER"] = owner
        env["TURKU3D_JOBS"] = str(host_slots.integration_jobs())
    return env


def status(**fields):
    current = s.read_json(status_path(), {})
    s.write_json(status_path(), {**current, **fields})


class Run:
    """One integration of G..M; `build` counts builds and keeps every stage log."""

    def __init__(self, published, main_repo, name):
        self.published = published
        self.main_repo = main_repo
        self.name = name
        self.builds = []
        self.started = time.monotonic()
        self.previous = (published / "live").resolve()

    def build(self, start, commit):
        """(report, None) when commit builds green, else (None, failure)."""
        repo = checkout(commit, self.name)
        env = environment(repo, self.published)
        work = Path(env["TURKU3D_WORK"])
        reason = facade_reason(start, commit, repo)
        for attempt in (1, 2):
            log = registry() / f"integration-{commit[:12]}-{len(self.builds) + 1}.log"
            status(commit=commit, log=str(log))
            started = time.monotonic()
            try:
                report = live_build.build(
                    repo, work, self.published, env, log, reason, self.previous
                )
            except live_build.StageFailed as exc:
                self.builds.append(
                    {"commit": commit, "seconds": round(time.monotonic() - started, 3)}
                )
                # One rerun guards against a flaky Chrome stage; cached stages are free.
                if exc.stage.startswith("chrome") and attempt == 1:
                    continue
                return None, {"stage": exc.stage, "detail": str(exc)[-1000:], "log": str(log)}
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                self.builds.append(
                    {"commit": commit, "seconds": round(time.monotonic() - started, 3)}
                )
                return None, {"stage": "build", "detail": str(exc)[-1000:], "log": str(log)}
            self.builds.append({"commit": commit, "seconds": round(time.monotonic() - started, 3)})
            return {**report, "work": str(work)}, None
        raise AssertionError("unreachable")


def bisect(run, start, end, failure):
    """First landing in start..end whose build is red, with its failure (end is red)."""
    repo = checkout(end, run.name)
    points = landings(start, end, repo)
    low, high = -1, len(points) - 1
    failures = {high: failure}
    while high - low > 1:
        middle = (low + high) // 2
        report, failed = run.build(start, points[middle])
        if report:
            low = middle
        else:
            high = middle
            failures[high] = failed
    return (points[high - 1] if high else start), points[high], failures[high]


def revert(run, parent, culprit):
    """Revert parent..culprit on current main through the land lock and the fast gate."""
    with s.lock(registry() / "land.lock"):
        before = s.git("rev-parse", "main", cwd=run.main_repo)
        if s.git("status", "--porcelain", "--untracked-files=no", cwd=run.main_repo):
            return {"error": "main worktree is dirty"}
        repo = checkout(before, run.name)
        try:
            s.git("revert", "--no-edit", f"{parent}..{culprit}", cwd=repo)
        except subprocess.CalledProcessError as exc:
            s.git("revert", "--abort", cwd=repo, check=False)
            return {"error": f"revert conflict: {exc.stderr.strip()[-300:]}"}
        started = time.monotonic()
        gate = s.run_group(
            [str(repo / "scripts/dev/gate"), "--fast", "--base", before],
            cwd=repo,
            env=environment(repo, run.published),
        )
        seconds = round(time.monotonic() - started, 3)
        if gate.returncode:
            return {"error": f"fast gate red on revert: {s.failure_detail(gate.stdout)}"}
        head = s.git("rev-parse", "HEAD", cwd=repo)
        s.git("merge", "--ff-only", head, cwd=run.main_repo)
        row = {
            "name": f"revert-{culprit[:8]}",
            "before": before,
            "commit": head,
            "source": "integrator",
            "reverts": f"{parent}..{culprit}",
            "time": time.time(),
            "fast_gate_seconds": seconds,
        }
        with (registry() / "landed.jsonl").open("a") as ledger:
            ledger.write(json.dumps(row) + "\n")
        return {"commit": head, "fast_gate_seconds": seconds}


def notify(task, culprit, failure, reverted):
    detail = (
        f"reverted on main as {reverted['commit'][:12]}"
        if "commit" in reverted
        else f"NOT reverted ({reverted['error']})"
    )
    body = (
        f"The live integration is red at your landing {culprit[:12]}: {failure['stage']} "
        f"failed. Log: {failure['log']}. Detail: {failure['detail'][-400:]}\n"
        f"The change is {detail}; live stays on the last verified release. Rebase onto "
        "main, fix it, and run codex-task finish again."
    )
    recipient = task if task and (s.tasks_root() / task / "meta.json").exists() else "orchestrator"
    try:
        coord.send(recipient, "request", f"integration red at {culprit[:12]}", body)
    except (ValueError, OSError):
        coord.send("orchestrator", "request", f"integration red at {culprit[:12]}", body)
    return recipient


def record(run, row):
    row = {
        "time": time.time(),
        "published": str(run.published),
        "builds": len(run.builds),
        "build_seconds": [build["seconds"] for build in run.builds],
        "wall_seconds": round(time.monotonic() - run.started, 3),
        **row,
    }
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    with ledger_path().open("a") as ledger:
        ledger.write(json.dumps(row) + "\n")
    return row


def run_once(published, main_repo=s.REPO, name=NAME):
    """Integrate the batch from the published commit G to main head M; returns the row."""
    published = Path(published)
    start = live_commit(published)
    end = s.git("rev-parse", "main", cwd=main_repo)
    if start is None:
        raise ValueError(f"no published live release in {published}")
    if start == end:
        return None
    run = Run(published, main_repo, name)
    status_path().parent.mkdir(parents=True, exist_ok=True)
    s.write_json(
        status_path(), {"pid": os.getpid(), "started": time.time(), "from": start, "to": end}
    )
    base = {"from": start, "to": end}
    changed = s.git("diff", "--name-only", start, end, cwd=main_repo).splitlines()
    affected = domains(changed)
    if not affected:
        return record(run, {**base, "result": "skipped", "changed_paths": len(changed)})
    base["domains"] = affected
    with host_slots.slot("integration") as inherited:
        if not inherited:
            os.environ["TURKU3D_INTEGRATION_OWNER"] = (
                f"{os.getpid()}:{host_slots.identity(os.getpid())}"
            )
        try:
            report, failure = run.build(start, end)
            if report is None:
                parent, culprit, failure = bisect(run, start, end, failure)
                task = author(culprit)
                reverted = revert(run, parent, culprit)
                return record(
                    run,
                    {
                        **base,
                        "result": "red",
                        "culprit": culprit,
                        "culprit_task": task,
                        "failure": failure,
                        "revert": reverted,
                        "notified": notify(task, culprit, failure, reverted),
                    },
                )
            row = {
                **base,
                "seconds": report["seconds"],
                "cache_hits": report["cache_hits"],
                "facades_bound": report["facades_bound"],
                "facades_baseline": report["facades_baseline"],
                "culprit": None,
            }
            work = Path(report.pop("work"))
            try:
                live_build.prepare(work, published, end, report)
                # Main is already at M; only the live link moves, and publish rolls it
                # back to G if any stable path does not resolve into the new release.
                live_build.publish(published, end)
            except (ValueError, OSError) as exc:
                return record(run, {**row, "result": "failed", "failure": {"detail": str(exc)}})
            return record(run, {**row, "result": "green"})
        finally:
            if not inherited:
                os.environ.pop("TURKU3D_INTEGRATION_OWNER", None)


def running():
    """The in-progress integration (from, to, commit, stage, elapsed), or None."""
    path = lock_path()
    if not path.exists():
        return None
    with path.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(file, fcntl.LOCK_UN)
            return None
    current = s.read_json(status_path(), {})
    stage = None
    try:
        for line in Path(current.get("log", "")).read_text().splitlines():
            if line.startswith("== ") and ":" in line:
                stage = line[3:].split(":", 1)[0]
    except (OSError, ValueError):
        pass
    elapsed = time.time() - current["started"] if current.get("started") else None
    return {**current, "stage": stage, "elapsed": round(elapsed, 1) if elapsed else None}


def start(published):
    """Start one detached integration; its own lock keeps a single run at a time."""
    log = registry() / "integration.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as output:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run", "--published", str(published)],
            cwd=s.REPO,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
    parser.add_argument("--main-repo", type=Path, default=s.REPO)
    parser.add_argument("--sandbox", default=NAME)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps({"running": running(), "last": last(args.published)}, indent=2))
        return 0
    if not args.published:
        parser.error("--published or TURKU3D_DATA is required")
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    with lock_path().open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("integration already running", file=sys.stderr)
            return 0
        try:
            row = run_once(args.published.resolve(), args.main_repo, args.sandbox)
        finally:
            status_path().unlink(missing_ok=True)
        print(json.dumps(row))
        return 0 if row is None or row["result"] in ("green", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
