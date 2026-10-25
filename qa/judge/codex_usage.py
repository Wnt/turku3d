#!/usr/bin/env python3
# Vendored copy of the historical codex-usage.py helper (read-only source; --percent changed to report
# the highest window so a weekly 100 % is not hidden behind a lower 5 h primary).
# Used by judge.py as the quota guard. No model call; asks `codex app-server` over JSON-RPC.
"""Show Codex (ChatGPT account) usage: the rate-limit windows (weekly / 5 h) as used-percent with
reset times, reset credits, and recent daily token totals. Asks the local `codex app-server` over
JSON-RPC (methods account/rateLimits/read and account/usage/read); no model call is made, so
checking costs nothing.

Usage: codex-usage [--json] [--percent] [--days N]
  --json    print the raw RPC results
  --percent print only the weekly used percent (scripts: stop when it reaches a cap)
  --days N  show the last N daily token buckets (default 7)
Exit codes: 0 ok, 1 could not talk to codex, 2 codex not logged in / no data.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime


def rpc(methods, timeout=25):
    p = subprocess.Popen(
        ["codex", "app-server"],
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
            {"clientInfo": {"name": "codex-usage", "title": "codex-usage", "version": "0.1"}},
        )
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


def fmt_reset(ts):
    if not ts:
        return ""
    dt = datetime.fromtimestamp(ts, UTC).astimezone()
    left = ts - time.time()
    d, h, m = int(left // 86400), int(left % 86400 // 3600), int(left % 3600 // 60)
    rel = (f"{d}d {h}h" if d else f"{h}h {m}min") if left > 0 else "now"
    return f"resets {dt.strftime('%a %Y-%m-%d %H:%M')} (in {rel})"


def window_name(mins):
    return {300: "5-hour", 10080: "weekly", 1440: "daily"}.get(mins, f"{mins}-min")


def bar(pct, width=20):
    n = max(0, min(width, round(width * pct / 100)))
    return "[" + "#" * n + "." * (width - n) + "]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--percent", action="store_true", help="print only the weekly used percent (for scripts)"
    )
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    try:
        res = rpc(["account/rateLimits/read", "account/usage/read"])
    except FileNotFoundError:
        print("codex CLI not found on PATH", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    if a.percent:
        lim = ((res.get("account/rateLimits/read") or {}).get("result") or {}).get(
            "rateLimits"
        ) or {}
        # highest window (primary is the 5 h one when a plan has two; secondary is then the weekly one)
        vals = [w.get("usedPercent") for w in (lim.get("primary"), lim.get("secondary")) if w]
        vals = [v for v in vals if v is not None]
        if not vals:
            print("unknown")
            return 2
        print(int(max(vals)))
        return 0
    rl = (res.get("account/rateLimits/read") or {}).get("result")
    if not rl:
        err = (res.get("account/rateLimits/read") or {}).get("error")
        print(
            "no rate-limit data from codex"
            + (f": {err.get('message')}" if err else " (not logged in?)"),
            file=sys.stderr,
        )
        return 2
    lim = rl.get("rateLimits") or {}
    print(
        f"Codex plan: {lim.get('planType') or '?'}"
        + (
            "   [rate limit reached: " + str(lim["rateLimitReachedType"]) + "]"
            if lim.get("rateLimitReachedType")
            else ""
        )
        + ("   [spend control reached]" if lim.get("spendControlReached") else "")
    )
    for key in ("primary", "secondary"):
        w = lim.get(key)
        if w:
            pct = w.get("usedPercent") or 0
            print(
                f"  {window_name(w.get('windowDurationMins')):8} {bar(pct)} {pct:3d}% used   {fmt_reset(w.get('resetsAt'))}"
            )
    if lim.get("secondary") is None:
        print("  (this plan has a single window)")
    cred = lim.get("credits") or {}
    rc = rl.get("rateLimitResetCredits") or {}
    print(
        f"  credits: {'unlimited' if cred.get('unlimited') else cred.get('balance', '0')}; rate-limit reset credits available: {rc.get('availableCount', 0)}"
    )
    us = (res.get("account/usage/read") or {}).get("result") or {}
    buckets = sorted(us.get("dailyUsageBuckets") or [], key=lambda b: b["startDate"])[-a.days :]
    if buckets:
        print(f"  tokens per day (last {len(buckets)} active days):")
        for b in buckets:
            print(f"    {b['startDate']}  {b['tokens'] / 1e6:8.1f} M")
    s = us.get("summary") or {}
    if s:
        print(
            f"  lifetime {s.get('lifetimeTokens', 0) / 1e9:.2f} G tokens, peak day {s.get('peakDailyTokens', 0) / 1e6:.0f} M, streak {s.get('currentStreakDays')} days"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
