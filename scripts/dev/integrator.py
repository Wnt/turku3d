"""Build and publish live and Pages for each landed batch.

The code lane builds main's game with served tiles; the data lane rebuilds changed tiles
with main's tile code and carries the live game. Inert batches repoint without building.
Only build failures trigger base checks and bisection; advisory failures never revert.
Chrome, audits and the deep gate run after publication and yield to newer landings.

Landings start idle lanes; finished runs start successors when main or data moved. Joins
are allowed only before the first uncached build stage. publish.lock serialises publication:
code releases recompose on newer tiles, data releases carry a newer live game. --refresh-stale
rebuilds stale_by_code tiles with main's tile code when the host is idle.
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

import advisory
import coordination as coord
import host_slots
import integration_advisory
import integration_chrome as chrome
import integration_messages as messages
import landing
import landing_class
import pages
import sandbox as s
import served

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
    """Identity of the data root a run builds against (landing_class.data_parts); the data lane
    runs once per (live commit, data key)."""
    parts = landing_class.data_parts(published)
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def batch_class(published, start, end, repo):
    """The code lane's class of the batch start..end: its paths only (the data lane owns the
    data root)."""
    return landing_class.classify(repo, start, end, [], published)


def release_delta(published):
    """The data-root parts the live release does not carry yet."""
    return landing_class.data_delta(
        landing_class.data_parts(published),
        landing_class.release_parts((published / "live").resolve()),
    )


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
    """True when main needs no new code-lane run: it is live, or its last run took it."""
    if full_due(published):
        return False
    return main in (live_commit(published), last(published).get("to"))


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
        # Whether this run is the scheduled full Chrome suite; self.chrome is the Chrome plan.
        self.full = full
        self.kind = "data" if lane == DATA_NAME else "full" if full else "code"
        self.chrome = chrome.full_plan() if full else None
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
        checkout(head, self.name, self.published)
        self.end = head
        self.joined.append(head)
        status(self.lane, to=head, commit=head, **{"class": self.landing_class})
        return {
            "commit": head,
            "kind": self.kind,
            "reason": landing.facade_reason_in(start, head, self.main_repo),
            "chrome": self.chrome,
        }

    def timed(self, commit, started):
        self.builds.append({"commit": commit, "seconds": round(time.monotonic() - started, 3)})

    def build(self, start, commit, join=False, checks=True):
        """(report, None) when commit builds, else (None, failure); report["advisory"] lists
        the advisory checks that failed.

        join lets main heads that land before the first expensive stage join (self.end);
        checks=False runs the build stages only (base check and bisection).
        """
        # Imported here: the daemon imports this module, and the lander path must not
        # load the live build.
        import live_build

        repo = checkout(commit, self.name, self.published)
        env = environment(repo, self.published, self.lane)
        work = Path(env["TURKU3D_WORK"])
        reason = landing.facade_reason_in(start, commit, repo)
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
                self.kind,
                (lambda cached: self.join(start, cached)) if join else None,
                chrome=self.chrome,
                timings=self.stage_seconds,
                checks=checks,
            )
        except live_build.StageFailed as exc:
            failure = {"stage": exc.stage, "detail": str(exc)[-1000:], "log": str(log)}
            return None, failure
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            return None, {"stage": "build", "detail": str(exc)[-1000:], "log": str(log)}
        finally:
            self.timed(self.end if join else commit, started)
        return {**report, "work": str(work)}, None


def bisect(run, start, end, failure):
    """First landing in start..end whose build is red, with its failure (end is red)."""
    points = landing.landed_heads(start, end, run.main_repo)
    low, high = -1, len(points) - 1
    failures = {high: failure}
    while high - low > 1:
        middle = (low + high) // 2
        report, failed = run.build(start, points[middle], checks=False)
        if report:
            low = middle
        else:
            high = middle
            failures[high] = failed
    return (points[high - 1] if high else start), points[high], failures[high]


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
        s.git("commit", "-q", "-m", messages.revert_message(run, task, failure, commits), cwd=repo)
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
    row["notified"] = messages.warn_data_red(run, row)
    return record(run, row)


def integrate(run, base, start, end):
    """Build M's game bundle; publish it with the served tiles and Pages when it builds,
    whatever its advisory checks say. A build failure builds G; red G is data-red, green G
    bisects."""
    import live_build

    published = run.published
    report, failure = run.build(start, end, join=True)
    end = run.end
    base.update(run.landing, to=end, batch_joined=run.joined, chrome_plan=run.chrome)
    if report is None:
        # Base check before blame: G built when published, so a red G means the data
        # root changed under it, and no commit in the batch caused the failure.
        _, base_failure = run.build(start, start, checks=False)
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
                "notified": messages.notify(run, task, culprit, failure, reverted),
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
        shipped = report.pop("shipped")
        row["advisory"] = report["advisory"]
        row["tiles_from"] = shipped["source"]
        with publish_lock():
            live = (published / "live").resolve()
            known = advisory.release_advisory(live)
            if landing_class.release_parts(live) == run.live_parts:
                try:
                    release = live
                    if end != live_commit(published):
                        record_fields = {
                            **report,
                            **served.provenance(run.main_repo, end, shipped),
                            "full_suite": False,
                            "advisory_pending": True,
                            "from": start,
                        }
                        release = live_build.prepare(work, published, end, record_fields)
                        # Main is already at M; only the live link moves, and publish rolls
                        # it back to G if a stable path does not resolve into the release.
                        live_build.publish(published, end)
                except (ValueError, OSError) as exc:
                    failure = {"detail": str(exc)}
                    return record(run, {**row, "result": "failed", "failure": failure})
                break
        # The data lane published new tiles meanwhile: M is recomposed on them (its game
        # bundle is a cache hit), never published over them. A batch recomposes at most once;
        # after that the next run takes the newer release.
        if "rebuilt_on" in row:
            detail = f"live changed again during the rebuild (now {live.name}); next run publishes"
            return record(run, {**row, "result": "failed", "failure": {"detail": detail}})
        run.previous, run.live_parts = live, landing_class.release_parts(live)
        row["rebuilt_on"] = live.name
        report, failure = run.build(start, end)
        if report is None:
            return record(run, {**row, "result": "failed", "failure": failure})
    if deployed := pages.deploy(published):
        row["pages"] = deployed
    row["publish_seconds"] = round(time.monotonic() - run.started, 3)
    return integration_advisory.finish(
        run,
        row,
        report,
        work,
        release,
        known,
        environment=environment,
        registry=registry,
        record=record,
    )


def repoint(run, base, start, end):
    """Inert batch: no build; live moves to end with the content of the live release."""
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
        if deployed := pages.deploy(run.published):
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


def begin(lane, start, end, landing, **fields):
    status_path(lane).parent.mkdir(parents=True, exist_ok=True)
    fields = {"pid": os.getpid(), "started": time.time(), "from": start, "to": end, **fields}
    s.write_json(status_path(lane), {**fields, "class": landing["class"]})


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
    start = live
    landing = batch_class(published, start, end, main_repo)
    landing = {**landing, "class": "code"} if full else landing
    run = Run(published, main_repo, name, end, landing, full=full)
    begin(NAME, start, end, landing)
    base = {"from": start, "to": end, **landing, "full_suite": full}
    if landing["class"] == "inert":
        return repoint(run, base, start, end)
    with reserved(NAME):
        try:
            return integrate(run, base, start, end)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            # Infrastructure failures (sandbox, env sync, git) name no culprit.
            failure = {"detail": str(exc)[-1000:]}
            return record(run, {**base, "result": "failed", "failure": failure})


def data_due(published):
    """Whether the data lane should rebuild tiles now: the data root has parts the live release
    does not carry, and no data run took this (live commit, data key) yet."""
    start = live_commit(published)
    if start is None or running(DATA_NAME) or not release_delta(published):
        return False
    key = data_key(published)
    return not any(
        row.get("lane") == "data" and row.get("from") == start and row.get("data_key") == key
        for row in read_rows(ledger_path())
        if row.get("published") == str(published)
    )


def run_data(published, main_repo=s.REPO, name=DATA_NAME, refresh_stale=False):
    """Data lane: rebuild the tiles the changed data root affects (refresh_stale: the live
    tiles whose tile code is older than main's), using main's tile code. Publish with the live
    game as release <G>-<data key>. When the code lane published a newer game on the same
    tiles meanwhile, the release ships that game; when it changed the tiles, it is superseded."""
    import live_build

    published = Path(published)
    start = live_commit(published)
    if start is None:
        raise ValueError(f"no published live release in {published}")
    live = (published / "live").resolve()
    delta = release_delta(published)
    stale = served.record(live).get("stale_by_code", [])
    if not (stale if refresh_stale else delta):
        return None
    builder = s.git("rev-parse", "main", cwd=main_repo)
    landing = landing_class.classify(main_repo, start, start, delta, published)
    run = Run(published, main_repo, name, start, landing, lane=DATA_NAME)
    run.chrome = chrome.data_plan(published, main_repo)
    key = data_key(published)
    begin(DATA_NAME, start, start, landing, data_key=key)
    base = {"lane": "data", "from": start, "to": start, "data_key": key, **landing}
    if refresh_stale:
        base["refresh_stale"] = stale
    with reserved(DATA_NAME):
        try:
            report, failure = run.build(start, builder)
            if report is None:
                return data_red(run, base, failure, failure)
            row = {
                **base,
                "tiles_commit": builder,
                "seconds": report["seconds"],
                "cache_hits": report["cache_hits"],
                "facades_bound": report["facades_bound"],
                "advisory": report["advisory"],
                "culprit": None,
            }
            work = Path(report.pop("work"))
            shipped = served.built(builder, live, report["tiles"], report.pop("tiles_built"))
            with publish_lock():
                current = (published / "live").resolve()
                if current != live:
                    if landing_class.release_parts(current) != run.live_parts:
                        # New tiles were published first; the next round rebuilds on them.
                        return record(run, {**row, "result": "superseded"})
                    # A code release of a newer main on the same tiles: ship its game.
                    start = live_commit(published)
                    served.carry_game(current, work)
                    row["to"] = start
                release = f"{start}-{key[:12]}" + (f"-{builder[:12]}" if refresh_stale else "")
                fields = {
                    **report,
                    **served.provenance(main_repo, start, shipped),
                    "from": start,
                    "advisory_pending": True,
                }
                live_build.prepare(work, published, start, fields, release)
                live_build.publish(published, start, release)
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            return record(
                run, {**base, "result": "failed", "failure": {"detail": str(exc)[-1000:]}}
            )
    if deployed := pages.deploy(published):
        row["pages"] = deployed
    row["publish_seconds"] = round(time.monotonic() - run.started, 3)
    release = published / "live-releases" / release
    # Diagnostics need a host reservation too; code and data builds retain separate slots.
    with reserved(DATA_NAME):
        return integration_advisory.finish(
            run,
            row,
            report,
            work,
            release,
            advisory.release_advisory(live),
            environment=environment,
            registry=registry,
            record=record,
        )


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
    if data_due(published):
        start(published, "data")
        started.append("data")
    return started


def refresh_stale(published):
    """Start the data lane on the live tiles marked stale_by_code (make tiles-refresh-stale,
    run by the sentinel's heartbeat on an idle host); False when none are or it is busy."""
    if live_commit(published) is None or running(DATA_NAME):
        return False
    if not served.record((Path(published) / "live").resolve()).get("stale_by_code"):
        return False
    start(published, "data", refresh_stale=True)
    return True


def start(published, lane="code", refresh_stale=False):
    """Start one detached integration of a lane; its own lock keeps one run per lane."""
    log = registry() / f"{LANES[lane]}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = ["run", "--published", str(published), "--lane", lane]
    command += ["--refresh-stale"] if refresh_stale else []
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
    parser.add_argument("command", choices=("run", "status", "ensure", "refresh-stale"))
    parser.add_argument("--published", type=Path, default=os.environ.get("TURKU3D_DATA"))
    parser.add_argument("--main-repo", type=Path, default=s.REPO)
    parser.add_argument("--lane", choices=tuple(LANES), default="code")
    parser.add_argument("--sandbox", help="integration sandbox (default: the lane's)")
    parser.add_argument(
        "--full", action="store_true", help="code lane: the full Chrome suite now (advisory)"
    )
    parser.add_argument(
        "--refresh-stale",
        action="store_true",
        help="data lane: rebuild the live tiles listed stale_by_code with main's tile code",
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
    if args.command == "refresh-stale":
        print(json.dumps({"started": refresh_stale(args.published.resolve())}))
        return 0
    lock_path(lane).parent.mkdir(parents=True, exist_ok=True)
    with lock_path(lane).open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{lane} already running", file=sys.stderr)
            return 0
        run = (
            functools.partial(run_data, refresh_stale=args.refresh_stale)
            if args.lane == "data"
            else functools.partial(run_once, full=args.full)
        )
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
