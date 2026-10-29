"""Disk retention on tiny synthetic directories; no builds, servers or real caches."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_cache
import sandbox

SERIAL = True


class DiskRetentionTests(unittest.TestCase):
    def test_stage_retention_is_per_stage_and_preserves_recent_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            for stage in ("refresh", "tile-build", "game-build"):
                for index in range(6):
                    path = live_cache.directory(data, stage, str(index))
                    path.mkdir(parents=True)
                    (path / "result.json").write_text("{}")
                    if stage == "game-build":
                        for relative in live_cache.OUTPUTS[stage]:
                            (path / relative).mkdir(parents=True)
                            (path / relative / "file").write_text(str(index))
                    os.utime(path, (index + 1, index + 1))
            self.assertEqual(live_cache.restore(data, data / "work", "game-build", "0"), {})
            self.assertEqual((data / "work/build/game/file").read_text(), "0")
            self.assertEqual((data / "work/build/check-game/file").read_text(), "0")
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

    def test_reports_merge_keeps_previous_evidence_and_rewrites_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "sandbox"
            source = home / "work/reports"
            target = root / "tasks/pilot/reports"
            for path in (source / "nested", target / "nested"):
                path.mkdir(parents=True)
            (source / "nested/photo.jpg").write_bytes(b"new")
            (target / "nested/photo.jpg").write_bytes(b"old")
            (target / "retained.txt").write_text("keep")
            (source / "result.md").write_text(f"[Photo]({source}/nested/photo.jpg)")
            with patch.object(sandbox, "tasks_root", return_value=root / "tasks"):
                sandbox.preserve_reports("pilot", home)
                sandbox.preserve_reports("pilot", home)  # Safe retry after a later cleanup failure.
            self.assertFalse(source.exists())
            self.assertEqual((target / "nested/photo.jpg").read_bytes(), b"new")
            self.assertEqual((target / "retained.txt").read_text(), "keep")
            self.assertEqual(
                (target / "result.md").read_text(), f"[Photo]({target}/nested/photo.jpg)"
            )
            previous = list((target.parent / "reports-history").glob("*/nested/photo.jpg"))
            self.assertEqual([p.read_bytes() for p in previous], [b"old"])
