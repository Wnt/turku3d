"""One retention sweep at a time, independent of demand expiry and scheduler telemetry."""

import json
import sys
import time

from .retention import prune


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
            result = prune(builder.root)
        except (OSError, ValueError) as error:
            print(f"coarse retention: {error}", file=sys.stderr, flush=True)
            result = {"error_kind": type(error).__name__}
        print(json.dumps({"retention": True, "at": time.time(),
                          "seconds": round(time.perf_counter() - started, 6),
                          "result": result}), flush=True)
        next_prune = time.monotonic() + 60
