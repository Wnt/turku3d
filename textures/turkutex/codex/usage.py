"""Codex weekly-quota check (no model call, costs nothing).

Port of the historical codex-usage.py helper (read-only source, not modified): it starts
`codex app-server` and asks `account/rateLimits/read` over JSON-RPC on stdio.

`used_percent()` returns the integer weekly usedPercent (None if unknown). Rollout `token_count`
events carry a float `used_percent` too; see rollout.py. Override with $TURKUTEX_USAGE_CMD (a command
that prints a number) for tests or other machines.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time


def _rpc(methods: list[str], codex_bin: str = "codex", timeout: float = 25.0) -> dict:
    p = subprocess.Popen(
        [codex_bin, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:

        def send(i, method, params):
            p.stdin.write(
                json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n"
            )
            p.stdin.flush()

        send(
            0,
            "initialize",
            {"clientInfo": {"name": "turkutex", "title": "turkutex", "version": "0.1"}},
        )
        for i, m in enumerate(methods, 1):
            send(i, m, {})
        # Read stdout in a thread: a bare readline() blocks forever when app-server hangs without output,
        # which used to freeze the runner (check_quota holds its lock) instead of failing closed.
        lines: queue.Queue = queue.Queue()
        threading.Thread(
            target=lambda: [lines.put(x) for x in iter(p.stdout.readline, "")] + [lines.put(None)],
            daemon=True,
        ).start()
        out, deadline = {}, time.time() + timeout
        while len(out) < len(methods) + 1:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                out[msg["id"]] = msg
        return {m: out.get(i) for i, m in enumerate(methods, 1)}
    finally:
        p.kill()
        p.wait()


def rate_limits(codex_bin: str = "codex") -> dict | None:
    res = _rpc(["account/rateLimits/read"], codex_bin)
    r = (res.get("account/rateLimits/read") or {}).get("result")
    return r.get("rateLimits") if r else None


def used_percent(codex_bin: str = "codex") -> int | None:
    cmd = os.environ.get("TURKUTEX_USAGE_CMD")
    if cmd:
        try:
            return int(
                float(
                    subprocess.run(
                        shlex.split(cmd), capture_output=True, text=True, timeout=60
                    ).stdout.strip()
                )
            )
        except (
            ValueError,
            OSError,
            subprocess.TimeoutExpired,
        ):  # missing/broken command -> unknown (fail closed)
            return None
    try:
        lim = rate_limits(codex_bin) or {}
    except (FileNotFoundError, OSError):
        return None
    # Use the highest window: with two windows `primary` is the short (5 h) one and `secondary` the weekly
    # one, so taking `primary` alone ignored a weekly 100 %. Today's Pro plan reports only a weekly primary.
    vals = [w.get("usedPercent") for w in (lim.get("primary"), lim.get("secondary")) if w]
    vals = [v for v in vals if v is not None]
    return int(max(vals)) if vals else None


def snapshot(codex_bin: str = "codex") -> dict:
    """Human/log-friendly snapshot: {used_percent, resets_at, plan, t}."""
    try:
        lim = rate_limits(codex_bin) or {}
    except (FileNotFoundError, OSError):
        lim = {}
    windows = [w for w in (lim.get("primary"), lim.get("secondary")) if w]
    w = max(windows, key=lambda w: w.get("windowDurationMins") or 0) if windows else {}
    return {
        "used_percent": w.get("usedPercent"),
        "resets_at": w.get("resetsAt"),
        "window_mins": w.get("windowDurationMins"),
        "plan": lim.get("planType"),
        "limit_reached": lim.get("rateLimitReachedType"),
        "t": time.time(),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--percent":
        os.environ.pop("TURKUTEX_USAGE_CMD", None)
        value = used_percent()
        print("" if value is None else value)
    else:
        print(json.dumps(snapshot()))
