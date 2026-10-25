"""Append-only cross-process Codex ledger. Null means unknown, never zero cost."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def ledger_path():
    return Path(
        os.environ.get("TURKU3D_CODEX_LEDGER", "~/turku3d/ledger/codex-calls.jsonl")
    ).expanduser()


def rows(path):
    if Path(path).exists():
        with Path(path).open() as stream:
            for line in stream:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def append(record):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        for line in stream:
            try:
                if json.loads(line).get("call_id") == record["call_id"]:
                    return False
            except ValueError:
                continue
        stream.seek(0)
        existing = stream.read()
        stream.seek(0, 2)
        if existing and not existing.endswith("\n"):
            stream.write("\n")
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


def record_call(
    *,
    source,
    model,
    effort,
    job_type,
    events="",
    seconds=None,
    task=None,
    job_id=None,
    units=None,
    started=None,
    call_id=None,
    fallback_id=None,
    ok=None,
    tokens=None,
):
    """Record every completed turn, or one unknown-usage failed attempt.

    Event usage is authoritative; rollout totals may span resumed sessions. A thread +
    turn index is stable across copied event files and repeated backfill. Failures
    without usage retain null tokens and are not silently counted as free calls.
    """
    thread, turn, parsed = None, 0, []
    if isinstance(events, bytes):
        events = events.decode(errors="replace")
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started":
            thread = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            parsed.append((thread, turn, event.get("usage")))
            turn += 1
    if not parsed:
        parsed = [(thread, 0, tokens)]
    count = 0
    for tid, index, usage in parsed:
        # A single-call runner starts a new thread. The task streamer supplies a
        # persistent event-index identity to distinguish resumed turns.
        identity = call_id or (f"{tid}:{index}" if tid else fallback_id or str(uuid.uuid4()))
        if len(parsed) > 1 and call_id:
            identity += f":{index}"
        usage = usage or {}
        stamp = started if started is not None else time.time()
        count += append(
            {
                "version": 1,
                "call_id": identity,
                "thread_id": tid,
                "source": str(source),
                "model": model,
                "effort": effort,
                "job_type": job_type,
                "task": task or os.environ.get("TURKU3D_SANDBOX", "unknown"),
                "job_id": job_id,
                "units": units or {},
                "started_at": datetime.fromtimestamp(stamp, UTC).isoformat(),
                "wall_seconds": seconds if len(parsed) == 1 else None,
                "ok": ok,
                "tokens": {field: usage.get(field) for field in FIELDS},
                "usage_known": bool(usage),
            }
        )
    return count


def stable_id(path, index):
    return hashlib.sha256(f"{Path(path).resolve()}:{index}".encode()).hexdigest()
