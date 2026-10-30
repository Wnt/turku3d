"""Small persistent rolling build telemetry, shared across producer slot switches."""

import json
import math
import os
import re
import time
from collections import Counter

WINDOW = 15 * 60


def error_kind(detail):
    # Publish exception types only, never messages (which may contain private paths).
    tail = detail.strip().splitlines()[-1:] or [""]
    if re.search(r"(?:^|\.)NoCoverage:", tail[0]):
        return "NoCoverage"
    match = re.match(r"([A-Za-z_][\w.]*(?:Error|Exception|Failure))\b", tail[0])
    return match[1] if match else "BuildFailure"


class BuildHealth:
    def __init__(self, root, clock=time.time):
        self.path = root / ".build-health.jsonl"
        self.clock = clock

    def record(self, pass_kind, outcome, seconds, detail=""):
        if outcome not in ("built", "FAILED", "cancelled", "unavailable"):
            return
        kind = error_kind(detail) if outcome == "FAILED" else None
        if kind == "NoCoverage" or outcome == "unavailable":
            outcome, kind = "unavailable", None
        row = dict(
            at=self.clock(),
            **{"pass": pass_kind},
            outcome="failed" if outcome == "FAILED" else outcome,
            seconds=seconds,
            error_kind=kind,
        )
        try:
            # One append syscall: concurrent slots cannot interleave JSON records.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
            try:
                os.write(fd, (json.dumps(row) + "\n").encode())
            finally:
                os.close(fd)
        except OSError:
            pass  # Observability must never fail serving.

    def snapshot(self, players_online=False):
        rows = []
        try:
            with self.path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 2_000_000))
                lines = stream.read().splitlines()
            for line in lines:
                try:
                    row = json.loads(line)
                    if self.clock() - WINDOW <= row["at"] <= self.clock():
                        rows.append(row)
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            pass
        passes = {}
        for kind in ("first", "full"):
            unavailable = [
                r
                for r in rows
                if r["pass"] == kind
                and (r["outcome"] == "unavailable" or r.get("error_kind") == "NoCoverage")
            ]
            selected = [
                r
                for r in rows
                if r["pass"] == kind
                and r["outcome"] != "unavailable"
                and r.get("error_kind") != "NoCoverage"
            ]
            counts = Counter(r["outcome"] for r in selected)
            durations = sorted(r["seconds"] for r in selected if r["outcome"] == "built")
            errors = Counter(r["error_kind"] for r in selected if r.get("error_kind"))
            passes[kind] = dict(
                **{k: counts[k] for k in ("built", "failed", "cancelled")},
                unavailable=len(unavailable),
                p50_seconds=round(durations[(len(durations) - 1) // 2], 3) if durations else None,
                p90_seconds=round(durations[math.ceil(len(durations) * 0.9) - 1], 3)
                if durations
                else None,
                # Cancellations follow player demand (preemption), not build health: only failures count against success.
                success_rate=counts["built"] / (counts["built"] + counts["failed"])
                if counts["built"] + counts["failed"]
                else None,
                last_error_kind=next(
                    (r["error_kind"] for r in reversed(selected) if r.get("error_kind")), None
                ),
                error_kinds=dict(errors),
            )
        return dict(window_seconds=WINDOW, players_online=players_online, passes=passes)
