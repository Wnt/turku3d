"""Synthetic paired evidence and immutable checkout tests; no real tiles or reference data."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import draft_reference_score as score  # noqa: E402
import integration_reference as reference  # noqa: E402

SERIAL = True


def metric(a=2, b=1, samples=4, better="lower", delta=-1):
    return {
        "before": {"value": a, "samples": samples, "better": better},
        "after": {"value": b, "samples": samples, "better": better},
        "delta": delta,
    }


class ScoreTests(unittest.TestCase):
    def rows(self, value):
        return score.metric_rows("t0_0_0", {"metrics": {"synthetic": value}})

    def test_direction_and_noise(self):
        for direction, delta, expected in (
            ("lower", -1, "better"),
            ("higher", -1, "worse"),
            ("higher", 1, "better"),
            ("lower", 1, "worse"),
            ("lower", 0.000001, "unchanged"),
        ):
            with self.subTest(direction=direction, delta=delta):
                self.assertEqual(
                    self.rows(metric(better=direction, delta=delta))[0]["verdict"], expected
                )

    def test_street_visible_priority_and_informational_roofs(self):
        rows = score.metric_rows(
            "synthetic",
            {
                "metrics": {
                    name: metric()
                    for name in (
                        "roof_height_mae_m",
                        "ground_height_rmse_m",
                        "bridge_deck_existence",
                        "facade_colour_dE76",
                    )
                }
            },
        )
        self.assertEqual(
            [row["metric"] for row in rows],
            [
                "facade_colour_dE76",
                "bridge_deck_existence",
                "ground_height_rmse_m",
                "roof_height_mae_m",
            ],
        )
        self.assertTrue(rows[-1]["informational"])
        self.assertIn("informational: better", score.table(rows))

    def test_null_zero_support_mismatch_and_nonfinite_abstain(self):
        for change in (
            {"value": None},
            {"value": float("nan")},
            {"samples": 0},
            {"samples": 3},
            {"better": "unknown"},
        ):
            current = metric()
            current["after"].update(change)
            row = self.rows(current)[0]
            self.assertEqual(row["verdict"], "no evidence")
            self.assertIsNone(row["delta"])

    def test_table_preserves_null(self):
        self.assertIn("null", score.table(self.rows(metric(a=None, delta=None))))
        self.assertIn("no evidence", score.table(self.rows(metric(a=None, delta=None))))

    def test_missing_private_tool_exits_before_reading_sandbox_or_git(self):
        with (
            patch.object(score, "private_tool", return_value=None),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(score.main(["--before", "missing", "--after", "worktree"]), 0)
        self.assertIn("nothing scored", out.getvalue())

    def test_ref_and_dirty_snapshot_capture_deletions_and_untracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args], check=True, capture_output=True
                )

            git("init", "-q")
            (repo / "a.py").write_text("original")
            (repo / "deleted.py").write_text("original")
            git("add", ".")
            git(
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            )
            (repo / "a.py").write_text("dirty")
            (repo / "deleted.py").unlink()
            (repo / "new.py").write_text("new")
            score.snapshot("HEAD", root / "before", repo)
            score.snapshot("worktree", root / "after", repo)
            (repo / "a.py").write_text("changed again")
            self.assertEqual((root / "before/a.py").read_text(), "original")
            self.assertTrue((root / "before/deleted.py").exists())
            self.assertEqual((root / "after/a.py").read_text(), "dirty")
            self.assertFalse((root / "after/deleted.py").exists())
            self.assertEqual((root / "after/new.py").read_text(), "new")
            score.snapshot(str(root / "after"), root / "frozen-again", repo)
            self.assertEqual((root / "frozen-again/a.py").read_text(), "dirty")

    def test_comparison_uses_paired_diff_and_retains_failed_tiles(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "measurement"
            result = {"metrics": {"synthetic": metric()}}
            responses = [
                subprocess.CompletedProcess([], 0, json.dumps(result)),
                subprocess.CompletedProcess([], 1, ""),
            ]
            with (
                patch.object(score, "private_tool", return_value="fake-gmaps"),
                patch.object(score, "build_version", return_value={"seconds": {"a": 1, "b": 2}}),
                patch.object(score.subprocess, "run", side_effect=responses) as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                report = score.compare("HEAD", "worktree", directory, tiles=["a", "b"])
            self.assertEqual(report["unscored"], ["b"])
            self.assertEqual(report["rows"][0]["verdict"], "better")
            self.assertEqual(run.call_args_list[0].args[0][1], "diff")
            self.assertTrue((directory / "summary.json").exists())

    def test_context_is_built_but_not_scored(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "measurement"
            result = {"metrics": {"synthetic": metric()}}
            with (
                patch.object(score, "private_tool", return_value="fake-gmaps"),
                patch.object(
                    score, "build_version", return_value={"seconds": {"a": 1, "neighbor": 2}}
                ) as build,
                patch.object(
                    score.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, json.dumps(result)),
                ) as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                report = score.compare(
                    "HEAD", "worktree", directory, tiles=["a"], context_tiles=["neighbor", "a"]
                )
            self.assertEqual(build.call_args.args[3], ("a", "neighbor"))
            self.assertEqual(run.call_count, 1)
            self.assertEqual([row["tile"] for row in report["rows"]], ["a"])
            self.assertIn("neighbor |", (directory / "table.md").read_text())


class PrivateAdvisoryTests(unittest.TestCase):
    def test_noncoarse_and_absent_private_install_do_not_spawn(self):
        run = SimpleNamespace(id="batch", main_repo=Path("/synthetic"))
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(reference.score, "private_tool", return_value=None),
            patch.object(reference.subprocess, "Popen") as spawn,
        ):
            reference.queue(run, {"from": "a", "to": "b"}, Path(temp), {})
            spawn.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])
        with patch.object(reference.s, "git", return_value=""):
            self.assertFalse(reference.changed(Path("/synthetic"), "a", "b"))

    def test_queue_has_only_private_inbox_recipients_and_atomic_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "local"
            directory.mkdir()
            request = {"batch": "abc.1", "recipients": ["author", "orchestrator"]}
            reference.enqueue(request, directory, {"synthetic": 1}, root / "queue")
            jobs = list((root / "queue/pending").glob("*.json"))
            self.assertEqual(len(jobs), len(score.TILES))
            self.assertEqual(len(list((root / "queue/pending").iterdir())), len(jobs))
            for path in jobs:
                job = json.loads(path.read_text())
                self.assertEqual(job["command"], "diff")
                self.assertEqual(job["notify_recipients"], ["author", "orchestrator"])
                self.assertNotIn("all", job["notify_recipients"])
                self.assertTrue(Path(job["before"]).is_absolute())

    def test_enqueue_hook_never_changes_public_row_or_uses_inherited_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = SimpleNamespace(id="abc.1", main_repo=root)
            row = {"from": "aaa", "to": "bbb", "advisory": []}

            def snapshot(version, destination, repo):
                destination.mkdir()

            with (
                patch.object(reference.score, "private_tool", return_value="fake"),
                patch.object(reference, "changed", return_value=True),
                patch.object(reference.score, "snapshot", side_effect=snapshot),
                patch.object(reference.advisory, "recipients", return_value=["author"]),
                patch.object(reference.subprocess, "Popen") as spawn,
            ):
                reference.queue(run, row, root, {"TURKU3D_INTEGRATION_OWNER": "old"})
                self.assertEqual(row, {"from": "aaa", "to": "bbb", "advisory": []})
                self.assertNotIn("TURKU3D_INTEGRATION_OWNER", spawn.call_args.kwargs["env"])
                self.assertTrue(spawn.call_args.kwargs["start_new_session"])
                reference.queue(run, row, root, {})
                self.assertEqual(spawn.call_count, 1)
                request = json.loads(
                    (root / "reports/draft-reference-advisory/abc.1/request.json").read_text()
                )
                self.assertEqual(request["recipients"], ["author", "orchestrator"])

    def test_private_warning_never_uses_public_send_or_board(self):
        with (
            patch.object(reference.coord, "post") as post,
            patch.object(reference.coord, "send") as send,
        ):
            reference.private_notice(["orchestrator"], "synthetic", "private")
            send.assert_not_called()
            self.assertEqual(post.call_args.args[1:3], ("orchestrator", "warning"))
