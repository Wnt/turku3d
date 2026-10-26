"""Small, offline fixtures for live artifact verification and publication."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_build


class LiveBuildTests(unittest.TestCase):
    def test_layer_delta_maps_collision_and_visual_to_only_affected_tiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "release/layers/ground"
            new = root / "work/build/layers/ground"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            for directory in (old, new):
                (directory / "t0_0_0.vis.glb").write_bytes(b"same")
                (directory / "t0_1_0.vis.glb").write_bytes(b"same")
                (directory / "c_4_1.col.npz").write_bytes(b"same")
            (new / "t0_1_0.vis.glb").write_bytes(b"changed")
            (new / "c_4_1.col.npz").write_bytes(b"changed")
            self.assertEqual(
                live_build.affected_tiles(root / "release", root / "work"), ("t0_1_0",)
            )
            (new / "unmapped.bin").write_bytes(b"unknown")
            self.assertEqual(
                live_build.affected_tiles(root / "release", root / "work"), live_build.TILES
            )

    def test_warm_checks_select_both_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            scripts = repo / "scripts/dev"
            scripts.mkdir(parents=True)
            (scripts / "warm_browser.mjs").touch()
            (scripts / "affected_browser.py").touch()
            state = root / "warm-browser.json"
            state.write_text("{}")
            env = {
                "TURKU3D_WARM_BROWSER_STATE": str(state),
                "TURKU3D_BROWSER_DIR": str(root / "browser"),
                "TURKU3D_PORT_BASE": "5200",
            }
            result = type(
                "Result", (), {"stdout": json.dumps({"tile": ["tests/e2e-tile/office.spec.ts"]})}
            )()
            with patch.object(live_build.subprocess, "run", return_value=result):
                commands, scopes = live_build.warm_checks(repo, root / "work", env)
            self.assertEqual([row[0] for row in commands], ["chrome-webgpu", "chrome-webgl"])
            self.assertEqual(scopes["chrome-webgpu"], ["tests/e2e-tile/office.spec.ts"])
            self.assertIn("--service-state", commands[0][1])

    def test_legacy_baseline_uses_last_published_assembler_result(self):
        with tempfile.TemporaryDirectory() as temp:
            published = Path(temp) / "data"
            (published / "dist/tiles").mkdir(parents=True)
            logs = published.parent / "logs"
            logs.mkdir()
            (logs / "deploy-once.log").write_text(
                json.dumps(
                    {"manifest": str(published / "dist/tiles/manifest.json"), "facades_bound": 164}
                )
                + "\n"
            )
            self.assertEqual(live_build.baseline(published), 164)

    def test_build_runs_real_stages_in_order_and_records_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, work, published = root / "repo", root / "work", root / "data"
            repo.mkdir()
            (published / "textures/assembly").mkdir(parents=True)
            (published / "textures/assembly/MANIFEST.json").write_text(
                json.dumps({"bound": ["one", "two"]})
            )
            (work / "dist/tiles").mkdir(parents=True)
            (work / "dist/tiles/manifest.json").write_text(
                json.dumps(
                    {
                        "visual": {
                            tile: {"ground_texture_bytes": 1, "wire_bytes": 100, "tris": 1000}
                            for tile in live_build.TILES
                        },
                        "spawn": {"position": [0, 0, 0]},
                    }
                )
            )
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs["cwd"], kwargs["env"]["TILE_DIST"]))
                output = '{"facades_bound": 2}\n' if "tile-build" in command else "passed\n"
                return type("Completed", (), {"returncode": 0, "stdout": output})()

            with patch.object(live_build.s, "run_group", side_effect=run):
                report = live_build.build(
                    repo, work, published, {"TURKU3D_WORK": str(work)}, root / "build.log"
                )
            self.assertEqual(
                [stage for stage in report["seconds"]],
                [
                    "refresh",
                    "tile-build",
                    "game-build",
                    "collision-sweep",
                    "collision-audit",
                    "chrome",
                ],
            )
            self.assertEqual(len(calls), 6)
            self.assertEqual(calls[-1][1], repo / "game")
            self.assertEqual(report["facades_bound"], 2)

    def test_inspect_requires_bound_ground_budget_and_facade_floor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            manifest = {
                "visual": {
                    tile: {"ground_texture_bytes": 1, "wire_bytes": 7999999, "tris": 1000}
                    for tile in live_build.TILES
                },
                "spawn": {"position": [0, 0, 0]},
            }
            path.write_text(json.dumps(manifest))
            self.assertEqual(live_build.inspect(path, 4, 4)["facades_bound"], 4)
            with self.assertRaisesRegex(ValueError, "facades_bound regressed"):
                live_build.inspect(path, 3, 4)
            self.assertEqual(
                live_build.inspect(path, 3, 4, "source removed")["facade_regression_reason"],
                "source removed",
            )
            manifest["visual"][live_build.TILES[0]]["ground_texture_bytes"] = 0
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "ground textures"):
                live_build.inspect(path, 4, 4)
            manifest["visual"][live_build.TILES[0]].update(
                ground_texture_bytes=1, wire_bytes=8000000
            )
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "8 MB"):
                live_build.inspect(path, 4, 4)

    def test_publish_switches_single_live_link_and_retains_previous_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, published = root / "work", root / "data"
            for base, marker in ((work, "new"), (published, "old")):
                for relative in ("build/game", "build/layers", "dist/tiles", "textures"):
                    directory = base / relative
                    directory.mkdir(parents=True)
                    (directory / "marker").write_text(marker)
            live_build.prepare(work, published, "abc", {"facades_bound": 7})
            live_build.publish(published, "abc")
            self.assertEqual((published / "build/game/marker").read_text(), "new")
            self.assertEqual((published / "dist/tiles/marker").read_text(), "new")
            self.assertEqual((published / "live-releases/legacy/game/marker").read_text(), "old")
            self.assertEqual(live_build.baseline(published), 7)
            self.assertEqual((root / "logs/live-version.txt").read_text(), "abc\n")


if __name__ == "__main__":
    unittest.main()
