"""Per-task browser budget: accounting, brief override, refusal and the host-slot debit."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_budget  # noqa: E402

DEV = Path(__file__).resolve().parents[1]


class BrowserBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tasks = self.root / "tasks"
        self.brief = self.root / "brief.md"
        self.brief.write_text("# demo\n")
        (self.tasks / "demo").mkdir(parents=True)
        (self.tasks / "demo/meta.json").write_text(json.dumps({"brief": str(self.brief)}))

    def read(self, task="demo"):
        return browser_budget.read(task, tasks=self.tasks)

    def test_unregistered_sandboxes_are_not_budgeted(self):
        for task in ("main", "integration", ""):
            self.assertIsNone(self.read(task))
            self.assertIsNone(browser_budget.debit(task, ["x"], 60, 1, tasks=self.tasks))
        self.assertIsNone(browser_budget.refusal(None))

    def test_debits_accumulate_wall_seconds_times_workers(self):
        account = self.read()
        self.assertEqual((account["budget_minutes"], account["used_minutes"]), (5.0, 0))
        command = ["/a/playwright", "test", "tests/e2e/smoke.spec.ts", "--project", "webgl"]
        browser_budget.debit("demo", command, 120, 2, tasks=self.tasks)
        account = browser_budget.debit("demo", ["/a/playwright", "test"], 30, 1, tasks=self.tasks)
        self.assertEqual((account["used_minutes"], account["remaining_minutes"]), (4.5, 0.5))
        first = account["runs"][0]
        self.assertEqual(
            (first["specs"], first["projects"]), (["tests/e2e/smoke.spec.ts"], ["webgl"])
        )
        self.assertEqual(
            (first["browser_minutes"], first["command"][:15]), (4.0, "playwright test")
        )
        saved = json.loads((self.tasks / "demo/browser-budget.json").read_text())
        self.assertEqual(len(saved["runs"]), 2)
        self.assertIn("4.5 of 5 browser-minutes used, 0.5 left", browser_budget.summary(account))
        self.assertIsNone(browser_budget.refusal(account))

    def test_spent_budget_refuses_and_a_brief_line_overrides_it(self):
        account = browser_budget.debit("demo", ["playwright"], 20 * 60, 1, tasks=self.tasks)
        self.assertIn("browser budget spent", browser_budget.refusal(account))
        self.brief.write_text("# demo\nBROWSER_BUDGET_MIN = 45\n")
        account = self.read()
        self.assertEqual((account["budget_minutes"], account["remaining_minutes"]), (45.0, 25.0))
        self.assertIsNone(browser_budget.refusal(account))

    def host_slots(self, task, *command):
        env = {
            **os.environ,
            "TURKU3D_TASKS_ROOT": str(self.tasks),
            "TURKU3D_SLOT_DIR": str(self.root / "slots"),
            "TURKU3D_SANDBOX": task,
            "TURKU3D_JOBS": "4",
        }
        env.pop("TURKU3D_INTEGRATION_OWNER", None)
        return subprocess.run(
            [sys.executable, str(DEV / "host_slots.py"), "browser", "--", *command],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_host_slot_browser_runs_debit_and_a_spent_budget_refuses(self):
        done = self.host_slots("demo", sys.executable, "-c", "import time; time.sleep(0.3)")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("browser budget:", done.stderr)
        [run] = self.read()["runs"]
        # Agents' browser commands run with one worker (the slot caps TURKU3D_JOBS).
        self.assertEqual(run["workers"], 1)
        self.assertGreaterEqual(run["seconds"], 0.3)
        browser_budget.debit("demo", ["playwright"], 20 * 60, 1, tasks=self.tasks)
        refused = self.host_slots("demo", sys.executable, "-c", "print('ran')")
        self.assertEqual(refused.returncode, 3)
        self.assertNotIn("ran", refused.stdout)
        self.assertIn("browser budget spent", refused.stderr)
        # The orchestrator's main checkout has no task and no budget.
        self.assertEqual(self.host_slots("main", sys.executable, "-c", "pass").returncode, 0)


if __name__ == "__main__":
    unittest.main()
