"""Small, offline fixtures for live artifact verification and publication."""

import contextlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_build


class LiveBuildTests(unittest.TestCase):
    def test_changed_collision_tiles_are_selected_individually(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "prior/tiles/collision/t0_0_0"
            new = root / "work/dist/tiles/collision/t0_0_0"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            for name in ("c_0_0.col", "c_0_1.col"):
                (old / name).write_bytes(b"same")
                (new / name).write_bytes(b"same")
            (new / "c_0_1.col").write_bytes(b"changed")
            self.assertEqual(
                live_build.affected_collision_files(root / "prior", root / "work", ("t0_0_0",)),
                (new / "c_0_1.col",),
            )
            (new / "c_0_1.col").write_bytes(b"same")
            self.assertEqual(
                len(
                    live_build.affected_collision_files(root / "prior", root / "work", ("t0_0_0",))
                ),
                2,
            )

    def test_prepared_release_snapshots_nested_symlink_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root / "work"
            for relative in ("build/game", "dist/tiles", "build/layers", "textures"):
                (work / relative).mkdir(parents=True)
            (work / "textures/asset.bin").write_bytes(b"stable")
            (work / "textures/link.bin").symlink_to("asset.bin")
            (work / "reports").mkdir()
            (work / "reports/validate-cars.json").write_text('{"pass":false}')
            release = live_build.prepare(
                work, root / "data", "commit",
                {"car_validation": {"report": "reports/validate-cars.json"}}, "key",
            )
            self.assertFalse((release / "textures/link.bin").is_symlink())
            self.assertEqual((release / "textures/link.bin").read_bytes(), b"stable")
            self.assertEqual((release / "reports/validate-cars.json").read_text(), '{"pass":false}')
            live_build.prepared(root / "data", "commit", "key")

    def test_seed_approvals_never_write_through_shared_texture_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            published = root / "data"
            shared = published / "textures"
            (shared / "assembly").mkdir(parents=True)
            (shared / "assembly/MANIFEST.json").write_text("shared")
            (published / "facade-approvals/assembly").mkdir(parents=True)
            (published / "facade-approvals/assembly/MANIFEST.json").write_text("approved")
            work = root / "work"
            work.mkdir()
            (work / "textures").symlink_to(shared, target_is_directory=True)
            live_build.seed(work, published)
            self.assertFalse((work / "textures").is_symlink())
            self.assertEqual((work / "textures/assembly/MANIFEST.json").read_text(), "approved")
            self.assertEqual((shared / "assembly/MANIFEST.json").read_text(), "shared")

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

    def test_browser_load_summarizes_frame_time_and_load_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            rows = Path(temp) / "chrome.jsonl"
            pace = {"p95FrameMs": 16.7, "scale": 1}
            rows.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in (
                        {
                            "project": "webgl",
                            "file": "parked.spec.ts",
                            "title": "drives",
                            "retry": 0,
                            "status": "timedOut",
                            "hostLoad": 63.2,
                            "pace": {**pace, "p95FrameMs": 3046.1},
                        },
                        {
                            "project": "webgl",
                            "file": "parked.spec.ts",
                            "title": "drives",
                            "retry": 1,
                            "status": "passed",
                            "hostLoad": 41.0,
                            "pace": pace,
                        },
                        {
                            "project": "webgpu",
                            "file": "tile.spec.ts",
                            "title": "walks",
                            "retry": 0,
                            "status": "passed",
                            "hostLoad": 12.0,
                            "pace": None,
                        },
                    )
                )
                + "\nnot json\n"
            )
            with patch.object(live_build.os, "getloadavg", return_value=(50.0, 40.0, 30.0)):
                load = live_build.browser_load(rows, (60.0, 45.0, 20.0))
            self.assertEqual(load["host_load_start"], [60.0, 45.0, 20.0])
            self.assertEqual(load["frame_p95_ms"], {"median": 3046.1, "max": 3046.1})
            self.assertEqual(load["attempts"], 3)
            self.assertEqual(
                load["retried"],
                [{"test": "[webgl] parked.spec.ts drives", "status": "passed", "host_load": 41.0}],
            )
            summary = live_build.load_summary(load)
            self.assertIn("host load 50.00, 40.00, 30.00 (start 60.00)", summary)
            self.assertIn("max 3046.1 ms; 1 load retries", summary)
            missing = live_build.browser_load(Path(temp) / "missing.jsonl", (1.0, 1.0, 1.0))
            self.assertIsNone(missing["frame_p95_ms"])
            self.assertIn("frame p95 unmeasured", live_build.load_summary(missing))

    def test_failure_detail_reports_playwright_timeout_not_closed_page(self):
        output = """
  2 failed
  1) [webgl] › tests/e2e-tile/integration.spec.ts:86:1 › office facades

    Test timeout of 120000ms exceeded.

    Error: page.waitForFunction: Target page, context or browser has been closed

  2) [webgl] › tests/e2e-tile/parked.spec.ts:8:1 › parked car

    TimeoutError: page.waitForFunction: Timeout 60000ms exceeded.
"""
        self.assertEqual(
            live_build.s.failure_detail(output),
            "1) [webgl] › tests/e2e-tile/integration.spec.ts:86:1 › office facades: "
            "Test timeout of 120000ms exceeded.",
        )
        self.assertEqual(live_build.s.failure_detail("ok\nFAILED test_x\n"), "FAILED test_x")

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
            started = time.monotonic()

            def run(command, **kwargs):
                load_log = kwargs["env"].get("TURKU3D_BROWSER_LOAD_LOG")
                if load_log:
                    Path(load_log).write_text(
                        json.dumps(
                            {
                                "project": "webgpu",
                                "file": "tile.spec.ts",
                                "title": "walks",
                                "retry": 0,
                                "status": "passed",
                                "hostLoad": 1.0,
                                "pace": {"p95FrameMs": 20.0},
                            }
                        )
                        + "\n"
                    )
                calls.append(
                    (
                        command,
                        kwargs["cwd"],
                        kwargs["env"]["TILE_DIST"],
                        float(kwargs["env"]["TURKU3D_CHECK_DEADLINE"]),
                    )
                )
                if "--checks" in command and "cars" in command:
                    (work / "reports").mkdir(exist_ok=True)
                    (work / "reports/validate-cars.json").write_text(
                        json.dumps({"pass": False, "checks": {"cars": {"status": "fail", "perTile": [{"status": "fail"}]}}})
                    )
                    return type("Completed", (), {"returncode": 1, "stdout": "cars FAIL\n"})()
                output = '{"facades_bound": 2}\n' if "tile-build" in command else "passed\n"
                return type("Completed", (), {"returncode": 0, "stdout": output})()

            with (
                patch.object(live_build.s, "run_group", side_effect=run),
                patch.object(live_build, "slot", return_value=contextlib.nullcontext()),
                patch.object(live_build, "identity", return_value="test-process"),
            ):
                report = live_build.build(
                    repo,
                    work,
                    published,
                    {"TURKU3D_WORK": str(work), "TURKU3D_CHECK_DEADLINE": str(started + 1000)},
                    root / "build.log",
                )
            self.assertEqual(
                [stage for stage in report["seconds"]],
                [
                    "refresh",
                    "tile-build",
                    "tile-validate",
                    "car-report",
                    "game-build",
                    "collision-sweep",
                    "collision-audit",
                    "chrome",
                ],
            )
            self.assertEqual(len(calls), 8)
            self.assertEqual(
                calls[2][0][:3], ["make", "tile-validate", "T=" + " ".join(live_build.TILES)]
            )
            self.assertIn("TILE_WALKERS=128", calls[2][0])
            self.assertIn("VALIDATOR_REQUIRE=buildings,curbs", calls[2][0])
            self.assertIn("--checks format,seams,buildings,curbs --walk-seconds 2", " ".join(calls[2][0]))
            self.assertEqual(report["car_validation"]["status"], "fail")
            self.assertEqual(report["car_validation"]["failed_tiles"], 1)
            self.assertLess(calls[2][3] - started, 121)
            self.assertEqual(calls[-1][1], repo / "game")
            self.assertLess(calls[0][3] - started, 76)
            self.assertGreater(calls[0][3] - started, 74)
            self.assertEqual(report["facades_bound"], 2)
            self.assertEqual(list(report["browser_load"]), ["chrome"])
            self.assertEqual(report["browser_load"]["chrome"]["frame_p95_ms"]["max"], 20.0)

    def test_car_report_rejects_missing_or_inconsistent_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cars.json"
            with self.assertRaisesRegex(ValueError, "report missing"):
                live_build.inspect_car_report(path, 1)
            path.write_text(json.dumps({"pass": False, "checks": {"cars": {"status": "fail"}}}))
            with self.assertRaisesRegex(ValueError, "exit code disagrees"):
                live_build.inspect_car_report(path, 0)
            with self.assertRaisesRegex(ValueError, "validator error"):
                live_build.inspect_car_report(path, 2)

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

    def test_prepare_dereferences_nested_shared_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, published = root / "work", root / "data"
            for relative in ("build/game", "dist/tiles", "build/layers", "textures"):
                (work / relative).mkdir(parents=True)
            shared_ground = published / "shared-ground"
            shared_ground.mkdir(parents=True)
            (published / "build").mkdir()
            (published / "dist").mkdir()
            (shared_ground / "mesh").write_text("ground")
            (work / "build/layers/ground").symlink_to(shared_ground, target_is_directory=True)
            (work / "textures/ground").symlink_to(shared_ground, target_is_directory=True)
            release = live_build.prepare(work, published, "abc", {"facades_bound": 1})
            self.assertFalse((release / "layers/ground").is_symlink())
            self.assertFalse((release / "textures/ground").is_symlink())
            self.assertEqual((release / "layers/ground/mesh").read_text(), "ground")
            live_build.publish(published, "abc")
            self.assertEqual((published / "build/layers/ground/mesh").read_text(), "ground")

    def test_publish_rolls_back_when_stable_path_does_not_resolve_to_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, published = root / "work", root / "data"
            for relative in ("build/game", "dist/tiles", "build/layers", "textures"):
                (work / relative).mkdir(parents=True)
            previous = published / "live-releases/old"
            previous.mkdir(parents=True)
            (published / "live").symlink_to(previous, target_is_directory=True)
            (published / "build").mkdir()
            (published / "build/game").symlink_to(published / "live/game", target_is_directory=True)
            (published / "build/layers").symlink_to(
                published / "build/layers", target_is_directory=True
            )
            (published / "dist").mkdir()
            (published / "dist/tiles").symlink_to(
                published / "live/tiles", target_is_directory=True
            )
            (published / "textures").symlink_to(
                published / "live/textures", target_is_directory=True
            )
            live_build.prepare(work, published, "abc", {"facades_bound": 1})
            with self.assertRaisesRegex(ValueError, "stable path"):
                live_build.publication_preflight(published, "abc")
            with self.assertRaisesRegex(ValueError, "stable path"):
                live_build.publish(published, "abc")
            self.assertEqual((published / "live").resolve(), previous.resolve())


if __name__ == "__main__":
    unittest.main()
