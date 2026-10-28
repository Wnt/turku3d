"""Small Pages ordering/retry tests; git and all network operations are fake."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pages  # noqa: E402

SERIAL = True


class OrderingTests(unittest.TestCase):
    def test_descendant_code_with_current_or_newer_tiles_can_publish(self):
        for stamp in (10, 11):
            with self.subTest(stamp=stamp), patch.object(pages, "git", return_value="") as git:
                self.assertIsNone(
                    pages.superseded(
                        {"commit": "new", "data_time": stamp},
                        {"commit": "old", "data_time": 10},
                        Path("/source"),
                    )
                )
                self.assertEqual(git.call_args.args, ("merge-base", "--is-ancestor", "old", "new"))

    def test_older_divergent_or_unknown_source_cannot_publish(self):
        error = subprocess.CalledProcessError(1, ["git"])
        with patch.object(pages, "git", side_effect=error):
            self.assertIn("source", pages.superseded({"commit": "old"}, {"commit": "new"}))

    def test_same_code_requires_current_tile_inputs(self):
        for stamp, allowed in ((9, False), (None, False), (10, True), (11, True)):
            with self.subTest(stamp=stamp), patch.object(pages, "git") as git:
                reason = pages.superseded(
                    {"commit": "same", "data_time": stamp}, {"commit": "same", "data_time": 10}
                )
                self.assertEqual(reason is None, allowed)
                git.assert_not_called()

    def test_newer_code_cannot_regress_tile_inputs(self):
        with patch.object(pages, "git", return_value=""):
            self.assertIn(
                "tile",
                pages.superseded(
                    {"commit": "new", "data_time": 9}, {"commit": "old", "data_time": 10}
                ),
            )

    def attempt(self, failures, newer=False):
        calls = []
        heads = iter(("parent1", "parent2"))
        current = {"commit": "same", "data_time": 10}

        def fake(*args, **kwargs):
            calls.append(args)
            if args[0] == "fetch":
                return ""
            if args[:2] == ("rev-parse", "FETCH_HEAD"):
                return next(heads)
            if args[0] == "log":
                if newer and len([c for c in calls if c[0] == "fetch"]) == 2:
                    return 'Pages-Release: {"commit":"same","data_time":12}'
                return pages.release_message("", current)
            if args[0] == "push" and failures:
                raise subprocess.CalledProcessError(1, ["git", "push"], stderr=failures.pop(0))
            return "tree-or-commit"

        with (
            patch.object(pages, "git", side_effect=fake),
            patch.object(pages, "notes", return_value="notes"),
        ):
            result = pages.push(
                Path("/site"),
                {"repo": "o/r", "branch": "gh-pages"},
                current,
                "date",
                {},
                Path("/source"),
            )
        return result, calls

    def test_retry_fetches_and_rebuilds_on_new_parent(self):
        for rejection in ("cannot lock ref", "non-fast-forward", "fetch first"):
            result, calls = self.attempt([rejection])
            self.assertEqual((result["status"], result["retries"]), ("published", 1))
            commits = [c for c in calls if c[0] == "commit-tree"]
            self.assertEqual([c[3] for c in commits], ["parent1", "parent2"])
            self.assertFalse(any("force" in arg for c in calls for arg in c))

    def test_retry_skips_when_remote_winner_has_newer_tiles(self):
        result, calls = self.attempt(["cannot lock ref"], newer=True)
        self.assertEqual((result["status"], result["retries"]), ("skipped", 1))
        self.assertEqual(len([c for c in calls if c[0] == "push"]), 1)

    def test_retry_is_bounded_and_auth_errors_are_not_retried(self):
        for failures in (["cannot lock ref", "cannot lock ref"], ["Permission denied"]):
            with self.subTest(failures=failures), self.assertRaises(subprocess.CalledProcessError):
                self.attempt(failures)

    def test_first_publication_has_no_parent(self):
        def fake(*args, **kwargs):
            if args[0] == "fetch":
                raise subprocess.CalledProcessError(
                    128, ["git"], stderr="fatal: couldn't find remote ref gh-pages"
                )
            return "commit"

        with (
            patch.object(pages, "git", side_effect=fake) as git,
            patch.object(pages, "notes", return_value="notes"),
        ):
            result = pages.push(
                Path("/site"),
                {"repo": "o/r", "branch": "gh-pages"},
                {"commit": "first"},
                "date",
                {},
                Path("/source"),
            )
        self.assertEqual(result["status"], "published")
        commit = next(c.args for c in git.call_args_list if c.args[0] == "commit-tree")
        self.assertNotIn("-p", commit)


if __name__ == "__main__":
    unittest.main()
