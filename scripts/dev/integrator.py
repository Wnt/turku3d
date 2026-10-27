"""Build, verify and publish the live release for main once per landed batch.

The lander fast-forwards main after the fast gate. This integrator then builds the batch
G..M (G = commit of the published live release, M = main head) once in its own sandbox.
Green publishes M. Red builds G against the current data root first: a red G is `data-red`
(the data root changed, no commit is blamed or reverted). Only a green G bisects the batch's
landings and reverts the one culprit landing on main through the land lock and the fast
gate, tells its author and leaves live at G.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import coordination as coord
import host_slots
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


def data_key(published):
    """Identity of the data root a run builds against: approval MANIFEST, the playable tile
    list and published layers.

    The queue retries a run for the same main head only when this key changes.
    """
    h = hashlib.sha256()
    manifest = published / "facade-approvals/assembly/MANIFEST.json"
    h.update(manifest.read_bytes() if manifest.is_file() else b"missing:approvals")
    listed = published / "playable-tiles.json"
    h.update(listed.read_bytes() if listed.is_file() else b"missing:playable-tiles")
    layers = published / "build/layers"
    if not layers.is_dir():
        h.update(b"missing:layers")
    for file in sorted(layers.rglob("*")) if layers.is_dir() else ():
        if file.is_file():
            stat = file.stat()
            h.update(f"{file.relative_to(layers)}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
    return h.hexdigest()


def settled(published, main):
    """True when main needs no new integration: it is live, or its last run saw this data."""
    if main == live_commit(published):
        return True
    row = last(published)
    if row.get("to") != main:
        return False
    # Rows written before the data key existed keep the old main-only rule.
    return row.get("data_key") in (None, data_key(published))


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


def checkout(commit, name, published):
    """The reused integration sandbox, reset to commit with its dependencies in sync."""
    try:
        repo = s.sandbox(name) / "repo"
    except ValueError:
        repo = s.new(name, commit)
    if s.git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise ValueError(f"integration sandbox has tracked changes: {repo}")
    s.git("reset", "--hard", commit, cwd=repo)
    # A fresh or reset checkout may need new npm/Python dependencies; the sync is stamped.
    sync = s.run_group(
        [str(repo / "scripts/dev/wt"), "sync-env"], cwd=repo, env=environment(repo, published)
    )
    if sync.returncode:
        raise ValueError(f"environment sync failed: {s.failure_detail(sync.stdout)}")
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

    def __init__(self, published, main_repo, name, end):
        self.published = published
        self.main_repo = main_repo
        self.name = name
        runs = sum(row.get("to") == end for row in read_rows(ledger_path()))
        # The row id names the integration in revert commits and messages.
        self.id = f"{end[:12]}.{runs + 1}"
        self.builds = []
        self.started = time.monotonic()
        self.previous = (published / "live").resolve()

    def build(self, start, commit):
        """(report, None) when commit builds green, else (None, failure)."""
        # Imported here: the daemon imports this module, and the lander path must not
        # load the live build.
        import live_build

        repo = checkout(commit, self.name, self.published)
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
                failure = {"stage": exc.stage, "detail": str(exc)[-1000:], "log": str(log)}
                if isinstance(exc, live_build.FacadeRegression):
                    failure["facades_bound"] = exc.bound
                    failure["facades_baseline"] = exc.baseline
                return None, failure
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
    points = landings(start, end, run.main_repo)
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


def revert_message(run, task, failure, commits):
    """Commit message of an integrator revert: integration row id, stage and exact commits."""
    lines = [
        f"Revert landing {task or 'unknown'} ({commits[-1][:12]}): integration {run.id} "
        f"red at {failure['stage']}",
        "",
        f"Integration: {run.id}",
        f"Failing stage: {failure['stage']}",
        f"Log: {failure['log']}",
        "Reverted commits:",
    ]
    for commit in commits:
        subject = s.git("log", "-1", "--format=%s", commit, cwd=run.main_repo)
        lines.append(f"  {commit} {subject}")
    return "\n".join([*lines, "", f"Detail: {failure['detail'][-300:]}"]) + "\n"


def revert(run, parent, culprit, task, failure):
    """Revert the culprit landing parent..culprit as one commit on current main.

    One revert per run: a further culprit is only blamed by a later run, after this
    reverted main has been built (head first, then the base check).
    """
    commits = s.git("rev-list", "--reverse", f"{parent}..{culprit}", cwd=run.main_repo).splitlines()
    with s.lock(registry() / "land.lock"):
        before = s.git("rev-parse", "main", cwd=run.main_repo)
        if s.git("status", "--porcelain", "--untracked-files=no", cwd=run.main_repo):
            return {"error": "main worktree is dirty", "reverted": []}
        repo = checkout(before, run.name, run.published)
        try:
            s.git("revert", "--no-commit", f"{parent}..{culprit}", cwd=repo)
        except subprocess.CalledProcessError as exc:
            s.git("revert", "--abort", cwd=repo, check=False)
            return {"error": f"revert conflict: {exc.stderr.strip()[-300:]}", "reverted": []}
        if not s.git("diff", "--cached", "--name-only", cwd=repo):
            s.git("revert", "--abort", cwd=repo, check=False)
            return {"error": "revert is empty; the landing is already reverted", "reverted": []}
        s.git("commit", "-q", "-m", revert_message(run, task, failure, commits), cwd=repo)
        started = time.monotonic()
        gate = s.run_group(
            [str(repo / "scripts/dev/gate"), "--fast", "--base", before],
            cwd=repo,
            env=environment(repo, run.published),
        )
        seconds = round(time.monotonic() - started, 3)
        if gate.returncode:
            return {
                "error": f"fast gate red on revert: {s.failure_detail(gate.stdout)}",
                "reverted": [],
            }
        head = s.git("rev-parse", "HEAD", cwd=repo)
        s.git("merge", "--ff-only", head, cwd=run.main_repo)
        row = {
            "name": f"revert-{culprit[:8]}",
            "before": before,
            "commit": head,
            "source": "integrator",
            "integration": run.id,
            "reverts": f"{parent}..{culprit}",
            "reverted": commits,
            "time": time.time(),
            "fast_gate_seconds": seconds,
        }
        with (registry() / "landed.jsonl").open("a") as ledger:
            ledger.write(json.dumps(row) + "\n")
        return {"commit": head, "reverted": commits, "fast_gate_seconds": seconds}


def notify(run, task, culprit, failure, reverted):
    detail = (
        f"reverted on main as {reverted['commit'][:12]}"
        if "commit" in reverted
        else f"NOT reverted ({reverted['error']})"
    )
    body = (
        f"Integration {run.id} is red at your landing {culprit[:12]}: {failure['stage']} "
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


def warn_data_red(run, row):
    """Tell everyone the data root, not a commit, turned the batch red; returns the message id."""
    failure = row["failure"]
    counts = (
        f" ({failure['facades_bound']} facades bound, published baseline "
        f"{failure['facades_baseline']})"
        if "facades_bound" in failure
        else ""
    )
    cause = (
        "The facade approval root's MANIFEST.json differs from the published release's."
        if row["data_cause"] == "approval-root-changed"
        else "The batch base itself is red against the current data root."
    )
    body = (
        f"Integration {run.id} ({row['from'][:12]}..{row['to'][:12]}) is data-red: the base "
        f"{row['from'][:12]} builds red at {failure['stage']}{counts}. {cause} No commit "
        f"was reverted; live stays on {row['from'][:12]}. The run is retried when main moves "
        "or the data root changes. Accept a deliberate facade drop by landing a commit whose "
        "result carries `Facade regression reason: ...`. "
        f"Log: {failure['log']}. Detail: {failure['detail'][-400:]}"
    )
    subject = f"integration {run.id} data-red at {failure['stage']}"
    try:
        coord.send("all", "warning", subject, body)
    except (ValueError, OSError):
        coord.send("orchestrator", "warning", subject, body)
    return subject


def record(run, row):
    row = {
        "id": run.id,
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


def data_red(run, base, head_failure, base_failure):
    """The batch base G is red against the current data root: blame and revert nothing."""
    import live_build

    manifests = live_build.approval_manifests(run.published)
    row = {
        **base,
        "result": "data-red",
        "culprit": None,
        "failure": base_failure,
        "head_failure": head_failure,
        "data_cause": (
            "approval-root-changed"
            if manifests["published"] != manifests["current"]
            else "data-root"
        ),
        "approval_manifest": manifests,
        "revert": None,
    }
    row["notified"] = warn_data_red(run, row)
    return record(run, row)


def integrate(run, base, start, end):
    """Build M; publish when green. Red: build G; red G is data-red, green G bisects."""
    import live_build

    published = run.published
    report, failure = run.build(start, end)
    if report is None:
        # Base check before blame: G was green when published, so a red G means the data
        # root changed under it, and no commit in the batch caused the failure.
        _, base_failure = run.build(start, start)
        if base_failure:
            return data_red(run, base, failure, base_failure)
        parent, culprit, failure = bisect(run, start, end, failure)
        task = author(culprit)
        reverted = revert(run, parent, culprit, task, failure)
        return record(
            run,
            {
                **base,
                "result": "red",
                "culprit": culprit,
                "culprit_task": task,
                "failure": failure,
                "revert": reverted,
                "notified": notify(run, task, culprit, failure, reverted),
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
    row["result"] = "green"
    deployed = publish_pages(published)
    if deployed:
        row["pages"] = deployed
    return record(run, row)


def publish_pages(published):
    """Deploy the new live release to GitHub Pages when publish/pages.json exists.

    A failure is reported in the integration row; the local publication stands.
    """
    import pages

    try:
        if pages.config(published) is None:
            return None
        return pages.publish(published)
    except subprocess.CalledProcessError as exc:
        return {"error": f"{' '.join(map(str, exc.cmd[:3]))}: {(exc.stderr or '').strip()[-500:]}"}
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)[-500:]}


def run_once(published, main_repo=s.REPO, name=NAME):
    """Integrate the batch from the published commit G to main head M; returns the row."""
    published = Path(published)
    start = live_commit(published)
    end = s.git("rev-parse", "main", cwd=main_repo)
    if start is None:
        raise ValueError(f"no published live release in {published}")
    if start == end:
        return None
    run = Run(published, main_repo, name, end)
    status_path().parent.mkdir(parents=True, exist_ok=True)
    s.write_json(
        status_path(), {"pid": os.getpid(), "started": time.time(), "from": start, "to": end}
    )
    base = {"from": start, "to": end, "data_key": data_key(published)}
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
            return integrate(run, base, start, end)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            # Infrastructure failures (sandbox, env sync, git) name no culprit.
            failure = {"detail": str(exc)[-1000:]}
            return record(run, {**base, "result": "failed", "failure": failure})
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
