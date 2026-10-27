"""Small process-level tests of host admission and crash recovery."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_slots  # noqa: E402


class HostSlotsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # A tasks root of its own: the sandbox's real task budget is never debited.
        self.env = {
            **os.environ,
            "TURKU3D_SLOT_DIR": self.directory.name,
            "TURKU3D_TASKS_ROOT": self.directory.name,
        }
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=3)

    def snapshot(self):
        return host_slots.snapshot(self.directory.name)

    def start(self, kind, seconds=20):
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(host_slots.__file__)),
                kind,
                "--",
                sys.executable,
                "-c",
                f"import time; time.sleep({seconds})",
            ],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.children.append(child)
        return child

    def until(self, predicate):
        end = time.monotonic() + 6
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.03)
        self.fail(f"slot state did not converge: {self.snapshot()}")

    def kinds(self, child):
        return [(host_slots.lane(e), e["state"]) for e in self.snapshot() if e["pid"] == child.pid]

    def test_integration_reservation_leaves_build_budget(self):
        integration = self.start("integration")
        self.until(lambda: self.kinds(integration) == [("integration", "running")])
        builds = [self.start("build") for _ in range(3)]
        self.until(lambda: len(self.snapshot()) == 4)
        # 6 integration jobs + 2 builds of 2 jobs fill the 10-job budget; the third waits.
        self.until(lambda: sum(e["state"] == "running" for e in self.snapshot()) == 3)
        browser = self.start("browser")
        self.until(lambda: self.kinds(browser) == [("browser", "running")])
        waiting = [c for c in builds if self.kinds(c) == [("build", "waiting")]]
        self.assertEqual(len(waiting), 1)
        os.killpg(integration.pid, signal.SIGTERM)
        integration.wait(timeout=3)
        self.until(lambda: self.kinds(waiting[0]) == [("build", "running")])

    def test_waiting_integration_is_not_starved(self):
        builds = [self.start("build") for _ in range(3)]
        self.until(lambda: sum(e["state"] == "running" for e in self.snapshot()) == 3)
        integration = self.start("integration")
        self.until(lambda: self.kinds(integration) == [("integration", "waiting")])
        os.killpg(builds[0].pid, signal.SIGTERM)
        builds[0].wait(timeout=3)
        extra = self.start("build")
        self.until(lambda: self.kinds(integration) == [("integration", "running")])
        self.until(lambda: self.kinds(extra) == [("build", "waiting")])
        os.killpg(integration.pid, signal.SIGTERM)
        integration.wait(timeout=3)
        self.until(lambda: self.kinds(extra) == [("build", "running")])

    def test_dead_owner_reclaimed_and_browser_cap(self):
        # One agent Chrome suite per host at a time.
        browsers = [self.start("browser") for _ in range(2)]
        self.until(lambda: len(self.snapshot()) == 2)
        self.assertEqual(sum(e["state"] == "running" for e in self.snapshot()), 1)
        owner = next(e for e in self.snapshot() if e["state"] == "running")
        child = next(c for c in browsers if c.pid == owner["pid"])
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        self.until(lambda: [e["state"] for e in self.snapshot()] == ["running"])

    def test_child_worker_budget(self):
        for kind, expected in (("build", "2"), ("browser", "1")):
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(host_slots.__file__)),
                    kind,
                    "--",
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['TURKU3D_JOBS'])",
                ],
                env={**self.env, "TURKU3D_JOBS": "8"},
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), expected)

    def test_integration_children_use_its_budget(self):
        for kind in ("build", "browser"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.nested(kind, "import os; print(os.environ['TURKU3D_JOBS'])"),
                    str(host_slots.integration_jobs(kind)),
                )

    def test_integration_children_reuse_reservation(self):
        # Make targets nested in the integrator's live build do not queue behind it.
        self.assertEqual(self.nested("build"), "42")

    def nested(self, kind, inner="print(42)"):
        script = (
            "import os, subprocess, sys; "
            f"subprocess.run([sys.executable, sys.argv[1], '{kind}', '--', "
            "sys.executable, '-c', sys.argv[2]], check=True)"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path(host_slots.__file__)),
                "integration",
                "--",
                sys.executable,
                "-c",
                script,
                str(Path(host_slots.__file__)),
                inner,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()

    def test_integrator_chrome_workers_follow_the_load(self):
        self.assertEqual((host_slots.browser_workers(59.9), host_slots.browser_workers(60)), (4, 2))
        self.assertEqual(host_slots.integration_jobs(lane="integration-data"), 4)

    def test_agents_chrome_queues_while_the_integrator_runs_chrome(self):
        # The integration child marks its Chrome phase for 1.5 s, then works without it.
        script = (
            "import sys, time; sys.path.insert(0, sys.argv[1]); import host_slots; "
            "time.sleep(0.3)\n"
            "with host_slots.phase('chrome'): time.sleep(1.5)\n"
            "time.sleep(20)"
        )
        integration = subprocess.Popen(
            [
                sys.executable,
                str(Path(host_slots.__file__)),
                "integration",
                "--",
                sys.executable,
                "-c",
                script,
                str(Path(host_slots.__file__).parent),
            ],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.children.append(integration)

        def phase():
            return [e.get("phase") for e in self.snapshot() if e["kind"] == "integration"]

        self.until(lambda: phase() == ["chrome"])
        browser = self.start("browser")
        self.until(lambda: self.kinds(browser) == [("browser", "waiting")])
        self.until(lambda: phase() == [None])
        self.until(lambda: self.kinds(browser) == [("browser", "running")])
        # The marked entry is still released as the same admission.
        os.killpg(integration.pid, signal.SIGTERM)
        integration.wait(timeout=3)
        self.until(lambda: [host_slots.lane(e) for e in self.snapshot()] == ["browser"])

    def test_code_and_data_lanes_hold_their_own_slots(self):
        code = self.start("integration")
        data = self.start("integration-data")
        self.until(
            lambda: (
                self.kinds(code) + self.kinds(data)
                == [
                    ("integration", "running"),
                    ("integration-data", "running"),
                ]
            )
        )
        # Stored as kind `integration` with a lane: tooling that predates the data lane counts it.
        self.assertEqual(
            {(e["kind"], e.get("lane")) for e in self.snapshot()},
            {
                ("integration", "integration"),
                ("integration", "integration-data"),
            },
        )
        # 6 + 4 jobs fill the budget: a build waits, a second data lane waits for its slot.
        build = self.start("build")
        again = self.start("integration-data")
        self.until(lambda: self.kinds(build) == [("build", "waiting")])
        self.until(lambda: self.kinds(again) == [("integration-data", "waiting")])
        os.killpg(data.pid, signal.SIGTERM)
        data.wait(timeout=3)
        # Whichever is admitted first takes the freed jobs; both run once the code lane ends.
        os.killpg(code.pid, signal.SIGTERM)
        code.wait(timeout=3)
        self.until(
            lambda: (
                self.kinds(build) + self.kinds(again)
                == [
                    ("build", "running"),
                    ("integration-data", "running"),
                ]
            )
        )

    def test_data_lane_children_use_its_budget(self):
        script = (
            "import os, subprocess, sys; "
            "subprocess.run([sys.executable, sys.argv[1], 'build', '--', "
            "sys.executable, '-c', \"import os; print(os.environ['TURKU3D_JOBS'])\"], check=True)"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path(host_slots.__file__)),
                "integration-data",
                "--",
                sys.executable,
                "-c",
                script,
                str(Path(host_slots.__file__)),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "4")

    def test_admissions_log_waits_and_stats_report_median_and_p90(self):
        first = self.start("integration")
        self.until(lambda: [e["state"] for e in self.snapshot()] == ["running"])
        queued = self.start("integration", seconds=0)
        self.until(lambda: len(self.snapshot()) == 2)
        os.killpg(first.pid, signal.SIGTERM)
        queued.wait(timeout=6)
        path = Path(self.directory.name) / "waits.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([row["kind"] for row in rows], ["integration", "integration"])
        self.assertEqual({row["name"] for row in rows}, {self.env.get("TURKU3D_SANDBOX", "main")})
        self.assertGreater(rows[1]["wait_seconds"], 0)
        now = time.time()
        old = {"time": now - 25 * 3600, "kind": "build", "name": "x", "wait_seconds": 999}
        with path.open("a") as log:
            for row in [old] + [
                {"time": now, "kind": "browser", "name": "x", "wait_seconds": float(s)}
                for s in range(1, 11)
            ]:
                log.write(json.dumps(row) + "\n")
            log.write("not json\n")
        stats = host_slots.wait_stats(24, now, self.directory.name)
        self.assertEqual(stats["browser"], {"admissions": 10, "median": 5.5, "p90": 9.0})
        self.assertEqual(stats["integration"]["admissions"], 2)
        self.assertNotIn("build", stats)
        done = subprocess.run(
            [sys.executable, str(Path(host_slots.__file__)), "stats"],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertRegex(done.stdout, r"browser +10 +5\.5 +9\.0")


if __name__ == "__main__":
    unittest.main()
