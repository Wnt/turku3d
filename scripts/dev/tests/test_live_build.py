"""Small, offline fixtures for live artifact verification and publication."""

import contextlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import advisory
import live_build
import spec_selection

CORE = live_build.C.CORE_TILES
GATE = spec_selection.gate_config()
# tests/run.py runs cases in threads; patches of the shared live_build.s.run_group must not
# overlap, or one test's build runs the real stage commands.
RUN_GROUP = threading.Lock()


class LiveBuildTests(unittest.TestCase):
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
                work,
                root / "data",
                "commit",
                {"car_validation": {"report": "reports/validate-cars.json"}},
                "key",
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

    def test_seed_drops_facades_retired_from_the_approvals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            published = root / "data"
            approvals = published / "facade-approvals/assembly"
            (approvals / "facades/b").mkdir(parents=True)
            (approvals / "facades/b/1.json").write_text("generated")
            (approvals / "MANIFEST.json").write_text("approved")
            stale = root / "work/textures/assembly/facades/b"
            stale.mkdir(parents=True)
            (stale / "0.json").write_text("retired photo")
            (stale / "1.json").write_text("old")
            live_build.seed(root / "work", published)
            self.assertEqual(sorted(p.name for p in stale.iterdir()), ["1.json"])
            self.assertEqual((stale / "1.json").read_text(), "generated")

    def test_tile_list_is_migrated_and_read_from_the_data_root(self):
        with tempfile.TemporaryDirectory() as temp:
            published = Path(temp)
            self.assertEqual(live_build.tiles(published), live_build.C.CORE_TILES)
            record = json.loads((published / "playable-tiles.json").read_text())
            self.assertEqual(record["level"], 0)
            self.assertEqual(record["core_bbox_en"], [23459500.0, 6704000.0, 23460000.0, 6704500.0])
            live_build.C.write_playable_tiles(
                published / "playable-tiles.json", [*record["tiles"], "t0_2_0"]
            )
            self.assertEqual(live_build.tiles(published)[-1], "t0_2_0")
            (published / "work").mkdir()
            (published / "work/playable-tiles.json").write_text("{}")
            live_build.seed(published / "work", published)
            self.assertFalse((published / "work/playable-tiles.json").exists())

    def test_inspect_checks_every_listed_tile(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            good = {"tris": 5000, "ground_texture_bytes": 1, "wire_bytes": 7_000_000}
            listed = (*live_build.C.CORE_TILES, "t0_2_0")
            visual = dict.fromkeys(live_build.C.CORE_TILES, good)
            manifest.write_text(json.dumps({"visual": visual, "spawn": [0, 0, 0]}))
            with self.assertRaisesRegex(ValueError, "t0_2_0: listed .* not assembled"):
                live_build.inspect(manifest, 1, 1, listed)
            visual["t0_2_0"] = {**good, "wire_bytes": 8_000_000}
            manifest.write_text(json.dumps({"visual": visual, "spawn": [0, 0, 0]}))
            with self.assertRaisesRegex(ValueError, "t0_2_0: L0 wire size exceeds 8 MB"):
                live_build.inspect(manifest, 1, 1, listed)
            visual["t0_2_0"] = good
            manifest.write_text(json.dumps({"visual": visual, "spawn": [0, 0, 0]}))
            self.assertEqual(live_build.inspect(manifest, 1, 1, listed)["tiles"], list(listed))

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

    def stage_fixture(self, root):
        """repo, work and published roots with one assembled manifest of the core tiles."""
        repo, work, published = root / "repo", root / "work", root / "data"
        (repo / "game/tests/e2e-tile").mkdir(parents=True)
        (repo / "game/playwright.tile.config.ts").write_text("export default {};\n")
        (repo / "game/tests/e2e-tile/tile.spec.ts").write_text("test('walks', f);\n")
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
                        for tile in live_build.C.CORE_TILES
                    },
                    "spawn": {"position": [0, 0, 0]},
                }
            )
        )
        return repo, work, published

    def stage_runner(self, work, calls, failing=(), checks=None):
        """A run_group stand-in that records each stage command and fakes its output.

        Stages in `failing` exit 1; `checks` are tile-validate's check statuses."""
        checks = checks or {"format": "pass", "seams": "pass", "buildings": "pass", "curbs": "pass"}

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
                    kwargs["env"].get("TURKU3D_JOBS"),
                )
            )
            if "--checks" in command and "cars" in command:
                (work / "reports").mkdir(exist_ok=True)
                (work / "reports/validate-cars.json").write_text(
                    json.dumps(
                        {
                            "pass": False,
                            "checks": {"cars": {"status": "fail", "perTile": [{"status": "fail"}]}},
                        }
                    )
                )
                return type("Completed", (), {"returncode": 1, "stdout": "cars FAIL\n"})()
            if "tile-validate" in command:
                (work / "reports").mkdir(exist_ok=True)
                selected = next(part for part in command if part.startswith("VALIDATOR_ARGS="))
                included = selected.split("--checks ")[1].split()[0].split(",")
                report = {
                    "checks": {
                        name: {"status": s} for name, s in checks.items() if name in included
                    }
                }
                (work / "reports/validate-aoi.json").write_text(json.dumps(report))
                code = 1 if any(c["status"] == "fail" for c in report["checks"].values()) else 0
                return type("Completed", (), {"returncode": code, "stdout": "validated\n"})()
            stage = next(
                (
                    name
                    for name in failing
                    if name in command or f"turku3d.package.{name}" in command
                ),
                None,
            )
            if stage or (command[0].endswith("playwright") and "chrome" in failing):
                return type("Completed", (), {"returncode": 1, "stdout": "Error: broke\n"})()
            if "tile-build" in command:
                output = '{"facades_bound": 2}\n'
            elif any(part.endswith("facade_ground_audit") for part in command):
                output = '{"sampled": 2, "buried_facades": []}\n'
            else:
                output = "passed\n"
            return type("Completed", (), {"returncode": 0, "stdout": output})()

        return run

    def shipped(self, source="live"):
        """A served tile set (served.tiles) for code builds on the stage fixture."""
        return {
            "dir": "unused",
            "source": source,
            "release": "g" * 40,
            "commit": "g" * 40,
            "tile_commits": {tile: "g" * 40 for tile in CORE},
            "facades_bound": 2,
        }

    def build_and_advise(self, repo, work, published, env, log, **kwargs):
        report = live_build.build(repo, work, published, env, log, **kwargs)
        if kwargs.get("checks", True):
            seconds = report["seconds"]
            report = live_build.post_publish(
                repo,
                work,
                published,
                env,
                log,
                report,
                kind=kwargs.get("kind", "code"),
                previous=kwargs.get("previous_release"),
                chrome=kwargs.get("chrome"),
                cancelled=lambda: False,
            )
            report["seconds"] = {**seconds, **report.get("advisory_seconds", {})}
        return report

    def test_build_runs_real_stages_in_order_and_records_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, work, published = self.stage_fixture(root)
            calls = []
            run = self.stage_runner(work, calls)
            with (
                RUN_GROUP,
                patch.object(live_build.s, "run_group", side_effect=run),
                patch("os.getloadavg", return_value=(12.0, 11.0, 10.0)),
            ):
                report = self.build_and_advise(
                    repo,
                    work,
                    published,
                    {"TURKU3D_WORK": str(work)},
                    root / "build.log",
                    kind="data",
                )
            self.assertEqual(
                [stage for stage in report["seconds"]],
                [
                    "refresh",
                    "tile-build",
                    "tile-validate",
                    "collision-checks",
                    "car-report",
                    "facade-ground-audit",
                    "chrome",
                ],
            )
            self.assertEqual(len(calls), 7)
            self.assertEqual(report["tiles_built"], list(CORE))
            self.assertEqual(
                calls[2][0][:3], ["make", "tile-validate", "T=" + " ".join(live_build.C.CORE_TILES)]
            )
            self.assertIn("TILE_WALKERS=128", calls[2][0])
            self.assertIn("VALIDATOR_REQUIRE=buildings,curbs", calls[3][0])
            self.assertIn("--checks format,seams", " ".join(calls[2][0]))
            self.assertEqual(report["car_validation"]["status"], "fail")
            self.assertEqual(report["car_validation"]["failed_tiles"], 1)
            self.assertEqual(calls[-1][1], repo / "game")
            # Without a selection the full suite runs on both projects; nothing passed, so
            # nothing is cached.
            self.assertEqual(
                calls[-1][0][1:],
                [
                    "test",
                    "-c",
                    "playwright.tile.config.ts",
                    "--reporter=list,json",
                    "tests/e2e-tile/tile.spec.ts",
                    "--project",
                    "webgpu",
                    "--project",
                    "webgl",
                ],
            )
            self.assertEqual(report["chrome"]["cached"], [])
            self.assertEqual(len(report["chrome"]["run"]), 2)
            # Chrome runs with the integration reservation's four workers under load 60.
            self.assertEqual(calls[-1][3], "4")
            self.assertEqual(report["facades_bound"], 2)
            self.assertEqual(list(report["browser_load"]), ["chrome"])
            self.assertEqual(report["browser_load"]["chrome"]["frame_p95_ms"]["max"], 20.0)
            self.assertEqual(report["facade_ground_audit"]["sampled"], 2)
            self.assertEqual(report["advisory"], [])

    def failing_build(self, root, failing=(), statuses=None, **kwargs):
        """A build (data unless kind is given) whose `failing` stages exit 1; returns (report,
        stage commands)."""
        repo, work, published = self.stage_fixture(root)
        kwargs.setdefault("kind", "data")
        calls = []
        with (
            RUN_GROUP,
            patch.object(
                live_build.s,
                "run_group",
                side_effect=self.stage_runner(work, calls, failing, statuses),
            ),
            patch.object(live_build.served, "ship", return_value=self.shipped()),
        ):
            report = self.build_and_advise(
                repo, work, published, {"TURKU3D_WORK": str(work)}, root / "build.log", **kwargs
            )
        return report, calls

    def test_failing_audits_chrome_and_walk_checks_are_advisory(self):
        failing = ("facade_ground_audit", "chrome")
        statuses = {"format": "pass", "seams": "pass", "buildings": "pass", "curbs": "fail"}
        with tempfile.TemporaryDirectory() as temp:
            report, calls = self.failing_build(Path(temp), failing, statuses)
        # Every stage still ran; the tiles are there to publish.
        self.assertEqual(len(calls), 7)
        stages = [entry["stage"] for entry in report["advisory"]]
        self.assertEqual(stages, ["collision-checks", "facade-ground-audit", "chrome"])
        self.assertEqual(report["advisory"][0]["checks"], ["curbs"])
        self.assertEqual(
            report["advisory"][-1]["specs"],
            ["webgpu:game/tests/e2e-tile/tile.spec.ts", "webgl:game/tests/e2e-tile/tile.spec.ts"],
        )
        self.assertIn("facade-ground-audit failed (1)", report["advisory"][1]["detail"])
        self.assertNotIn("facade_ground_audit", report)

    def test_build_stage_or_blocking_tile_check_failure_is_red(self):
        for failing, statuses, kind, stage in (
            (("game-build",), None, "code", "game-build"),
            (("refresh",), None, "data", "refresh"),
            ((), {"format": "pass", "seams": "fail", "buildings": "fail"}, "data", "tile-validate"),
        ):
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(live_build.StageFailed) as caught:
                    self.failing_build(Path(temp), failing, statuses, kind=kind)
            self.assertEqual(caught.exception.stage, stage)
        self.assertIn("seams", str(caught.exception))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "validate-aoi.json"
            with self.assertRaisesRegex(ValueError, "no validator report"):
                advisory.tile_checks(path, 0)
            path.write_text(json.dumps({"checks": {"format": {"status": "pass"}}}))
            with self.assertRaisesRegex(ValueError, "validator exit 2"):
                advisory.tile_checks(path, 2)
            self.assertEqual(advisory.tile_checks(path, 0), [])

    def test_build_stages_only_skip_every_advisory_stage(self):
        self.assertEqual(
            live_build.stages_for("data", checks=False), ("refresh", "tile-build", "tile-validate")
        )
        self.assertEqual(live_build.stages_for("full", checks=False), ("game-build",))
        with tempfile.TemporaryDirectory() as temp:
            report, calls = self.failing_build(Path(temp), ("chrome",), checks=False)
        self.assertEqual(list(report["seconds"]), ["refresh", "tile-build", "tile-validate"])
        self.assertEqual((len(calls), report["advisory"]), (3, []))

    def test_served_tiles_replace_what_a_reused_sandbox_built_before(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            published, work = root / "data", root / "work"
            live = published / "live-releases/g"
            for name in ("layers", "textures", "game", "tiles"):
                (live / name).mkdir(parents=True)
                (live / name / "asset.bin").write_text("live")
                (work / name).mkdir(parents=True)
            (published / "live").symlink_to(live)
            for relative in ("build/layers", "textures", "build/game", "dist/tiles"):
                (work / relative).mkdir(parents=True, exist_ok=True)
                (work / relative / "asset.bin").write_text("an older build")
            shipped = {"source": "live", "dir": str(live / "tiles")}
            with (
                patch.object(live_build.s, "git", return_value="h" * 40),
                patch.object(live_build.served, "tiles", return_value=shipped),
            ):
                self.assertIs(live_build.served.ship(root, work, published), shipped)
            for relative in ("build/layers", "textures", "build/game", "dist/tiles"):
                self.assertEqual((work / relative / "asset.bin").read_text(), "live", relative)

    def test_code_build_runs_only_the_game_bundle_on_the_served_tiles(self):
        # A code batch never rebuilds tiles: one stage, the served set checked and reported.
        self.assertEqual(live_build.stages_for("code"), ("game-build",))
        with tempfile.TemporaryDirectory() as temp:
            report, calls = self.failing_build(Path(temp), kind="code")
        self.assertEqual([call[0] for call in calls], [["make", "game-build"]])
        self.assertEqual(list(report["seconds"]), ["game-build"])
        self.assertEqual((report["tiles"], report["facades_bound"]), (list(CORE), 2))
        self.assertEqual(report["shipped"]["source"], "live")
        self.assertNotIn("dir", report["shipped"])
        self.assertNotIn("tiles_built", report)

    def join_build(self, root, updates, cached=(), kind="code"):
        """Build with a join callback that returns `updates` in turn; stages in `cached` are
        cache hits. Returns (stage commands run, the stages each join call saw, log, report)."""
        repo, work, published = self.stage_fixture(root)
        (repo / ".git").mkdir()
        calls, joins = [], []
        updates = list(updates)

        def join(restored):
            joins.append(restored)
            return updates.pop(0) if updates else None

        with (
            RUN_GROUP,
            patch.object(live_build.s, "run_group", side_effect=self.stage_runner(work, calls)),
            patch.object(live_build, "phase", return_value=contextlib.nullcontext()),
            patch.object(live_build, "store_tiles"),
            patch.object(live_build.served, "ship", return_value=self.shipped()),
            patch.object(live_build.live_cache, "digest", side_effect=lambda *a: f"key-{a[3]}"),
            patch.object(
                live_build.live_cache,
                "restore",
                side_effect=lambda _p, _w, stage, _k: {} if stage in cached else None,
            ),
            patch.object(live_build.live_cache, "store"),
        ):
            timings = {}
            report = live_build.build(
                repo,
                work,
                published,
                {"TURKU3D_WORK": str(work)},
                root / "build.log",
                kind=kind,
                join=join,
                timings=timings,
            )
        stages = [call[0][1] for call in calls if call[0][0] == "make"]
        return stages, joins, (root / "build.log").read_text(), report, timings

    def test_landing_during_a_real_game_build_never_restarts_it(self):
        with tempfile.TemporaryDirectory() as temp:
            # Main had not moved when game-build started; a head landing while it runs is not
            # offered again: the build is frozen once a stage runs for real.
            stages, joins, log, report, _ = self.join_build(Path(temp), [None])
            self.assertEqual(joins, [()])
            self.assertEqual(stages, ["game-build"])
            self.assertNotIn("== join", log)

    def test_landing_before_game_build_joins_and_it_runs_once(self):
        update = {"commit": "m2", "kind": "code", "reason": "joined reason"}
        with tempfile.TemporaryDirectory() as temp:
            stages, joins, log, report, timings = self.join_build(Path(temp), [update])
            # The join is taken before game-build starts: nothing ran yet, so nothing is lost;
            # the rebuild offers once more (main has not moved again) and then runs.
            self.assertEqual(joins, [(), ()])
            self.assertEqual(stages, ["game-build"])
            self.assertLess(
                log.index("== join: rebuilding m2 as code =="), log.rindex("== game-build")
            )
            self.assertEqual(set(timings), set(report["seconds"]))

    def test_cached_game_build_allows_a_join_at_the_first_real_stage(self):
        update = {"commit": "m2", "kind": "full", "reason": None}
        with tempfile.TemporaryDirectory() as temp:
            stages, joins, log, report, timings = self.join_build(
                Path(temp), [update], cached=("game-build",), kind="full"
            )
            # The cached bundle is all a restart repeats; Chrome then runs for real once.
            self.assertEqual(joins, [])
            self.assertEqual(stages, [])
            self.assertEqual(log.count("cache hit key-game-build"), 1)
            self.assertEqual(log.count("== chrome"), 0)
            self.assertEqual((report["cache_hits"], timings["game-build"]), (["game-build"], 0.0))

    def test_data_build_runs_no_unselected_chrome_and_carries_unchanged_car_result(self):
        self.assertEqual(live_build.stages_for("inert"), ())
        self.assertEqual(live_build.stages_for("data", post_publish=True)[-1], "chrome")
        self.assertEqual(live_build.stages_for("full"), ("game-build",))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, work, published = self.stage_fixture(root)
            previous = root / "release"
            (previous / "reports").mkdir(parents=True)
            (previous / "reports/validate-cars.json").write_text('{"previous": true}')
            car = {"status": "fail", "failed_tiles": 3, "report": "reports/validate-cars.json"}
            (previous / "live-build.json").write_text(
                json.dumps({"commit": "g" * 40, "car_validation": car})
            )
            for layers in (previous / "layers", work / "build/layers"):
                (layers / "traffic_static").mkdir(parents=True)
                (layers / "traffic_static/placements.json").write_text("[1]")

            def build():
                calls = []
                with (
                    RUN_GROUP,
                    patch.object(
                        live_build.s, "run_group", side_effect=self.stage_runner(work, calls)
                    ),
                ):
                    report = self.build_and_advise(
                        repo,
                        work,
                        published,
                        {"TURKU3D_WORK": str(work)},
                        root / "build.log",
                        previous_release=previous,
                        kind="data",
                        chrome=spec_selection.select([], "data", root=repo, config=GATE),
                    )
                return report, calls

            report, calls = build()
            self.assertEqual(
                list(report["seconds"]),
                [
                    "refresh",
                    "tile-build",
                    "tile-validate",
                    "collision-checks",
                    "facade-ground-audit",
                    "chrome",
                ],
            )
            # The unchanged tile list selects no spec, so Chrome never starts.
            self.assertEqual(len(calls), 5)
            self.assertEqual((report["chrome"]["selected"], report["chrome"]["run"]), ([], []))
            self.assertEqual(report["car_validation"], {**car, "carried_from": "g" * 40})
            self.assertEqual(
                (work / "reports/validate-cars.json").read_text(), '{"previous": true}'
            )
            self.assertIn("parked cars unchanged", (root / "build.log").read_text())
            # Changed parked cars run the car report again, still without a browser.
            (work / "build/layers/traffic_static/placements.json").write_text("[2]")
            report, calls = build()
            self.assertIn("car-report", report["seconds"])
            self.assertNotIn("carried_from", report["car_validation"])
            self.assertEqual(len(calls), 6)

    def test_car_report_rejects_missing_or_inconsistent_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cars.json"
            with self.assertRaisesRegex(ValueError, "report missing"):
                advisory.car_report(path, 1)
            path.write_text(json.dumps({"pass": False, "checks": {"cars": {"status": "fail"}}}))
            with self.assertRaisesRegex(ValueError, "exit code disagrees"):
                advisory.car_report(path, 0)
            with self.assertRaisesRegex(ValueError, "validator error"):
                advisory.car_report(path, 2)

    def test_inspect_requires_bound_ground_budget_and_reports_a_facade_drop(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            manifest = {
                "visual": {
                    tile: {"ground_texture_bytes": 1, "wire_bytes": 7999999, "tris": 1000}
                    for tile in live_build.C.CORE_TILES
                },
                "spawn": {"position": [0, 0, 0]},
            }
            path.write_text(json.dumps(manifest))
            self.assertEqual(live_build.inspect(path, 4, 4, CORE)["facades_bound"], 4)
            self.assertIsNone(advisory.facade_drop(live_build.inspect(path, 4, 4, CORE)))
            drop = advisory.facade_drop(live_build.inspect(path, 3, 4, CORE))
            self.assertEqual((drop["stage"], drop["facades_bound"]), ("facade-baseline", 3))
            self.assertIn("facades_bound regressed: 3 < 4", drop["detail"])
            reported = live_build.inspect(path, 3, 4, CORE, "source removed")
            self.assertEqual(reported["facade_regression_reason"], "source removed")
            self.assertIsNone(advisory.facade_drop(reported))
            manifest["visual"][live_build.C.CORE_TILES[0]]["ground_texture_bytes"] = 0
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "ground textures"):
                live_build.inspect(path, 4, 4, CORE)
            manifest["visual"][live_build.C.CORE_TILES[0]].update(
                ground_texture_bytes=1, wire_bytes=8000000
            )
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "8 MB"):
                live_build.inspect(path, 4, 4, CORE)

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
