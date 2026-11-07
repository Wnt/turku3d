"""One retention sweep at a time, independent of demand expiry and scheduler telemetry.

A sweep reads every tile's manifests and lists every payload: 16-147 s on a loaded host (city.lab Amsterdam,
2026-11-07). In the service's own process that work held the GIL from the request handlers for most of each minute,
so it runs in a child process at the lowest CPU priority, every RETENTION_SECONDS. retention.prune takes the same
file locks as publication, so a separate process is as safe as a thread.
"""

import json
import subprocess
import sys
import time

RETENTION_SECONDS = 300


def sweep(root):
    """One prune of `root` in a niced child process; its JSON result."""
    proc = subprocess.run(
        ["nice", "-n", "19", sys.executable, "-m", "turku3d.coarse.retention", "--root", str(root)],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["retention failed"]
        raise OSError(tail[0][:300])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def maintain_retention(builder):
    next_prune = 0
    while True:
        with builder.lock:
            if builder.stopped:
                return
            if builder.draining or time.monotonic() < next_prune:
                builder.lock.wait(timeout=1)
                continue
        started = time.perf_counter()
        try:
            result = sweep(builder.root)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"coarse retention: {error}", file=sys.stderr, flush=True)
            result = {"error_kind": type(error).__name__}
        print(json.dumps({"retention": True, "at": time.time(),
                          "seconds": round(time.perf_counter() - started, 6),
                          "result": result}), flush=True)
        next_prune = time.monotonic() + RETENTION_SECONDS
