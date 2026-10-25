"""Codex weekly-quota check (no model call, costs nothing).

Port of osgallery-dev:~/leikkipuistodata/codex-usage.py (read-only source, not modified): it starts
`codex app-server` and asks `account/rateLimits/read` over JSON-RPC on stdio.

`used_percent()` returns the integer weekly usedPercent (None if unknown). Rollout `token_count`
events carry a float `used_percent` too; see rollout.py. Override with $TURKUTEX_USAGE_CMD (a command
that prints a number) for tests or other machines.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time


def _rpc(methods: list[str], codex_bin: str = "codex", timeout: float = 25.0) -> dict:
    p = subprocess.Popen([codex_bin, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        def send(i, method, params):
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
            p.stdin.flush()
        send(0, "initialize", {"clientInfo": {"name": "turkutex", "title": "turkutex", "version": "0.1"}})
        for i, m in enumerate(methods, 1):
            send(i, m, {})
        out, t0 = {}, time.time()
        while len(out) < len(methods) + 1 and time.time() - t0 < timeout:
            line = p.stdout.readline()
            if not line:
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
            return int(float(subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                                            timeout=60).stdout.strip()))
        except (ValueError, subprocess.TimeoutExpired):
            return None
    try:
        lim = rate_limits(codex_bin) or {}
    except (FileNotFoundError, OSError):
        return None
    w = lim.get("primary") or lim.get("secondary") or {}
    v = w.get("usedPercent")
    return None if v is None else int(v)


def snapshot(codex_bin: str = "codex") -> dict:
    """Human/log-friendly snapshot: {used_percent, resets_at, plan, t}."""
    try:
        lim = rate_limits(codex_bin) or {}
    except (FileNotFoundError, OSError):
        lim = {}
    w = lim.get("primary") or lim.get("secondary") or {}
    return {"used_percent": w.get("usedPercent"), "resets_at": w.get("resetsAt"),
            "window_mins": w.get("windowDurationMins"), "plan": lim.get("planType"),
            "limit_reached": lim.get("rateLimitReachedType"), "t": time.time()}
