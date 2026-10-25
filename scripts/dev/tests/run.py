#!/usr/bin/env python3
"""Run independent synthetic-repository tests concurrently, with normal unittest results."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
from pathlib import Path
import sys
import time
import unittest


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


if __name__ == '__main__':
    start = time.monotonic()
    tests = list(cases(unittest.defaultTestLoader.discover(str(Path(__file__).parent))))
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(run, case) for case in tests]):
            name, ok, elapsed, output = future.result()
            print(f'{"PASS" if ok else "FAIL"} {name} ({elapsed:.2f}s)', flush=True)
            if not ok:
                failed += 1
                print(output, flush=True)
    print(f'{len(tests) - failed} passed, {failed} failed; total {time.monotonic() - start:.2f}s')
    sys.exit(bool(failed))
