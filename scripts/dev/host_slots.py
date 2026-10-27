"""Host-wide, crash-recovering admission for builds and Chrome suites."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

LIMITS = {"build": 3, "browser": 1, "integration": 1, "integration-data": 1}
# The code lane (`integration`, with Chrome) and the data lane (no Chrome) of the integrator.
# Both are stored as kind `integration` with their `lane`, so tooling that predates the data
# lane counts it as an integration instead of failing on an unknown kind.
INTEGRATIONS = ("integration", "integration-data")

# Host budget in CPU job units and browser (GPU) workers. One integration reserves a
# fixed share; build and browser admissions continue within what remains.
BUDGET = {"jobs": 10, "gpu": 6}
COST = {
    "build": {"jobs": 2},
    "browser": {"gpu": 1},
    # Chrome suites share one GPU. On the M1 Max the 20-test real-tile suite passed in
    # 1.9 min with four workers; ten workers timed out on 9 tests.
    "integration": {"jobs": 6, "gpu": 4},
    "integration-data": {"jobs": 4},
}
# Above this 1-minute load average the integrator's Chrome stage halves its workers:
# the same 32 tests took 251 s at load 50 and 1086 s at load 150-250 with four.
BROWSER_LOAD = 60


def browser_workers(load=None):
    """Chrome workers of the integrator: 4 under BROWSER_LOAD, else 2."""
    load = os.getloadavg()[0] if load is None else load
    return COST["integration"]["gpu"] if load < BROWSER_LOAD else COST["integration"]["gpu"] // 2


def integration_jobs(kind="build", lane="integration"):
    """TURKU3D_JOBS for commands nested in the `lane` integration reservation."""
    return browser_workers() if kind == "browser" else COST[lane]["jobs"]


def lane(entry):
    """The slot class of an entry: its integration lane, else its kind."""
    return entry.get("lane", entry["kind"])


def fits(entries, kind):
    """Whether `kind` fits beside the running entries within every budgeted resource."""
    running = [e for e in entries if e["state"] == "running"]
    if sum(lane(e) == kind for e in running) >= LIMITS[kind]:
        return False
    return all(
        sum(COST[lane(e)].get(resource, 0) for e in running) + COST[kind].get(resource, 0) <= budget
        for resource, budget in BUDGET.items()
    )


def admissible(entries, entry):
    """Admit within budget; later admissions never starve a waiting integration, and
    agents' Chrome suites queue while the integrator's Chrome stage runs."""
    if not fits(entries, lane(entry)):
        return False
    if entry["kind"] == "browser" and any(
        lane(e) == "integration" and e.get("phase") == "chrome" for e in entries
    ):
        return False
    older = any(
        lane(e) == "integration" and e["state"] == "waiting" and e["since"] < entry["since"]
        for e in entries
    )
    if not older:
        return True
    # Admit only what still leaves room for the waiting integration.
    return lane(entry) != "integration" and fits(
        [*entries, {**entry, "state": "running"}], "integration"
    )


def root():
    return Path(os.environ.get("TURKU3D_SLOT_DIR", Path.home() / "turku3d/host-slots"))


def identity(pid):
    try:
        result = (
            subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
            .split()
        )
    except subprocess.CalledProcessError:
        return None
    return " ".join(result[:-1]) if result and not result[-1].startswith("Z") else None


@contextlib.contextmanager
def state(directory=None):
    directory = Path(directory) if directory is not None else root()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "state.json"
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            data = {"entries": []}
        data["entries"] = [
            entry for entry in data["entries"] if identity(entry["pid"]) == entry["identity"]
        ]
        try:
            yield data
        finally:
            tmp = directory / f"state.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(data, sort_keys=True))
            os.replace(tmp, path)
            fcntl.flock(lock, fcntl.LOCK_UN)


def same(a, b):
    """The same admission: entries gain fields (phase) while they are held."""
    return all(a[key] == b[key] for key in ("pid", "identity", "kind", "since"))


def owner():
    """(pid, identity) of the integration reservation this process runs under, or None."""
    pid, _, started = os.environ.get("TURKU3D_INTEGRATION_OWNER", "").partition(":")
    return (pid, started) if pid else None


@contextlib.contextmanager
def phase(name):
    """Mark the owning integration reservation as in `name` (e.g. its Chrome stage)."""
    held = owner()

    def mark(value):
        if not held:
            return
        with state() as data:
            for entry in data["entries"]:
                if (
                    entry["kind"] == "integration"
                    and (str(entry["pid"]), entry["identity"]) == held
                ):
                    entry["phase"] = value

    mark(name)
    try:
        yield
    finally:
        mark(None)


def snapshot(directory=None):
    with state(directory) as data:
        return list(data["entries"])


def waiting(sandbox):
    return next(
        (
            lane(entry)
            for entry in snapshot()
            if entry["sandbox"] == sandbox and entry["state"] == "waiting"
        ),
        None,
    )


@contextlib.contextmanager
def slot(kind):
    if kind not in LIMITS:
        raise ValueError(f"unknown slot class: {kind}")
    # Build and browser commands nested in a running integration use its reservation;
    # the slot yields that reservation's kind (its lane), else False.
    if held := owner():
        with state() as data:
            inherited = next(
                (
                    lane(entry)
                    for entry in data["entries"]
                    if entry["kind"] == "integration"
                    and entry["state"] == "running"
                    and (str(entry["pid"]), entry["identity"]) == held
                ),
                None,
            )
        if inherited:
            yield inherited
            return
    pid = os.getpid()
    started = identity(pid)
    if started is None:
        raise RuntimeError("cannot identify slot owner")
    entry = {
        "pid": pid,
        "identity": started,
        "kind": "integration" if kind in INTEGRATIONS else kind,
        "state": "waiting",
        "sandbox": os.environ.get("TURKU3D_SANDBOX", "main"),
        "since": time.time(),
    }
    if kind in INTEGRATIONS:
        entry["lane"] = kind
    try:
        announced = False
        while True:
            with state() as data:
                entries = data["entries"]
                stored = next((e for e in entries if same(e, entry)), None)
                if stored is None:
                    stored = dict(entry)
                    entries.append(stored)
                if admissible(entries, stored):
                    stored["state"] = "running"
                    entry["state"] = "running"
                    break
            if not announced:
                print(f"waiting for {kind} slot", file=sys.stderr, flush=True)
                announced = True
            time.sleep(0.2)
        if announced:
            print(f"acquired {kind} slot", file=sys.stderr, flush=True)
        record_wait(kind, entry["sandbox"], time.time() - entry["since"])
        yield False
    finally:
        with state() as data:
            data["entries"] = [e for e in data["entries"] if not same(e, entry)]


def record_wait(kind, name, seconds):
    """One O_APPEND line per admission; `stats` reads them back before agents are added."""
    row = {"time": time.time(), "kind": kind, "name": name, "wait_seconds": round(seconds, 3)}
    fd = os.open(root() / "waits.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode())
    finally:
        os.close(fd)


def wait_stats(hours=24, now=None, directory=None):
    """Admissions, median and p90 (nearest rank) wait seconds per slot kind in the window."""
    since = (time.time() if now is None else now) - hours * 3600
    waits = {}
    try:
        lines = (Path(directory or root()) / "waits.jsonl").read_text().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
            if row["time"] >= since:
                waits.setdefault(row["kind"], []).append(float(row["wait_seconds"]))
        except (ValueError, KeyError, TypeError):
            continue
    return {
        kind: {
            "admissions": len(values),
            "median": statistics.median(values),
            "p90": sorted(values)[math.ceil(0.9 * len(values)) - 1],
        }
        for kind, values in sorted(waits.items())
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=(*LIMITS, "stats"), help="slot class, or stats")
    parser.add_argument("--hours", type=float, default=24, help="stats window (default 24)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.kind == "stats":
        print(f"slot waits, last {args.hours:g} h: kind admissions median_s p90_s")
        for kind, row in wait_stats(args.hours).items():
            print(f"{kind:8} {row['admissions']:6} {row['median']:9.1f} {row['p90']:8.1f}")
        return 0
    if not command:
        parser.error("command required")
    with slot(args.kind) as inherited:
        env = os.environ.copy()
        if args.kind in INTEGRATIONS and not inherited:
            env["TURKU3D_JOBS"] = str(integration_jobs(lane=args.kind))
            env["TURKU3D_INTEGRATION_OWNER"] = f"{os.getpid()}:{identity(os.getpid())}"
        else:
            # Children of an integration keep its lane's budget, GPU-bounded for Chrome.
            cap = (
                integration_jobs(args.kind, inherited)
                if inherited
                else 1
                if args.kind == "browser"
                else 2
            )
            env["TURKU3D_JOBS"] = str(min(int(env.get("TURKU3D_JOBS", cap)), cap))
        return subprocess.call(command, env=env)


if __name__ == "__main__":
    sys.exit(main())
