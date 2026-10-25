#!/usr/bin/env python3
"""Run independent synthetic-repository tests concurrently, with normal unittest results."""

import io
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
    failed = 0
    with ThreadPoolExecutor(max_workers=jobs()) as pool:
        for future in as_completed([pool.submit(run, case) for case in tests]):
            name, ok, elapsed, output = future.result()
            print(f"{'PASS' if ok else 'FAIL'} {name} ({elapsed:.2f}s)", flush=True)
            if not ok:
                failed += 1
                print(output, flush=True)
    print(f"{len(tests) - failed} passed, {failed} failed; total {time.monotonic() - start:.2f}s")
    sys.exit(bool(failed))
