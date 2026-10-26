"""Small process-level tests of host admission and crash recovery."""

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
        self.env = {**os.environ, "TURKU3D_SLOT_DIR": self.directory.name}
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

    def test_capacity_and_land_priority(self):
        builds = [self.start("build") for _ in range(3)]
        self.until(lambda: sum(e["state"] == "running" for e in self.snapshot()) == 3)
        land = self.start("land")
        self.until(lambda: any(e["kind"] == "land" for e in self.snapshot()))
        extra = self.start("build")
        self.until(lambda: len(self.snapshot()) == 5)
        self.assertEqual(
            [(e["kind"], e["state"]) for e in self.snapshot() if e["pid"] == extra.pid],
            [("build", "waiting")],
        )
        for child in builds:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=3)
        self.until(
            lambda: any(e["kind"] == "land" and e["state"] == "running" for e in self.snapshot())
        )
        self.assertEqual(
            [(e["kind"], e["state"]) for e in self.snapshot() if e["pid"] == extra.pid],
            [("build", "waiting")],
        )
        os.killpg(land.pid, signal.SIGTERM)
        land.wait(timeout=3)
        self.until(
            lambda: any(e["pid"] == extra.pid and e["state"] == "running" for e in self.snapshot())
        )

    def test_dead_owner_reclaimed_and_browser_cap(self):
        browsers = [self.start("browser") for _ in range(3)]
        self.until(lambda: len(self.snapshot()) == 3)
        self.assertEqual(sum(e["state"] == "running" for e in self.snapshot()), 2)
        owner = next(e for e in self.snapshot() if e["state"] == "running")
        child = next(c for c in browsers if c.pid == owner["pid"])
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        self.until(lambda: all(e["state"] == "running" for e in self.snapshot()))
        self.assertEqual(len(self.snapshot()), 2)

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

    def test_land_reservation_children_use_whole_host(self):
        # The exclusive reservation and the build/browser commands nested in it share all cores.
        for kind in ("build", "browser"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.nested(kind, "import os; print(os.environ['TURKU3D_JOBS'])"),
                    str(host_slots.land_jobs(kind)),
                )

    def test_landing_children_reuse_exclusive_reservation(self):
        # A queue-run precheck builds under the queue's own landing reservation.
        for kind in ("build", "land"):
            with self.subTest(kind=kind):
                self.assertEqual(self.nested(kind), "42")

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
                "land",
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


if __name__ == "__main__":
    unittest.main()
