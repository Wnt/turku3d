"""Synthetic merge-queue returns: external workers, held task locks and retry backoff."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordination  # noqa: E402
import merge_queue  # noqa: E402
import queue_sweep  # noqa: E402
import sandbox  # noqa: E402
import task_runner  # noqa: E402

HOLD = (
    "import fcntl, sys, time\n"
    "f = open(sys.argv[1], 'a')\n"
    "fcntl.flock(f, fcntl.LOCK_EX)\n"
    "print('held', flush=True)\n"
    "time.sleep(30)\n"
)


class QueueReturnTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.tasks = self.root / "tasks"
        self.record = {"tasks": {}, "events": []}
        for patcher in (
            patch.object(sandbox, "root", return_value=self.root),
            patch.object(sandbox, "tasks_root", return_value=self.tasks),
            patch.dict(os.environ, {"TURKU3D_COORD_ROOT": str(self.root / "coord")}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def task(self, name, worker=None, finished=1.0):
        path = self.tasks / name
        path.mkdir(parents=True, exist_ok=True)
        meta = {"state": "done", "exit_code": 0, "finished": finished, "created": 0}
        meta["repo"] = str(self.root / name / "repo")
        if worker:
            meta["worker"] = worker
        (path / "meta.json").write_text(json.dumps(meta))
        (path / "last.md").write_text("## Result: DONE\n")
        (path / "precheck.json").write_text('{"main": "old-main"}')
        (self.root / name).mkdir(exist_ok=True)
        (self.root / name / "sandbox.env").write_text("")
        return path

    @contextlib.contextmanager
    def queue(self, reverify, fast=None):
        """One queue round with rebase and git stubbed; the recheck outcome is `reverify`."""
        with (
            patch.object(merge_queue, "rebase", return_value=True) as rebase,
            patch.object(merge_queue, "reverify", side_effect=reverify),
            patch.object(merge_queue.s, "sandbox", side_effect=lambda n: self.root / n),
            patch.object(merge_queue.s, "git", return_value="main"),
            patch.object(queue_sweep, "start"),
            patch("landing.fast", side_effect=fast or AssertionError("must not land")),
            patch.object(task_runner, "launch", side_effect=AssertionError("Codex resume")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            yield rebase

    def kinds(self, name):
        return [row["kind"] for row in self.record["events"] if row["task"] == name]

    def test_external_task_is_returned_with_request_and_reenqueued_by_finish(self):
        path = self.task("ext", worker="external")
        failure = ValueError("affected recheck failed (1): FAIL unit.test_x")
        with self.queue(failure):
            self.assertTrue(merge_queue.process(self.record))
            self.assertFalse(merge_queue.process(self.record))
        self.assertEqual(self.kinds("ext"), ["enqueued", "started", "returned"])
        self.assertEqual(merge_queue.ready(self.record), [])
        self.assertEqual(
            merge_queue.returned("ext", self.record),
            "affected recheck failed (1): FAIL unit.test_x",
        )
        [request] = coordination.inbox("ext", unread=True)
        self.assertEqual(
            (request["kind"], request["subject"]), ("request", "merge queue returned ext")
        )
        self.assertIn("run scripts/dev/codex-task precheck", request["body"])
        # codex-task ls/status and the board show the returned reason instead of "done".
        merge_queue.save(self.record)
        output = io.StringIO()
        with (
            patch.object(task_runner.host_slots, "waiting", return_value=None),
            contextlib.redirect_stdout(output),
        ):
            task_runner.status("ext")
        self.assertIn(
            "ext: returned: affected recheck failed (1): FAIL unit.test_x", output.getvalue()
        )
        with patch.object(coordination, "idle_minutes", return_value=3):
            self.assertEqual(
                coordination.idle_tasks(),
                ["ext: returned: affected recheck failed (1): FAIL unit.test_x, idle 3 min"],
            )
        # A new finish (new meta.finished) re-enqueues; the queue acts again.
        meta = json.loads((path / "meta.json").read_text())
        meta["finished"] = 2.0
        (path / "meta.json").write_text(json.dumps(meta))
        merge_queue.discover(self.record)
        self.assertIsNone(merge_queue.returned("ext", self.record))
        with patch.object(merge_queue.s, "sandbox", side_effect=lambda n: self.root / n):
            self.assertEqual(merge_queue.ready(self.record), ["ext"])

    def test_codex_task_still_resumes_and_failed_resume_backs_off(self):
        self.task("codex")
        with (
            self.queue(ValueError("rebase conflict")),
            patch.object(task_runner, "launch", side_effect=ValueError("no session ID")) as launch,
        ):
            merge_queue.process(self.record)
        self.assertEqual(launch.call_args.kwargs["resume_reason"], "main-moved")
        self.assertEqual(self.kinds("codex")[-1], "failed")
        row = self.record["tasks"]["codex"]
        self.assertEqual((row["retries"], self.record["events"][-1]["retry_seconds"]), (1, 300))
        self.assertIn("resume failed: no session ID", self.record["events"][-1]["reason"])

    def test_busy_task_lock_skips_round_without_touching_sandbox(self):
        path = self.task("ext", worker="external")
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD, str(path / "lock")], stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.addCleanup(holder.stdout.close)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        with self.queue(AssertionError("must not recheck")) as rebase:
            self.assertFalse(merge_queue.process(self.record))
            self.assertFalse(merge_queue.process(self.record))
            rebase.assert_not_called()
        # One `busy` event per held stretch, not one per poll.
        self.assertEqual(self.kinds("ext"), ["enqueued", "busy"])
        holder.kill()
        holder.wait()
        landed = {"commit": "c", "merge_wall_seconds": 0.1, "verification": "v"}
        with self.queue(lambda *_: {}, fast=lambda name: landed) as rebase:
            self.assertTrue(merge_queue.process(self.record))
            rebase.assert_called_once()
        self.assertEqual(self.kinds("ext")[-2:], ["started", "published"])

    def test_queue_lock_is_reentrant_and_handed_to_recheck_child(self):
        path = self.tasks / "ext/lock"
        child = (
            "import sys\n"
            f"sys.path.insert(0, {str(Path(sandbox.__file__).parent)!r})\n"
            "import sandbox\n"
            "from pathlib import Path\n"
            "try:\n"
            "    with sandbox.lock(Path(sys.argv[1]), wait=False):\n"
            "        print('locked')\n"
            "except sandbox.Busy:\n"
            "    print('busy')\n"
        )
        env = {**os.environ, "TURKU3D_WORK": str(self.root)}
        with sandbox.lock(path, wait=False):
            # landing.fast and the Codex launcher take the same lock inside the queue round.
            with sandbox.lock(path):
                pass
            run = [sys.executable, "-c", child, str(path)]
            handed = sandbox.run_group(run, cwd=self.root, env=env, hand_over=[path])
            other = sandbox.run_group(run, cwd=self.root, env=env)
        self.assertEqual(handed.stdout.strip(), "locked")
        self.assertEqual(other.stdout.strip(), "busy")
        with self.assertRaises(ValueError):
            sandbox.held_fd(path)

    def test_slow_rechecks_back_off_then_return_to_worker(self):
        self.assertEqual(
            [merge_queue.backoff(n) for n in range(1, 6)], [300, 600, 1200, 1800, 1800]
        )
        self.task("ext", worker="external")
        slow = merge_queue.Slow("affected recheck slow (over 210 s): SLOW")
        with self.queue(slow):
            merge_queue.process(self.record)
            self.assertEqual(self.kinds("ext")[-1], "failed")
            self.assertEqual(self.record["events"][-1]["retry_seconds"], 300)
            self.assertEqual(merge_queue.ready(self.record), [])
            self.record["tasks"]["ext"]["failed"] = time.time() - 301
            self.assertEqual(merge_queue.ready(self.record), ["ext"])
            merge_queue.process(self.record)
        self.assertEqual(self.kinds("ext")[-1], "returned")
        self.assertTrue(
            merge_queue.returned("ext", self.record).startswith("2 consecutive slow rechecks")
        )
        # A base move is a queue-side retry with a growing delay, never a return.
        self.task("codex")
        with self.queue(merge_queue.Retry("base moved during affected recheck")):
            for expected in (300, 600, 1200):
                merge_queue.process(self.record)
                self.assertEqual(self.record["events"][-1]["retry_seconds"], expected)
                self.record["tasks"]["codex"]["failed"] = 0
        self.assertNotIn("resumed", self.record["tasks"]["codex"])

    def test_recheck_classifies_deadline_as_slow(self):
        repo = self.root / "ext/repo"
        repo.mkdir(parents=True)
        self.task("ext", worker="external")
        (self.tasks / "ext/lock").touch()
        for returncode, stdout, error in (
            (124, "\nSLOW: check exceeded queue deadline\n", merge_queue.Slow),
            (1, "FAIL unit.test_x\n", ValueError),
        ):
            done = subprocess.CompletedProcess([], returncode, stdout)
            with (
                patch.object(merge_queue.s, "sandbox", return_value=self.root / "ext"),
                patch.object(merge_queue.s, "read_env", return_value={}),
                patch.object(merge_queue.s, "run_group", return_value=done) as run,
                self.assertRaises(error) as raised,
            ):
                merge_queue.run_precheck("ext", "main", {})
            self.assertEqual(run.call_args.kwargs["hand_over"], [self.tasks / "ext/lock"])
            self.assertEqual(isinstance(raised.exception, merge_queue.Slow), returncode == 124)


if __name__ == "__main__":
    unittest.main()
