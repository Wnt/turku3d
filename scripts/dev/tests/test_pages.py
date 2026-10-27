"""Synthetic GitHub Pages deploys: provenance refusal, orphan force-push, site files, notes."""

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

# The cases patch os.environ: tests/run.py runs them one by one in a process of their own.
SERIAL = True
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

    def release(self, commit, facades, **fields):
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
        record = {"commit": commit * 4, "facades_bound": len(bound), **fields}
        (release / "live-build.json").write_text(json.dumps(record))
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
        self.assertRegex(message, r"^Release bbbbbbbbbbbb \d{4}-\d\d-\d\d \d\d:\d\d UTC$")
        self.assertEqual(self.calls, [("Owner/turku-game", "gh-pages")] * 2)

    def test_derived_maps_from_generated_styles_are_published(self):
        derived = {"source_mode": "derived", "derived_from": "k1", "style": {"path": "style.json"}}
        neighbour = {"source_mode": "derived-neighbour", "derived_from": "k1"}
        self.assertTrue(pages.generated(derived))
        self.assertTrue(pages.generated(neighbour))
        self.assertFalse(pages.generated({"source_mode": "derived"}))
        self.assertFalse(
            pages.generated(
                {
                    "source_mode": "derived",
                    "derived_from": "k1",
                    "method": "rectified-photo-bay-inpaint-v1",
                }
            )
        )
        release = self.release("eeeeeeeeeeee", [GENERATED, derived, neighbour])
        self.assertEqual(pages.check_facades(release), 3)

    def test_photo_derived_facades_are_never_published(self):
        self.release("cccccccccccc", [GENERATED, PHOTO])
        with self.assertRaisesRegex(pages.PhotoFacades, "1 bound facades.*b_1"):
            pages.publish(self.published)
        self.assertEqual(git("branch", "--list", cwd=self.remote), "")
        self.assertEqual(self.calls, [])

    def history(self, count):
        """A source repository with count commits; returns their ids, oldest first."""
        source = self.root / "source"
        source.mkdir(exist_ok=True)
        git("init", "-q", "-b", "main", cwd=source)
        for n in range(count):
            (source / "f.txt").write_text(str(n))
            git("add", "f.txt", cwd=source)
            git("commit", "-q", "-m", f"Change {n}", cwd=source)
        return source, git("rev-list", "--reverse", "HEAD", cwd=source).splitlines()

    def message(self):
        return git("log", "-1", "--format=%B", "gh-pages", cwd=self.remote)

    def test_any_built_release_publishes_with_its_failures_as_release_notes(self):
        source, commits = self.history(4)
        advisory = [
            {
                "stage": "chrome",
                "detail": "chrome failed (1); 2 failed",
                "specs": ["webgpu:game/tests/e2e-tile/save-menu.spec.ts"],
            },
            {"stage": "facade-ground-audit", "detail": "facade-ground-audit failed (1); 30 fail"},
        ]
        # No full Chrome suite ran, and advisory checks failed: it is published anyway.
        release = self.release("x", [GENERATED], advisory=advisory, **{"from": commits[0]})
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "commit": commits[2]}))
        result = pages.publish(self.published, repo=source)
        self.assertEqual(result["release"], commits[2])
        message = self.message()
        self.assertIn(f"Commits {commits[0][:12]}..{commits[2][:12]}:", message)
        self.assertIn(f"  {commits[2][:12]} Change 2", message)
        self.assertIn(f"  {commits[1][:12]} Change 1", message)
        self.assertNotIn("Change 0", message)
        self.assertIn("Known broken (advisory checks that failed; published anyway):", message)
        self.assertIn("  chrome: chrome failed (1); 2 failed", message)
        self.assertIn("    webgpu:game/tests/e2e-tile/save-menu.spec.ts", message)
        self.assertIn("  facade-ground-audit: facade-ground-audit failed (1); 30 fail", message)
        # The next release's notes start where this Pages release ended.
        release = self.release("y", [GENERATED], advisory=[], **{"from": commits[0]})
        record = json.loads((release / "live-build.json").read_text())
        (release / "live-build.json").write_text(json.dumps({**record, "commit": commits[3]}))
        pages.publish(self.published, release, repo=source)
        message = self.message()
        self.assertIn(f"Commits {commits[2][:12]}..{commits[3][:12]}:", message)
        self.assertNotIn("Change 1", message)
        self.assertIn("Advisory checks: all green", message)

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
