"""Which integrator lanes a landing, a finished run or the idle-host refresh starts (ensure);
nothing polls."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integrator  # noqa: E402
import landing  # noqa: E402

# The cases patch os.environ and module globals: tests/run.py runs them in a process of their own.
SERIAL = True


class EnsureTests(unittest.TestCase):
    """integrator.ensure: what a landing or a finished run starts; nothing polls."""

    published = Path("/published")

    def ensure(self, *, live="older", last=None, lanes=None, data_due=False, full_due=False):
        lanes = lanes or {}
        with (
            patch.object(integrator.s, "git", return_value="main-head"),
            patch.object(integrator, "live_commit", return_value=live),
            patch.object(integrator, "last", return_value=last or {}),
            patch.object(integrator, "data_key", return_value="data"),
            patch.object(integrator, "full_due", return_value=full_due),
            patch.object(
                integrator, "running", side_effect=lambda lane=integrator.NAME: lanes.get(lane)
            ),
            patch.object(integrator, "data_due", return_value=data_due),
            patch.object(integrator, "start") as start,
        ):
            started = integrator.ensure(self.published)
        self.assertEqual([call.args[1] for call in start.call_args_list], started)
        return started

    def test_code_lane_starts_only_when_main_moved_past_the_last_run(self):
        cases = (
            ("main-head", {}, []),  # live already names main
            ("older", {"to": "main-head"}, []),  # the last run took main
            ("older", {"to": "main-head", "data_key": "old-data"}, []),  # data is the data lane's
            ("older", {"to": "older"}, ["code"]),  # main moved
        )
        for live, last, started in cases:
            with self.subTest(live=live, last=last):
                self.assertEqual(self.ensure(live=live, last=last), started)

    def test_due_full_suite_starts_the_code_lane_for_live_main(self):
        self.assertEqual(self.ensure(live="main-head", full_due=True), ["code"])

    def test_running_code_lane_is_not_doubled_and_data_lane_starts_beside_it(self):
        running = {integrator.NAME: {"to": "x", "data_key": "old"}}
        self.assertEqual(self.ensure(lanes=running), [])
        self.assertEqual(self.ensure(lanes=running, data_due=True), ["data"])

    def test_refresh_stale_starts_the_data_lane_only_for_stale_tiles_on_an_idle_lane(self):
        for record, lane, started in (
            ({"stale_by_code": ["t0_0_0"]}, None, True),
            ({"stale_by_code": []}, None, False),
            ({"stale_by_code": ["t0_0_0"]}, {"to": "x"}, False),
        ):
            with (
                self.subTest(record=record, lane=lane),
                patch.object(integrator, "live_commit", return_value="live"),
                patch.object(integrator.served, "record", return_value=record),
                patch.object(integrator, "running", return_value=lane),
                patch.object(integrator, "start") as start,
            ):
                self.assertEqual(integrator.refresh_stale(self.published), started)
            if started:
                start.assert_called_once_with(self.published, "data", refresh_stale=True)

    def test_nothing_starts_without_a_live_release(self):
        self.assertEqual(self.ensure(live=None, data_due=True), [])

    def test_landing_starts_the_integrator_after_its_broadcast(self):
        output = io.StringIO()
        with (
            patch.object(landing.coord, "open_requests", return_value=[]),
            patch.object(landing.coord, "broadcast_landed", return_value=["peer"]) as broadcast,
            patch.object(integrator, "ensure", return_value=["code"]) as ensure,
            contextlib.redirect_stdout(output),
        ):
            landing.report("demo", {"commit": "c0ffee"}, "Subject", 5.0, "a note")
        broadcast.assert_called_once_with("demo", {"commit": "c0ffee"}, "Subject", 5.0, "a note")
        ensure.assert_called_once_with(landing.landing_class.published_root())
        self.assertIn("landed message sent to peer", output.getvalue())
        self.assertIn("integrator started (code)", output.getvalue())

    def test_finished_run_starts_the_next_and_an_idle_run_does_not(self):
        for row, chained in (({"result": "red"}, 1), (None, 0)):
            with (
                self.subTest(row=row),
                tempfile.TemporaryDirectory() as temp,
                patch.dict(os.environ, {"TURKU3D_WT_ROOT": temp}),
                patch.object(integrator, "run_once", return_value=row),
                patch.object(integrator, "ensure", return_value=[]) as ensure,
                patch.object(sys, "argv", ["integrator.py", "run", "--published", temp]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                integrator.main()
                self.assertEqual(ensure.call_count, chained)
                # The lane lock is free again when the next run starts.
                self.assertIsNone(integrator.running())


if __name__ == "__main__":
    unittest.main()
