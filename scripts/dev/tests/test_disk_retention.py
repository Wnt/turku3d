"""Disk retention on tiny synthetic directories; no builds, servers or real caches."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integrator
import live_cache
import live_retention
import sandbox

SERIAL = True


class DiskRetentionTests(unittest.TestCase):
    def test_releases_keep_live_previous_two_and_running_or_rollback_references(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            releases = data / "live-releases"
            for index, name in enumerate(
                ("old", "batch-key", "rollback", "prev2", "prev1", "live")
            ):
                path = releases / name
                path.mkdir(parents=True)
                (path / "payload").write_text(name)
                (path / "live-build.json").write_text("{}")
                os.utime(path, (index + 1, index + 1))
            incomplete = releases / "failed-prepare"
            incomplete.mkdir()
            prepared = releases / "prepared-but-unpublished"
            prepared.mkdir()
            (prepared / "live-build.json").write_text("{}")
            for name, previous in (("live", "prev1"), ("prev1", "prev2")):
                (releases / name / ".publication.json").write_text(
                    json.dumps({"previous": previous})
                )
            # Directory mtimes must not let a prepared release replace a real predecessor.
            os.utime(prepared, (100, 100))
            (data / "live").symlink_to(releases / "live")
            (data / "rollback").symlink_to(releases / "rollback")
            # Explicit running references also protect a data release whose id adds a key.
            with patch.object(integrator, "running", return_value={"from": "batch"}):
                live_retention.prune_releases(data)
            self.assertEqual(
                {p.name for p in releases.iterdir()},
                {"live", "prev1", "prev2", "batch-key", "rollback"},
            )
            (data / "rollback").unlink()
            with patch.object(integrator, "running", return_value=None):
                live_retention.prune_releases(data)
            self.assertEqual({p.name for p in releases.iterdir()}, {"live", "prev1", "prev2"})

    def test_stage_retention_is_per_stage_and_preserves_recent_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            for stage in ("refresh", "tile-build", "game-build"):
                for index in range(6):
                    path = live_cache.directory(data, stage, str(index))
                    path.mkdir(parents=True)
                    (path / "result.json").write_text("{}")
                    if stage == "game-build":
                        (path / "build/game").mkdir(parents=True)
                        (path / "build/game/file").write_text(str(index))
                    os.utime(path, (index + 1, index + 1))
            self.assertEqual(live_cache.restore(data, data / "work", "game-build", "0"), {})
            self.assertEqual((data / "work/build/game/file").read_text(), "0")
            live_cache.prune(data, "game-build")
            self.assertTrue(live_cache.directory(data, "game-build", "0").is_dir())
            self.assertFalse(live_cache.directory(data, "game-build", "1").exists())
            recent = live_cache.directory(data, "refresh", "0")
            os.utime(recent, (100, 100))
            partial = recent.parent / "abandoned.123.tmp"
            partial.mkdir()
            live_cache.prune(data, "refresh")
            self.assertEqual(
                {p.name for p in recent.parent.iterdir() if p.is_dir()}, {"0", "4", "5"}
            )
            self.assertTrue(live_cache.directory(data, "tile-build", "0").is_dir())

    def test_sandbox_status_warns_at_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.assertEqual(sandbox.cache_warning(home), "cache 0 B")
            (home / "work/cache").mkdir(parents=True)
            (home / "work/cache/small").write_text("cache")
            self.assertIn("WARNING", sandbox.cache_warning(home, limit=1))
