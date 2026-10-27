"""Synthetic GitHub Pages deploys: provenance refusal, orphan force-push, site files."""

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

GENERATED = {"source_mode": "generate", "generated_qa": {"ok": True}, "imagegen_calls": 1}
PHOTO = {"method": "rectified-photo-bay-inpaint-v1", "imagegen_calls": 0}
ENABLE_PAGES = pages.enable_pages


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


class PagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        identity = {
            "GIT_AUTHOR_NAME": "Pages Test",
            "GIT_AUTHOR_EMAIL": "pages@example.test",
            "GIT_COMMITTER_NAME": "Pages Test",
            "GIT_COMMITTER_EMAIL": "pages@example.test",
        }
        patcher = patch.dict(os.environ, identity)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.published = self.root / "data"
        (self.published / "publish").mkdir(parents=True)
        (self.published / pages.CONFIG).write_text(
            json.dumps({"repo": "Owner/turku-game", "branch": "gh-pages"})
        )
        self.remote = self.root / "remote.git"
        git("init", "-q", "--bare", str(self.remote), cwd=self.root)
        self.calls = []
        for name, value in (
            ("remote_url", lambda repo: str(self.remote)),
            ("enable_pages", self.enable),
        ):
            patcher = patch.object(pages, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def enable(self, repo, branch):
        self.calls.append((repo, branch))
        return pages.site_url(repo)

    def release(self, commit, facades):
        release = self.published / "live-releases" / commit
        (release / "game").mkdir(parents=True)
        (release / "game/index.html").write_text(commit)
        (release / "tiles").mkdir()
        (release / "tiles/tileset.json").write_text("{}")
        assembly = release / "textures/assembly"
        (assembly / "facades/b").mkdir(parents=True)
        for index, meta in enumerate(facades):
            (assembly / f"facades/b/{index}.json").write_text(json.dumps(meta))
        bound = [f"b_{index}" for index in range(len(facades))]
        (assembly / "MANIFEST.json").write_text(json.dumps({"bound": bound}))
        (release / "live-build.json").write_text(
            json.dumps({"commit": commit * 4, "facades_bound": len(bound)})
        )
        live = self.published / "live"
        live.unlink(missing_ok=True)
        live.symlink_to(release, target_is_directory=True)
        return release

    def test_publish_force_pushes_one_orphan_commit_with_site_files(self):
        self.release("aaaaaaaaaaaa", [GENERATED, GENERATED])
        first = pages.publish(self.published)
        self.release("bbbbbbbbbbbb", [GENERATED])
        result = pages.publish(self.published)
        self.assertEqual(result["url"], "https://owner.github.io/turku-game/")
        self.assertEqual((result["facades"], result["release"]), (1, "bbbbbbbbbbbb" * 4))
        self.assertNotEqual(first["commit"], result["commit"])
        self.assertEqual(git("rev-list", "--count", "gh-pages", cwd=self.remote), "1")
        files = set(git("ls-tree", "-r", "--name-only", "gh-pages", cwd=self.remote).split())
        self.assertEqual(
            files,
            {
                ".nojekyll",
                "ATTRIBUTION.md",
                "README.md",
                "index.html",
                "game/index.html",
                "tiles/tileset.json",
            },
        )
        readme = git("show", "gh-pages:README.md", cwd=self.remote)
        self.assertIn("https://owner.github.io/turku-game/", readme)
        self.assertNotIn("{{", git("show", "gh-pages:index.html", cwd=self.remote))
        message = git("log", "-1", "--format=%s", "gh-pages", cwd=self.remote)
        self.assertRegex(message, r"^Release bbbbbbbbbbbb \d{4}-\d\d-\d\d$")
        self.assertEqual(self.calls, [("Owner/turku-game", "gh-pages")] * 2)

    def test_photo_derived_facades_are_never_published(self):
        self.release("cccccccccccc", [GENERATED, PHOTO])
        with self.assertRaisesRegex(pages.PhotoFacades, "1 bound facades.*b_1"):
            pages.publish(self.published)
        self.assertEqual(git("branch", "--list", cwd=self.remote), "")
        self.assertEqual(self.calls, [])

    def test_bound_count_beyond_the_manifest_is_refused(self):
        release = self.release("dddddddddddd", [GENERATED])
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "facades_bound": 300}))
        with self.assertRaisesRegex(pages.PhotoFacades, "binds 300 facades"):
            pages.check_facades(release)

    def test_enable_pages_tolerates_pages_enabled_by_the_first_push(self):
        site = json.dumps({"html_url": "https://owner.github.io/turku-game/"})
        answers = iter(
            (
                subprocess.CompletedProcess([], 1, "", "Not Found (HTTP 404)"),
                subprocess.CompletedProcess([], 1, "", "already enabled. (HTTP 409)"),
                subprocess.CompletedProcess([], 0, site, ""),
            )
        )
        with patch.object(pages, "gh", side_effect=lambda *args: next(answers)):
            url = ENABLE_PAGES("Owner/turku-game", "gh-pages")
        self.assertEqual(url, "https://owner.github.io/turku-game/")

    def test_no_config_means_no_pages_target(self):
        (self.published / pages.CONFIG).unlink()
        self.assertIsNone(pages.config(self.published))


if __name__ == "__main__":
    unittest.main()
