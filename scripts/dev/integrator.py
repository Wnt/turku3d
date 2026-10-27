"""Build, verify and publish the live release for main once per landed batch.

The lander fast-forwards main after the fast gate. This integrator then classifies the batch
G..M (G = commit of the published live release, M = main head; landing_class.py): `inert`
re-points live to M without a build, `data` and `code` build M once in its own sandbox with
the class's stages (live_build.stages_for). Green publishes M. Red builds G against the
current data root first: a red G is `data-red` (the data root changed, no commit is blamed
or reverted). Only a green G bisects the batch's landings and reverts the one culprit
landing on main through the land lock and the fast gate, tells its author and leaves live
at G.

Nothing polls: each landing starts a run when none is going (ensure), and a finished run starts
the next when main or the data root moved meanwhile. A newer main joins only while that is free
(before the first stage that is not a cache hit); then the batch is frozen. A second lane (`--lane data`, sandbox `integration-data`) builds the live commit G against a changed
data root while the code lane is busy; publication is serialised under `publish.lock`, and a
lane whose live release changed meanwhile rebuilds on top of it instead of publishing over it.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import coordination as coord
import host_slots
import integration_chrome as chrome
import landing
import landing_class
import pages
import sandbox as s

NAME = "integration"
DATA_NAME = "integration-data"
LANES = {"code": NAME, "data": DATA_NAME}
# Main heads one batch may take in, and how often joins may repeat one (cached) stage.
JOIN_LIMIT = 3
RESTART_LIMIT = 1
# An inert batch republishes Pages only when the image licence list changed.
PAGES_INPUTS = ("docs/published-images.toml",)


def registry():
    return s.root() / ".registry"


def ledger_path():
    return registry() / "integrations.jsonl"


def status_path(lane=NAME):
    return registry() / f"{lane}.json"


def lock_path(lane=NAME):
    return registry() / f"{lane}.lock"


def publish_lock():
    """Held while a lane checks the live release and publishes over it."""
    return s.lock(registry() / "publish.lock")


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
    """The latest code-lane integration row for this published root, or {}."""
    rows = code_rows(published)
    return rows[-1] if rows else {}


def data_key(published):
    """Identity of the data root a run builds against (landing_class.data_parts).

    A new run for the same main head starts only when this key changes (settled).
    """
    parts = landing_class.data_parts(published)
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def batch_class(published, start, end, repo):
    """The landing class of the batch start..end against the live release and data root."""
    delta = landing_class.data_delta(
        landing_class.data_parts(published),
        landing_class.release_parts((published / "live").resolve()),
    )
    return landing_class.classify(repo, start, end, delta, published)


def code_rows(published):
    return [
        row
        for row in read_rows(ledger_path())
        if row.get("published") == str(published) and row.get("lane") != "data"
    ]


def full_due(published, now=None):
    landed = landing.ledger_rows()
    return chrome.full_due(code_rows(published), landed, time.time() if now is None else now)


def settled(published, main):
    """True when main needs no new integration: it is live, or its last run saw this data."""
    if full_due(published):
        return False
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


def environment(repo, published, lane=NAME):
    env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
    env["TURKU3D_DATA"] = str(published)
    owner = os.environ.get("TURKU3D_INTEGRATION_OWNER")
    if owner:
        env["TURKU3D_INTEGRATION_OWNER"] = owner
        env["TURKU3D_JOBS"] = str(host_slots.integration_jobs(lane=lane))
    return env


def status(lane=NAME, **fields):
    current = s.read_json(status_path(lane), {})
    s.write_json(status_path(lane), {**current, **fields})


class Run:
    """One integration of G..M; `build` counts builds and keeps every stage log."""

    def __init__(self, published, main_repo, name, end, landing=None, lane=NAME, full=False):
        self.published = published
        self.main_repo = main_repo
        self.name = name
        self.lane = lane
        # The Chrome plan; bisection builds reuse it so the failing specs rerun.
        self.full = full
        self.chrome = None
        # The batch end M and its class fields; a join moves both to a newer main.
        self.end = end
        self.landing = landing or {"class": "code"}
        self.joined = []
        # Per stage: how often a join repeated it, and its seconds summed over every build
        # of the run (restarted, rebuilt and bisection builds included).
        self.restarts = {}
        self.stage_seconds = {}
        runs = sum(row.get("to") == end for row in read_rows(ledger_path()))
        # The row id names the integration in revert commits and messages.
        self.id = f"{end[:12]}.{runs + 1}"
        self.builds = []
        self.started = time.monotonic()
        self.previous = (published / "live").resolve()
        # What live held when the run started; a different value at publication means
        # another lane published meanwhile.
        self.live_parts = landing_class.release_parts(self.previous)

    @property
    def landing_class(self):
        return self.landing["class"]

    def join(self, start, cached=()):
        """Take in a newer main while the build has only restored cached stages; the build
        update or None. live_build.build offers this once, before its first real stage."""
        head = s.git("rev-parse", "main", cwd=self.main_repo)
        if head == self.end or len(self.joined) >= JOIN_LIMIT:
            return None
        if any(self.restarts.get(stage, 0) >= RESTART_LIMIT for stage in cached):
            return None
        for stage in cached:
            self.restarts[stage] = self.restarts.get(stage, 0) + 1
        self.landing = batch_class(self.published, start, head, self.main_repo)
        if self.full:
            self.landing = {**self.landing, "class": "code"}
        else:
            self.chrome = chrome.batch_plan(self.main_repo, start, head, self.landing)
        checkout(head, self.name, self.published)
        self.end = head
        self.joined.append(head)
        # The restarted build seeds the current data root, so the lane now covers its key.
        key = data_key(self.published)
        status(self.lane, to=head, commit=head, data_key=key, **{"class": self.landing_class})
        return {
            "commit": head,
            "landing_class": self.landing_class,
            "reason": landing.facade_reason_in(start, head, self.main_repo),
            "chrome": self.chrome,
        }

    def timed(self, commit, started):
        self.builds.append({"commit": commit, "seconds": round(time.monotonic() - started, 3)})

    def build(self, start, commit, join=False, kind=None):
        """(report, None) when commit builds green, else (None, failure).

        join lets main heads that land before the first expensive stage join (self.end);
        kind overrides the batch class (a data rebuild on top of another lane's release).
        """
        # Imported here: the daemon imports this module, and the lander path must not
        # load the live build.
        import live_build

        repo = checkout(commit, self.name, self.published)
        env = environment(repo, self.published, self.lane)
        work = Path(env["TURKU3D_WORK"])
        reason = landing.facade_reason_in(start, commit, repo)
        for attempt in (1, 2):
            if join and commit != self.end:
                commit, reason = self.end, landing.facade_reason_in(start, self.end, repo)
            log = registry() / f"integration-{commit[:12]}-{len(self.builds) + 1}.log"
            status(self.lane, commit=commit, log=str(log))
            started = time.monotonic()
            try:
                report = live_build.build(
                    repo,
                    work,
                    self.published,
                    env,
                    log,
                    reason,
                    self.previous,
                    kind or self.landing_class,
                    (lambda cached: self.join(start, cached)) if join else None,
                    chrome=chrome.data_plan(self.published) if kind == "data" else self.chrome,
                    timings=self.stage_seconds,
                )
            except live_build.StageFailed as exc:
                self.timed(self.end if join else commit, started)
                # One rerun guards against a flaky Chrome stage; cached stages are free.
                if exc.stage.startswith("chrome") and attempt == 1:
                    continue
                failure = {"stage": exc.stage, "detail": str(exc)[-1000:], "log": str(log)}
                if isinstance(exc, live_build.FacadeRegression):
                    failure["facades_bound"] = exc.bound
                    failure["facades_baseline"] = exc.baseline
                return None, failure
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                self.timed(self.end if join else commit, started)
                return None, {"stage": "build", "detail": str(exc)[-1000:], "log": str(log)}
            self.timed(self.end if join else commit, started)
            return {**report, "work": str(work)}, None
        raise AssertionError("unreachable")


def bisect(run, start, end, failure):
    """First landing in start..end whose build is red, with its failure (end is red)."""
    points = landing.landed_heads(start, end, run.main_repo)
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
        with landing.ledger_path().open("a") as ledger:
            ledger.write(json.dumps(row) + "\n")
        # A revert moves main like a landing: active tasks rebase onto it.
        paths = s.git("diff", "--name-only", before, head, cwd=run.main_repo).splitlines()
        subject = s.git("log", "-1", "--format=%s", head, cwd=run.main_repo)
        with contextlib.suppress(ValueError, OSError, subprocess.CalledProcessError):
            coord.broadcast_landed(
                row["name"],
                {**row, "class": "revert", "paths": paths[:40], "changed_paths": len(paths)},
                subject,
                time.time(),
            )
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
        "joins": len(run.joined),
        "restarts": run.restarts,
        "stage_seconds": run.stage_seconds,
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
    report, failure = run.build(start, end, join=True)
    end = run.end
    base.update(run.landing, to=end, batch_joined=run.joined, chrome_plan=run.chrome)
    if report is None:
        # Base check before blame: G was green when published, so a red G means the data
        # root changed under it, and no commit in the batch caused the failure.
        _, base_failure = run.build(start, start)
        if base_failure:
            return data_red(run, base, failure, base_failure)
        parent, culprit, failure = bisect(run, start, end, failure)
        task = landing.author(culprit)
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
        "chrome": report.get("chrome"),
        "culprit": None,
    }
    while True:
        work = Path(report.pop("work"))
        with publish_lock():
            live = (published / "live").resolve()
            if landing_class.release_parts(live) == run.live_parts:
                try:
                    if end == live_commit(published):  # a full run verifies live in place
                        chrome.mark_full_suite(published)
                    else:
                        live_build.prepare(work, published, end, {**report, "full_suite": run.full})
                        # Main is already at M; only the live link moves, and publish rolls
                        # it back to G if a stable path does not resolve into the release.
                        live_build.publish(published, end)
                except (ValueError, OSError) as exc:
                    failure = {"detail": str(exc)}
                    return record(run, {**row, "result": "failed", "failure": failure})
                break
        # The data lane published newer data meanwhile: verified code M is rebuilt on top of
        # it with the data stages, as a data landing after M would be, never published over it.
        # A batch rebuilds at most once; after that the next run builds on the newer release.
        if "rebuilt_on" in row:
            detail = f"live changed again during the rebuild (now {live.name}); the next run publishes"
            return record(run, {**row, "result": "failed", "failure": {"detail": detail}})
        run.previous, run.live_parts = live, landing_class.release_parts(live)
        row["rebuilt_on"] = live.name
        report, failure = run.build(start, end, kind="data")
        if report is None:
            return data_red(run, base, failure, failure)
    row["result"] = "green"
    deployed = pages.deploy(published)
    if deployed:
        row["pages"] = deployed
    return record(run, row)


def repoint(run, base, start, end):
    """Inert batch: no build; live moves to end with the content of the verified release."""
    import live_build

    row = {**base, "skipped": True, "culprit": None}
    try:
        with publish_lock():
            live_build.repoint(run.published, start, end)
    except (ValueError, OSError) as exc:
        return record(run, {**row, "result": "failed", "failure": {"detail": str(exc)}})
    row["result"] = "green"
    changed = landing_class.changed_paths(run.main_repo, start, end)
    if any(path in PAGES_INPUTS for path in changed):
        deployed = pages.deploy(run.published)
        if deployed:
            row["pages"] = deployed
    return record(run, row)


@contextlib.contextmanager
def reserved(lane):
    """The lane's host slot; nested make targets inherit it through the owner variable."""
    with host_slots.slot(lane) as inherited:
        if not inherited:
            os.environ["TURKU3D_INTEGRATION_OWNER"] = (
                f"{os.getpid()}:{host_slots.identity(os.getpid())}"
            )
        try:
            yield
        finally:
            if not inherited:
                os.environ.pop("TURKU3D_INTEGRATION_OWNER", None)


def begin(lane, start, end, landing, key):
    status_path(lane).parent.mkdir(parents=True, exist_ok=True)
    fields = {"pid": os.getpid(), "started": time.time(), "from": start, "to": end}
    s.write_json(status_path(lane), {**fields, "class": landing["class"], "data_key": key})


def run_once(published, main_repo=s.REPO, name=NAME, full=False):
    """Integrate G..M (full: the full Chrome suite from the last green full run); the row."""
    published = Path(published)
    live = live_commit(published)
    end = s.git("rev-parse", "main", cwd=main_repo)
    if live is None:
        raise ValueError(f"no published live release in {published}")
    full = full or full_due(published)
    if live == end and not full:
        return None
    start = chrome.full_base(code_rows(published), main_repo, live, end) if full else live
    landing = batch_class(published, start, end, main_repo)
    landing = {**landing, "class": "code"} if full else landing
    run = Run(published, main_repo, name, end, landing, full=full)
    key = data_key(published)
    begin(NAME, start, end, landing, key)
    base = {"from": start, "to": end, "data_key": key, **landing, "full_suite": full}
    if landing["class"] == "inert":
        return repoint(run, base, start, end)
    run.chrome = chrome.batch_plan(main_repo, start, end, landing, full)
    with reserved(NAME):
        try:
            return integrate(run, base, start, end)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            # Infrastructure failures (sandbox, env sync, git) name no culprit.
            failure = {"detail": str(exc)[-1000:]}
            return record(run, {**base, "result": "failed", "failure": failure})


def data_due(published, main_repo=s.REPO):
    """Whether the data lane should build live G against the changed data root now.

    Only while the code lane cannot cover the change: it runs with an older data key, or
    main is live so no code run follows. One run per (G, data key).
    """
    start = live_commit(published)
    if start is None or running(DATA_NAME):
        return False
    key = data_key(published)
    code = running(NAME)
    if code:
        if code.get("data_key") == key:
            return False
    elif s.git("rev-parse", "main", cwd=main_repo) != start:
        return False
    live = (published / "live").resolve()
    if not landing_class.data_delta(
        landing_class.data_parts(published), landing_class.release_parts(live)
    ):
        return False
    return not any(
        row.get("lane") == "data" and row.get("from") == start and row.get("data_key") == key
        for row in read_rows(ledger_path())
        if row.get("published") == str(published)
    )


def run_data(published, main_repo=s.REPO, name=DATA_NAME):
    """Data lane: build live G with the data stages against the current data root and
    publish it as release <G>-<data key> unless live moved meanwhile."""
    import live_build

    published = Path(published)
    start = live_commit(published)
    if start is None:
        raise ValueError(f"no published live release in {published}")
    live = (published / "live").resolve()
    current = landing_class.data_parts(published)
    delta = landing_class.data_delta(current, landing_class.release_parts(live))
    if not delta:
        return None
    landing = landing_class.classify(main_repo, start, start, delta, published)
    run = Run(published, main_repo, name, start, landing, lane=DATA_NAME)
    run.chrome = chrome.data_plan(published)
    key = data_key(published)
    begin(DATA_NAME, start, start, landing, key)
    base = {"lane": "data", "from": start, "to": start, "data_key": key, **landing}
    with reserved(DATA_NAME):
        try:
            report, failure = run.build(start, start)
            if report is None:
                return data_red(run, base, failure, failure)
            row = {
                **base,
                "seconds": report["seconds"],
                "cache_hits": report["cache_hits"],
                "facades_bound": report["facades_bound"],
                "culprit": None,
            }
            work = Path(report.pop("work"))
            with publish_lock():
                if (published / "live").resolve() != live:
                    # The code lane published first; the next round rebuilds on top of it.
                    return record(run, {**row, "result": "superseded"})
                release = f"{start}-{key[:12]}"
                live_build.prepare(work, published, start, report, release)
                live_build.publish(published, start, release)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            return record(
                run, {**base, "result": "failed", "failure": {"detail": str(exc)[-1000:]}}
            )
    row["result"] = "green"
    deployed = pages.deploy(published)
    if deployed:
        row["pages"] = deployed
    return record(run, row)


def running(lane=NAME):
    """The lane's in-progress integration (from, to, commit, stage, elapsed), or None."""
    path = lock_path(lane)
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
    current = s.read_json(status_path(lane), {})
    stage = None
    try:
        for line in Path(current.get("log", "")).read_text().splitlines():
            if line.startswith("== ") and ":" in line:
                stage = line[3:].split(":", 1)[0]
    except (OSError, ValueError):
        pass
    elapsed = time.time() - current["started"] if current.get("started") else None
    return {**current, "stage": stage, "elapsed": round(elapsed, 1) if elapsed else None}


def ensure(published, main_repo=s.REPO):
    """Start the lanes that have work; nothing polls. Each landing and each finished run calls
    this: the code lane when main or the data root moved past its last run or the full suite is
    due (settled), the data lane when the data root changed beyond what the code lane covers
    (data_due). Without a published live release nothing starts. Returns the lanes started.
    """
    published = Path(published)
    if live_commit(published) is None:
        return []
    started = []
    if not running(NAME) and not settled(published, s.git("rev-parse", "main", cwd=main_repo)):
        start(published, "code")
        started.append("code")
    if data_due(published, main_repo):
        start(published, "data")
        started.append("data")
    return started


def start(published, lane="code"):
    """Start one detached integration of a lane; its own lock keeps one run per lane."""
    log = registry() / f"{LANES[lane]}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = ["run", "--published", str(published), "--lane", lane]
    with log.open("a") as output:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), *command],
            cwd=s.REPO,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "ensure"))
    parser.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
    parser.add_argument("--main-repo", type=Path, default=s.REPO)
    parser.add_argument("--lane", choices=tuple(LANES), default="code")
    parser.add_argument("--sandbox", help="integration sandbox (default: the lane's)")
    parser.add_argument(
        "--full", action="store_true", help="code lane: the full Chrome suite now (before Pages)"
    )
    args = parser.parse_args()
    lane = LANES[args.lane]
    if args.command == "status":
        lanes = {name: running(name) for name in LANES.values()}
        print(json.dumps({"running": lanes, "last": last(args.published)}, indent=2))
        return 0
    if not args.published:
        parser.error("--published or TURKU3D_DATA is required")
    if args.command == "ensure":
        print(json.dumps({"started": ensure(args.published.resolve(), args.main_repo)}))
        return 0
    lock_path(lane).parent.mkdir(parents=True, exist_ok=True)
    with lock_path(lane).open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{lane} already running", file=sys.stderr)
            return 0
        run = run_data if args.lane == "data" else functools.partial(run_once, full=args.full)
        try:
            row = run(args.published.resolve(), args.main_repo, args.sandbox or lane)
        finally:
            status_path(lane).unlink(missing_ok=True)
    print(json.dumps(row))
    # Landings after this run's join window, or a data root changed meanwhile, start the next
    # run now (a manual run in its own sandbox or against another clone chains nothing).
    if row is not None and args.sandbox is None and args.main_repo.resolve() == s.REPO:
        print(json.dumps({"started": ensure(args.published.resolve(), args.main_repo)}))
    return 0 if row is None or row["result"] == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
