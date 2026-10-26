"""Host-wide, crash-recovering admission for builds and Chrome suites."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LIMITS = {"build": 3, "browser": 2, "land": 1}


# Chrome suites share one GPU. On the M1 Max the 20-test real-tile suite passed in
# 1.9 min with four workers; ten workers timed out on 9 tests.
LAND_BROWSER_JOBS = 4


def land_jobs(kind="land"):
    """An admitted land reservation excludes every other slot, so it owns the whole host."""
    cores = os.cpu_count() or 1
    return min(cores, LAND_BROWSER_JOBS) if kind == "browser" else cores


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


def snapshot(directory=None):
    with state(directory) as data:
        return list(data["entries"])


def waiting(sandbox):
    return next(
        (
            entry["kind"]
            for entry in snapshot()
            if entry["sandbox"] == sandbox and entry["state"] == "waiting"
        ),
        None,
    )


@contextlib.contextmanager
def slot(kind):
    if kind not in LIMITS:
        raise ValueError(f"unknown slot class: {kind}")
    owner = os.environ.get("TURKU3D_LAND_OWNER", "")
    # Children of a running landing reservation, including a nested landing
    # build such as a queue-run precheck, reuse its exclusive capacity.
    if owner:
        pid, _, started = owner.partition(":")
        with state() as data:
            inherited = any(
                entry["kind"] == "land"
                and entry["state"] == "running"
                and str(entry["pid"]) == pid
                and entry["identity"] == started
                for entry in data["entries"]
            )
        if inherited:
            yield True
            return
    pid = os.getpid()
    started = identity(pid)
    if started is None:
        raise RuntimeError("cannot identify slot owner")
    entry = {
        "pid": pid,
        "identity": started,
        "kind": kind,
        "state": "waiting",
        "sandbox": os.environ.get("TURKU3D_SANDBOX", "main"),
        "since": time.time(),
    }
    try:
        announced = False
        while True:
            with state() as data:
                entries = data["entries"]
                if entry not in entries:
                    entries.append(entry)
                running = [e for e in entries if e["state"] == "running"]
                older_land = any(
                    e["kind"] == "land"
                    and e["state"] == "waiting"
                    and (kind != "land" or e["since"] < entry["since"])
                    for e in entries
                )
                if kind == "land":
                    admitted = not running and not older_land
                else:
                    admitted = (
                        not any(e["kind"] == "land" for e in running)
                        and not older_land
                        and sum(e["kind"] == kind for e in running) < LIMITS[kind]
                    )
                if admitted:
                    entries[entries.index(entry)]["state"] = "running"
                    entry["state"] = "running"
                    break
            if not announced:
                print(f"waiting for {kind} slot", file=sys.stderr, flush=True)
                announced = True
            time.sleep(0.2)
        if announced:
            print(f"acquired {kind} slot", file=sys.stderr, flush=True)
        yield False
    finally:
        with state() as data:
            data["entries"] = [e for e in data["entries"] if e != entry]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=LIMITS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required")
    with slot(args.kind) as inherited:
        env = os.environ.copy()
        if args.kind == "land" and not inherited:
            env["TURKU3D_JOBS"] = str(land_jobs())
            env["TURKU3D_LAND_OWNER"] = f"{os.getpid()}:{identity(os.getpid())}"
        else:
            # Children of a land reservation keep its whole-host budget, GPU-bounded for Chrome.
            cap = land_jobs(args.kind) if inherited else 1 if args.kind == "browser" else 2
            env["TURKU3D_JOBS"] = str(min(int(env.get("TURKU3D_JOBS", cap)), cap))
        return subprocess.call(command, env=env)


if __name__ == "__main__":
    sys.exit(main())
