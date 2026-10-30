"""Game-only direct publication against a tiny local bare Git remote."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pages  # noqa: E402


def git(*args, cwd):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class PagesTests(unittest.TestCase):
    def test_game_only_publication_appends_to_remote_and_needs_no_tiles_or_facades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, release = root / "data", root / "release"
            (data / "publish").mkdir(parents=True)
            (data / pages.CONFIG).write_text('{"repo":"Owner/game"}')
            (release / "game").mkdir(parents=True)
            (release / "game/index.html").write_text("<html><head></head><body>game</body></html>")
            remote = root / "remote.git"
            git("init", "-q", "--bare", str(remote), cwd=root)
            env = {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.test",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.test",
            }
            with (
                patch.dict(os.environ, env),
                patch.object(pages, "remote_url", return_value=str(remote)),
            ):
                (release / "live-build.json").write_text(json.dumps({"commit": "a" * 40}))
                first = pages.publish(data, release)
                (release / "live-build.json").write_text(json.dumps({"commit": "b" * 40}))
                second = pages.publish(data, release)
            self.assertEqual(git("rev-parse", second["commit"] + "^", cwd=remote), first["commit"])
            names = git("ls-tree", "--name-only", "-r", second["commit"], cwd=remote)
            self.assertIn("game/index.html", names)
            document = git("show", second["commit"] + ":game/index.html", cwd=remote)
            self.assertIn('name="turku3d-release" content="' + "b" * 40 + '"', document)
            self.assertNotIn("turku3d-release", (release / "game/index.html").read_text())
            self.assertNotIn("tiles/", names)
            self.assertEqual(second["retries"], 0)
            self.assertFalse((data / "publish/pages.lock").exists())

    def test_remote_race_is_reported_without_retry_or_revert(self):
        with (
            patch.object(
                pages,
                "git",
                side_effect=["", "", "tree", "commit", subprocess.CalledProcessError(1, "push")],
            ) as git_mock,
            patch.object(pages, "fetch_head", return_value=None),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                pages.push(
                    Path("/unused"),
                    {"repo": "Owner/game", "branch": "gh-pages"},
                    {"commit": "a" * 40},
                    "today",
                    None,
                    Path("/source"),
                )
        self.assertEqual(sum(call.args[0] == "push" for call in git_mock.call_args_list), 1)
