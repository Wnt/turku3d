"""Warm worker pool: reuse, failure logs and replacement, recycling and stops (fake generator)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from workers import WarmWorkers  # noqa: E402

# Stands in for `run.sh python -m turku3d.coarse worker`: one result line per job line.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
for line in sys.stdin:
    job = json.loads(line)
    tile = job["tile"]
    if tile == "slow":
        time.sleep(30)
    if tile in ("bad", "water"):
        message = "turku3d.ground.NoCoverage: 0 ground key points" if tile == "water" else "ValueError: broken"
        print(json.dumps({"code": 1, "stderr": message}), flush=True)
        continue
    os.makedirs(job["out"], exist_ok=True)
    open(os.path.join(job["out"], "pid"), "w").write(str(os.getpid()))
    print(json.dumps({"code": 0, "stdout": json.dumps({"tile": tile})}), flush=True)
'''


class Farm(SimpleNamespace):
    def build_environment(self, revision, root):
        return Path(self.root), dict(os.environ)

    def free(self):
        return 100 * 2**30


class WarmWorkersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.runner = base / "runner"
        self.runner.write_text(FAKE)
        self.runner.chmod(0o755)
        self.farm = Farm(root=base, floor=0, queue_info={}, run_build=None, workers=4)
        self.out = base / "products" / "g1"

    def tearDown(self):
        self.tmp.cleanup()

    def pid(self, tile):
        return (self.out / tile / "pid").read_text()

    def test_tiles_reuse_one_worker_and_recycle_after_max_builds(self):
        pool = WarmWorkers(self.farm, self.runner, max_builds=2)
        rows = [pool("r1", t, self.out) for t in ("a", "b", "c")]
        self.assertEqual(self.pid("a"), self.pid("b"))
        self.assertNotEqual(self.pid("b"), self.pid("c"))
        self.assertEqual([r["worker_builds"] for r in rows], [1, 2, 1])
        pool.close()

    def test_failure_writes_the_tile_log_and_replaces_the_worker(self):
        pool = WarmWorkers(self.farm, self.runner)
        pool("r1", "a", self.out)
        with self.assertRaisesRegex(RuntimeError, "broken"):
            pool("r1", "bad", self.out)
        self.assertIn("broken", (self.farm.root / "logs" / "r1-bad.log").read_text())
        pool("r1", "b", self.out)
        self.assertNotEqual(self.pid("a"), self.pid("b"))
        pool.close()

    def test_an_expected_no_coverage_exclusion_keeps_the_worker(self):
        pool = WarmWorkers(self.farm, self.runner)
        pool("r1", "a", self.out)
        with self.assertRaisesRegex(RuntimeError, "NoCoverage"):
            pool("r1", "water", self.out)
        self.assertIn("NoCoverage", (self.farm.root / "logs" / "r1-water.log").read_text())
        pool("r1", "b", self.out)
        self.assertEqual(self.pid("a"), self.pid("b"))
        pool.close()

    def test_concurrent_builds_get_separate_workers_per_revision(self):
        pool = WarmWorkers(self.farm, self.runner)
        threads = [threading.Thread(target=pool, args=("r1", t, self.out)) for t in ("a", "b")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        pool("r2", "c", self.out)
        self.assertEqual(len({self.pid(t) for t in "abc"}), 3)
        pool.close()

    def test_idle_workers_never_exceed_the_admitted_builds(self):
        pool = WarmWorkers(self.farm, self.runner)
        threads = [threading.Thread(target=pool, args=("r1", t, self.out)) for t in ("a", "b", "c")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(pool.idle), 3)
        self.farm.workers = 1  # admission dropped (a dense front): extra idle workers exit
        pool("r1", "d", self.out)
        self.assertEqual(len(pool.idle), 1)
        pool.close()

    def test_a_worker_too_large_for_the_tile_reservation_is_not_reused(self):
        import workers
        sizes = {}
        with mock.patch.object(workers, "resident", lambda pid: sizes.get(pid, 0)):
            pool = WarmWorkers(self.farm, self.runner, reserve=lambda tile: 1.1 if tile.startswith("w") else 2.1)
            pool("r1", "a", self.out)
            sizes[int(self.pid("a"))] = int(1.5 * 2**30)  # retained after a suburb tile
            pool("r1", "b", self.out)  # suburb, 2.1 GiB reserved: 1.5 + 0.5 fits
            pool("r1", "w1", self.out)  # water, 1.1 GiB reserved: a fresh worker builds it
            self.assertEqual(self.pid("a"), self.pid("b"))
            self.assertNotEqual(self.pid("b"), self.pid("w1"))
            pool.close()

    def test_timeout_stops_the_worker(self):
        pool = WarmWorkers(self.farm, self.runner, timeout=0)
        with self.assertRaisesRegex(RuntimeError, "build timeout"):
            pool("r1", "slow", self.out)
        self.assertEqual(pool.idle, [])

    def test_a_recorded_one_shot_build_is_adopted_by_the_farm(self):
        self.farm.queue_info[("g1", "a")] = {"pid": 123}
        self.farm.run_build = lambda revision, tile, root: dict(returncode=0, adopted=True)
        self.assertTrue(WarmWorkers(self.farm, self.runner)("r1", "a", self.out)["adopted"])


if __name__ == "__main__":
    unittest.main()
