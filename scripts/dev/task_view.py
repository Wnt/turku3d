"""Bounded, read-only snapshots for task listings and the live monitor."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from pathlib import Path

import host_slots
import sandbox as s

TAIL_BYTES = 256 * 1024
ACTIVE = ("starting", "running")


def json_object(path):
    with contextlib.suppress(OSError, ValueError):
        value = json.loads(Path(path).read_text())
        if isinstance(value, dict):
            return value
    return {}


def task_rows(root):
    paths = {
        p.parent for pattern in ("*/meta.json", "*/inbox.jsonl") for p in Path(root).glob(pattern)
    }
    return [(path, json_object(path / "meta.json")) for path in sorted(paths)]


def process_snapshot():
    """One ps per locale, independent of the number of historical tasks."""
    snapshots = []
    locales = dict.fromkeys(("C", os.environ.get("LC_TIME") or os.environ.get("LANG", "C")))
    for locale in locales:
        rows = {}
        with contextlib.suppress(OSError, TimeoutError):
            try:
                output = s.run(
                    "ps",
                    "-axo",
                    "pid=,stat=,lstart=",
                    check=False,
                    env={**os.environ, "LC_ALL": locale},
                    timeout=2,
                ).stdout
            except subprocess.SubprocessError:
                output = ""
            for line in output.splitlines():
                fields = line.split()
                if len(fields) >= 7 and fields[0].isdigit() and not fields[1].startswith("Z"):
                    rows[int(fields[0])] = " ".join(fields[2:])
        snapshots.append(rows)
    return snapshots


def is_live(meta, processes):
    return bool(meta.get("pid") and meta.get("identity")) and any(
        rows.get(meta["pid"]) == meta["identity"] for rows in processes
    )


def waiting_slots(processes):
    """Read the scheduler's atomic state file without taking its lock or pruning it."""
    entries = json_object(host_slots.root() / "state.json").get("entries", [])
    return {
        entry["sandbox"]: host_slots.lane(entry)
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("sandbox")
        and entry.get("state") == "waiting"
        and is_live(entry, processes)
    }


def phase(meta, live, waiting=None):
    # A live agent stays "running" after merging a slice into main mid-turn; "landed" only describes a finished one.
    state = meta.get("state", "inbox only")
    if state in ACTIVE and live:
        return f"waiting for {waiting} slot" if waiting else state
    if meta.get("landed"):
        return "landed"
    return "interrupted" if state in ACTIVE else state


def modified(path):
    # A resume can rename a log between directory discovery and sorting.
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def last_event(path):
    with contextlib.suppress(OSError):
        with (path / "events.jsonl").open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - TAIL_BYTES)
            stream.seek(start)
            data = stream.read(TAIL_BYTES)
        lines = data.split(b"\n")
        if start:
            lines.pop(0)
        # Ignore a torn final record, just as the monitor does.
        for line in reversed(lines[:-1]):
            with contextlib.suppress(ValueError):
                event = json.loads(line)
                if isinstance(event, dict):
                    return event
    return {}
