"""Synthetic paired evidence and immutable checkout tests; no real tiles or reference data."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import draft_reference_score as score  # noqa: E402

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
