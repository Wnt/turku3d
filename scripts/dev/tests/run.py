#!/usr/bin/env python3
"""Run independent synthetic-repository tests concurrently, with normal unittest results.

A module that sets `SERIAL = True` (it patches os.environ or module globals) runs its cases one
after another in a process of its own: threads share one environment, so a patch one case
restores would point another case at the real registry and sandboxes.
"""

import io
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resources import jobs  # noqa: E402


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def serial(case):
    return getattr(sys.modules[type(case).__module__], "SERIAL", False)


def run_serial(module, ids):
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", *ids],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
    )
    return module, proc.returncode == 0, time.monotonic() - start, proc.stdout + proc.stderr


def run(case):
    output = io.StringIO()
    start = time.monotonic()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(unittest.TestSuite([case]))
    return case.id(), result.wasSuccessful(), time.monotonic() - start, output.getvalue()


if __name__ == "__main__":
    start = time.monotonic()
    tests = [
        case
        for pattern in (sys.argv[1:] or ["test_*.py"])
        for case in cases(unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern))
    ]
    modules = {}
    for case in tests:
        if serial(case):
            modules.setdefault(type(case).__module__, []).append(case.id())
    failed = 0
    with ThreadPoolExecutor(max_workers=jobs()) as pool:
        futures = [pool.submit(run_serial, *item) for item in modules.items()]
        futures += [pool.submit(run, case) for case in tests if not serial(case)]
        for future in as_completed(futures):
            name, ok, elapsed, output = future.result()
            print(f"{'PASS' if ok else 'FAIL'} {name} ({elapsed:.2f}s)", flush=True)
            if not ok:
                failed += 1
                print(output, flush=True)
    print(f"{len(tests) - failed} passed, {failed} failed; total {time.monotonic() - start:.2f}s")
    sys.exit(bool(failed))
