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

    def test_publishing_and_data_never_wait_behind_bulk_builds_and_short_checks(self):
        builds = [self.start("build") for _ in range(2)]
        for _ in range(2):
            self.start("check")
        self.until(lambda: sum(e["state"] == "running" for e in self.snapshot()) == 4)
        code, data = self.start("integration"), self.start("integration-data")
        self.until(lambda: self.kinds(code) == [("integration", "running")])
        self.until(lambda: self.kinds(data) == [("integration-data", "running")])
        self.assertEqual(len(self.snapshot()), 6)
        browser = self.start("browser")
        self.until(lambda: self.kinds(browser) == [("browser", "running")])
        extra = self.start("build")
        self.until(lambda: self.kinds(extra) == [("build", "waiting")])
        os.killpg(builds[0].pid, signal.SIGTERM)
        builds[0].wait(timeout=3)
        self.until(lambda: self.kinds(extra) == [("build", "running")])

    def test_all_lane_combinations_fit_the_ten_core_budget(self):
        # Exhaust the actual supported counts, including legacy entries with no lane.
        running = [{"kind": "build", "state": "running", "since": i} for i in range(2)]
        running += [
            {"kind": "build", "purpose": "check", "state": "running", "since": i} for i in range(2)
        ]
        code = {"kind": "integration", "state": "waiting", "since": 4}
        data = {**code, "lane": "integration-data"}
        self.assertTrue(host_slots.admissible(running, code))
        self.assertTrue(host_slots.admissible(running, data))
        running += [{**code, "state": "running"}]
        self.assertTrue(host_slots.admissible(running, data))
        self.assertFalse(host_slots.admissible(running, code))
        self.assertEqual(sum(host_slots.COST[host_slots.lane(e)]["jobs"] for e in running), 8)

    def test_build_waiters_are_fifo_when_only_one_fits(self):
        integration = {"kind": "integration", "state": "running", "since": 0}
        running = [{"kind": "build", "state": "running", "since": i} for i in (1,)]
        older = {"kind": "build", "state": "waiting", "since": 2}
        newer = {"kind": "build", "state": "waiting", "since": 3}
        entries = [integration, *running, older, newer]
        self.assertTrue(host_slots.admissible(entries, older))
        self.assertFalse(host_slots.admissible(entries, newer))
        self.assertTrue(host_slots.admissible([integration, *running, newer], newer))
        browser = {"kind": "browser", "state": "waiting", "since": 4}
        self.assertTrue(host_slots.admissible(entries, browser))

    def test_checks_queue_fifo_independently_of_bulk_builds(self):
        old_build = {"kind": "build", "state": "waiting", "since": 0}
        older = {"kind": "build", "purpose": "check", "state": "waiting", "since": 1}
        newer = {**older, "since": 2}
        self.assertTrue(host_slots.admissible([old_build, older, newer], older))
        self.assertFalse(host_slots.admissible([old_build, older, newer], newer))
        self.assertTrue(host_slots.admissible([old_build, newer], newer))

    def test_short_checks_are_readable_by_preexisting_schedulers(self):
        child = self.start("check")
        self.until(lambda: self.kinds(child) == [("check", "running")])
        [entry] = self.snapshot()
        self.assertEqual((entry["kind"], entry["purpose"]), ("build", "check"))
        # This is the lane lookup used by already-running old scheduler processes.
        self.assertEqual(entry.get("lane", entry["kind"]), "build")

    def test_normal_check_queue_has_a_five_minute_limit(self):
        from unittest.mock import patch

        with (
            patch.dict(os.environ, {"TURKU3D_SLOT_DIR": self.directory.name}),
            patch.object(host_slots, "admissible", return_value=False),
            patch.object(host_slots, "CHECK_WAIT", 0),
        ):
            with self.assertRaisesRegex(TimeoutError, "check capacity busy"):
                with host_slots.slot("check"):
                    self.fail("overfull check was admitted")
        self.assertEqual(self.snapshot(), [])

    def test_dead_owner_reclaimed_and_browser_cap(self):
        # At most LIMITS["browser"] (3) agent Chrome suites per host; the fourth waits.
        browsers = [self.start("browser") for _ in range(4)]
        self.until(lambda: len(self.snapshot()) == 4)
        self.until(
            lambda: sorted(e["state"] for e in self.snapshot()) == ["running"] * 3 + ["waiting"]
        )
        owner = next(e for e in self.snapshot() if e["state"] == "running")
        child = next(c for c in browsers if c.pid == owner["pid"])
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        self.until(lambda: [e["state"] for e in self.snapshot()] == ["running"] * 3)

    def test_child_worker_budget(self):
        for kind, expected in (("build", "2"), ("check", "1"), ("browser", "1")):
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
        self.assertEqual((host_slots.browser_workers(59.9), host_slots.browser_workers(60)), (2, 1))
        self.assertEqual(host_slots.integration_jobs(lane="integration-data"), 2)

    def test_gpu_reservations_and_chrome_queue_policy(self):
        code = {"kind": "integration", "state": "running", "since": 0}
        data = {**code, "lane": "integration-data"}
        browser = {"kind": "browser", "state": "waiting", "since": 1}
        self.assertEqual(host_slots.BUDGET["gpu"], 5)
        self.assertEqual(host_slots.COST["integration"]["gpu"], 2)
        # Spare GPU capacity is usable outside Chrome, even with both CPU lanes held.
        self.assertTrue(host_slots.admissible([code, data], browser))
        self.assertTrue(host_slots.fits([{**browser, "state": "running"}], "integration"))
        # Agents' Chrome shares the GPU budget with the integrator's Chrome stage.
        chrome = {**code, "phase": "chrome"}
        running = [chrome, *[{**browser, "state": "running", "since": n} for n in range(2)]]
        self.assertTrue(host_slots.admissible(running, browser))
        # Two integrator workers plus three agent browsers fill the five GPU workers.
        self.assertFalse(
            host_slots.admissible([*running, {**browser, "state": "running"}], browser)
        )
        # The data lane reserves no GPU capacity.
        self.assertTrue(host_slots.admissible([{**data, "phase": "chrome"}], browser))

    def test_agents_chrome_runs_beside_the_integrators_chrome_stage(self):
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
        # Admitted during the 1.5 s Chrome phase, not after it.
        self.until(
            lambda: self.kinds(browser) == [("browser", "running")] and phase() == ["chrome"]
        )
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
        # Integration lanes leave capacity for builds; only another data integration waits.
        build = self.start("build")
        again = self.start("integration-data")
        self.until(lambda: self.kinds(build) == [("build", "running")])
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
        self.assertEqual(result.stdout.strip(), "2")

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
