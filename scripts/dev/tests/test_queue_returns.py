"""Synthetic merge-queue returns: failed or returned landings never resume a worker."""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordination  # noqa: E402
import merge_queue  # noqa: E402
import sandbox  # noqa: E402
import task_runner  # noqa: E402


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
            patch.object(merge_queue, "integrate"),
            patch.object(task_runner, "launch", side_effect=AssertionError("Codex resume")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def task(self, name, finished=1.0):
        path = self.tasks / name
        path.mkdir(parents=True, exist_ok=True)
        meta = {"state": "done", "exit_code": 0, "finished": finished, "created": 0}
        meta["repo"] = str(self.root / name / "repo")
        meta["worker"] = "external"
        (path / "meta.json").write_text(json.dumps(meta))
        (path / "last.md").write_text("## Result: DONE\n")
        (self.root / name).mkdir(exist_ok=True)
        (self.root / name / "sandbox.env").write_text("")
        return path

    def kinds(self, name):
        return [row["kind"] for row in self.record["events"] if row["task"] == name]

    def test_failed_landing_is_returned_with_request_and_reenqueued_by_finish(self):
        path = self.task("ext")
        failure = ValueError("fast gate failed: FAIL unit.test_x")
        with patch("landing.land", side_effect=failure), contextlib.redirect_stdout(io.StringIO()):
            merge_queue.process(self.record)
            merge_queue.process(self.record)
        self.assertEqual(self.kinds("ext"), ["enqueued", "started", "returned"])
        self.assertEqual(merge_queue.ready(self.record), [])
        self.assertEqual(
            merge_queue.returned("ext", self.record), "fast gate failed: FAIL unit.test_x"
        )
        [request] = coordination.inbox("ext", unread=True)
        self.assertEqual(
            (request["kind"], request["subject"]), ("request", "merge queue returned ext")
        )
        self.assertIn("gate --fast --base main", request["body"])
        # codex-task ls/status and the board show the returned reason instead of "done".
        merge_queue.save(self.record)
        output = io.StringIO()
        with (
            patch.object(task_runner.host_slots, "waiting", return_value=None),
            contextlib.redirect_stdout(output),
        ):
            task_runner.status("ext")
        self.assertIn("ext: returned: fast gate failed: FAIL unit.test_x", output.getvalue())
        with patch.object(coordination, "idle_minutes", return_value=3):
            self.assertEqual(
                coordination.idle_tasks(),
                ["ext: returned: fast gate failed: FAIL unit.test_x, idle 3 min"],
            )
        # A new finish (new meta.finished) re-enqueues; the queue acts again.
        meta = json.loads((path / "meta.json").read_text())
        meta["finished"] = 2.0
        (path / "meta.json").write_text(json.dumps(meta))
        merge_queue.discover(self.record)
        self.assertIsNone(merge_queue.returned("ext", self.record))
        self.assertEqual(merge_queue.ready(self.record), ["ext"])

    def test_lander_return_is_recorded_without_a_second_request(self):
        path = self.task("conflict")

        def land(name, force=False):
            meta = json.loads((path / "meta.json").read_text())
            meta.update(state="returned", returned_reason="rebase conflict in a.txt")
            (path / "meta.json").write_text(json.dumps(meta))
            raise ValueError("conflict: rebase conflict in a.txt")

        with patch("landing.land", side_effect=land):
            merge_queue.process(self.record)
        self.assertEqual(self.kinds("conflict")[-1], "returned")
        self.assertEqual(coordination.inbox("conflict", unread=True), [])

    def test_unacknowledged_request_holds_then_retries(self):
        self.task("held")
        with patch(
            "landing.land", side_effect=ValueError("held: unacknowledged request/warning abc")
        ) as land:
            merge_queue.process(self.record)
            merge_queue.process(self.record)
            self.assertEqual(land.call_count, 1)
            self.record["tasks"]["held"]["held"] = time.time() - merge_queue.HELD_SECONDS - 1
            merge_queue.process(self.record)
            self.assertEqual(land.call_count, 2)
        self.assertNotIn("returned", self.record["tasks"]["held"])

    def test_busy_task_lock_skips_round(self):
        self.task("busy")
        with patch("landing.land", side_effect=sandbox.Busy("busy")):
            merge_queue.process(self.record)
        self.assertEqual(self.kinds("busy"), ["enqueued", "started", "busy"])
        self.assertEqual(merge_queue.ready(self.record), ["busy"])


if __name__ == "__main__":
    unittest.main()
