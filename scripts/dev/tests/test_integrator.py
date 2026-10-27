"""Synthetic integration batches: classes, publish, re-point, base check, bisect, facades,
data lane, recomposition and advisory checks (lanes started: test_integration_ensure.py)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import advisory  # noqa: E402
import coordination  # noqa: E402
import integrator  # noqa: E402
import landing_class  # noqa: E402
import live_build  # noqa: E402
import pages  # noqa: E402
import served  # noqa: E402

# The cases patch os.environ: tests/run.py runs them one by one in a process of their own.
SERIAL = True
GATE = "#!/bin/sh\necho PASS fast-gate 0.01s\n"
REPO = Path(__file__).resolve().parents[3]
CORE = list(live_build.C.CORE_TILES)
SPECS = {
    "game/tests/e2e-tile/tile.spec.ts": "test('walks', { tag: ['@walk', '@streaming'] }, f);\n",
    "game/tests/e2e-tile/integration.spec.ts": "test('office', { tag: ['@render'] }, f);\n",
}


class IntegratorFixture(unittest.TestCase):
    """A synthetic main, published root and integration sandboxes; no tests of its own."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.wt = self.root / "wt"
        self.tasks = self.root / "tasks"
        self.published = self.root / "data"
        env = {
            "TURKU3D_WT_ROOT": str(self.wt),
            "TURKU3D_TASKS_ROOT": str(self.tasks),
            "TURKU3D_COORD_ROOT": str(self.root / "coord"),
            "TURKU3D_SLOT_DIR": str(self.root / "slots"),
            "TURKU3D_SANDBOX": "main",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Every ledger, status and lock path goes through registry(): it refuses to leave
        # the temporary root, so a lost environment patch fails instead of writing live rows.
        real_registry = integrator.registry

        def registry():
            path = real_registry()
            self.assertTrue(path.is_relative_to(self.root), f"real registry reached: {path}")
            return path

        guard = patch.object(integrator, "registry", side_effect=registry)
        guard.start()
        self.addCleanup(guard.stop)
        self.main = self.root / "origin"
        self.main.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "integrator@example.test")
        self.git("config", "user.name", "Integrator Test")
        (self.main / "scripts/dev").mkdir(parents=True)
        (self.main / "scripts/dev/gate").write_text(GATE)
        (self.main / "scripts/dev/gate").chmod(0o755)
        (self.main / "scripts/dev/wt").write_text("#!/bin/sh\necho env in sync\n")
        (self.main / "scripts/dev/wt").chmod(0o755)
        (self.main / "game").mkdir()
        (self.main / "game/facades.txt").write_text("2\n")
        # The real selection map over two synthetic tile specs.
        shutil.copy(REPO / "scripts/dev/gate.toml", self.main / "scripts/dev/gate.toml")
        for name, text in SPECS.items():
            (self.main / name).parent.mkdir(parents=True, exist_ok=True)
            (self.main / name).write_text(text)
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        for lane in (integrator.NAME, integrator.DATA_NAME):
            home = self.wt / lane
            home.mkdir(parents=True)
            self.git("worktree", "add", "-q", "--detach", str(home / "repo"), "main")
            (home / "sandbox.env").write_text(f"export TURKU3D_WORK={home / 'work'}\n")
        self.release(self.base)
        self.builds = []
        self.classes = []
        self.plans = []
        # Hooks run inside the next build: `preparing` before its join point (seed,
        # refresh), `during` after it (the first expensive stage has started).
        self.preparing = []
        self.during = []
        # Builds whose Chrome stage still fails (advisory), and whether bisection asked
        # for the build stages only.
        self.chrome_red = 0
        self.checks = []

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.main, check=True, text=True, capture_output=True
        ).stdout.strip()

    def approvals(self):
        return self.published / "facade-approvals/assembly/MANIFEST.json"

    def approve(self, walls):
        self.approvals().parent.mkdir(parents=True, exist_ok=True)
        self.approvals().write_text(json.dumps({"bound": [f"wall-{n}" for n in range(walls)]}))

    def outputs(self, work, facades):
        for name in ("build/game", "dist/tiles", "build/layers", "textures"):
            (work / name).mkdir(parents=True, exist_ok=True)
            (work / name / "asset.bin").write_text(str(facades))
        # live_build.seed replaces the release assembly with the approval root.
        (work / "textures/assembly").mkdir(exist_ok=True)
        (work / "textures/assembly/MANIFEST.json").write_bytes(self.approvals().read_bytes())
        visual = {
            tile: {"tris": 1000, "ground_texture_bytes": 1, "wire_bytes": 1}
            for tile in live_build.tiles(self.published)
        }
        manifest = work / "dist/tiles/manifest.json"
        manifest.write_text(json.dumps({"visual": visual, "spawn": [0, 0, 0]}))
        return manifest

    def release(self, commit):
        self.approve(2)
        work = self.root / "seed"
        self.outputs(work, 2)
        report = {"facades_bound": 2, "seconds": {}, "cache_hits": [], "advisory": []}
        for parent in ("build", "dist"):
            (self.published / parent).mkdir(parents=True, exist_ok=True)
        live_build.prepare(work, self.published, commit, report)
        live_build.publish(self.published, commit)

    def fake_build(
        self,
        repo,
        work,
        published,
        env,
        log,
        reason=None,
        previous=None,
        kind="code",
        join=None,
        chrome=None,
        timings=None,
        checks=True,
    ):
        """Stands in for the real stages of live_build.build.

        A code or full build ships the live release's tiles and facade assembly with a new
        game bundle (red on game/bad.ts or a data root marked game-broken); a data build
        rebuilds the tiles from the approval root with the checkout's tile code (red on a
        data root marked raw-broken, or marked needs-new-code without pipeline/new.py) and
        carries the live game. Before its first real stage it calls join with no restored
        stages and then rebuilds the joined head; `self.during` runs as that stage starts.
        Advisory failures: game/full-red.ts fails the full Chrome suite, `chrome_red` the
        next builds' Chrome stage, and a facade drop without reason the facade baseline.
        """
        if self.preparing:
            self.preparing.pop(0)()
        if join and (update := join(())):
            kind = update["kind"]
            chrome = update.get("chrome", chrome)
        self.plans.append(chrome)
        self.checks.append(checks)
        self.assertEqual(previous, (published / "live").resolve())
        self.builds.append(self.git("rev-parse", "HEAD", cwd=repo))
        self.classes.append(kind)
        if self.during:
            self.during.pop(0)()
        stage = "tile-build" if kind == "data" else "game-build"
        log.write_text(f"== {stage}: make {stage} ==\n")
        timings[stage] = timings.get(stage, 0.0) + 0.01
        record = json.loads((previous / "live-build.json").read_text())
        if kind == "data":
            if (published / "raw-broken").exists() or (
                (published / "needs-new-code").exists() and not (repo / "pipeline/new.py").exists()
            ):
                raise live_build.StageFailed("refresh", f"refresh failed (1); {log}")
            # The assembler binds the walls the code places that the approval root still holds.
            approved = len(json.loads(self.approvals().read_text())["bound"])
            facades = min(int((repo / "game/facades.txt").read_text()), approved)
            manifest = self.outputs(work, facades)
            shutil.rmtree(work / "build/game")
            shutil.copytree(previous / "game", work / "build/game")
            listed = live_build.tiles(published)
        else:
            if (repo / "game/bad.ts").exists() or (published / "game-broken").exists():
                raise live_build.StageFailed("game-build", f"game-build failed (2); {log}")
            for name, source in (("dist/tiles", "tiles"), ("build/layers", "layers")):
                shutil.rmtree(work / name, ignore_errors=True)
                shutil.copytree(previous / source, work / name)
            shutil.rmtree(work / "textures", ignore_errors=True)
            shutil.copytree(previous / "textures", work / "textures")
            (work / "build/game").mkdir(parents=True, exist_ok=True)
            (work / "build/game/asset.bin").write_text(self.git("rev-parse", "HEAD", cwd=repo))
            manifest = work / "dist/tiles/manifest.json"
            facades = record["facades_bound"]
            listed = landing_class.released_tiles(previous)
        report = live_build.inspect(
            manifest, facades, live_build.baseline(published), listed, reason
        )
        failures = [drop] if (drop := advisory.facade_drop(report)) else []
        if kind == "data":
            report["tiles_built"] = list(listed)
        else:
            report["shipped"] = {
                "source": "live",
                "release": previous.name,
                "commit": served.builder(record),
                "tile_commits": served.release_tiles(previous),
            }
        return {
            **report,
            "seconds": {stage: 0.01},
            "cache_hits": ["refresh"] if kind == "data" else [],
            "chrome": None,
            "advisory": failures,
        }

    def fake_advise(self, repo, work, published, env, log, report, *, chrome, **kwargs):
        report["chrome"] = chrome and {
            "full_suite": chrome["full_suite"],
            "selected": chrome["specs"],
        }
        failures = report["advisory"]
        full_red = (repo / "game/full-red.ts").exists() and chrome and chrome["full_suite"]
        if self.chrome_red or full_red:
            self.chrome_red = max(0, self.chrome_red - 1)
            spec = "webgpu:game/tests/e2e-tile/tile.spec.ts"
            failures.append(
                {"stage": "chrome", "detail": f"chrome failed (1); {log}", "specs": [spec]}
            )
        return report

    def land(self, name, path, text, reason=None, when=None):
        (self.main / path).parent.mkdir(parents=True, exist_ok=True)
        (self.main / path).write_text(text)
        self.git("add", ".")
        self.git("commit", "-q", "-m", name)
        commit = self.git("rev-parse", "HEAD")
        (self.tasks / name).mkdir(parents=True, exist_ok=True)
        (self.tasks / name / "meta.json").write_text(json.dumps({"state": "done"}))
        row = {"name": name, "commit": commit, "facade_regression_reason": reason}
        if when is not None:
            row["time"] = when
        ledger = self.wt / ".registry/landed.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as file:
            file.write(json.dumps(row) + "\n")
        return commit

    def integrate(self, full=False):
        with (
            patch.object(live_build, "build", side_effect=self.fake_build),
            patch.object(live_build, "post_publish", side_effect=self.fake_advise),
        ):
            return integrator.run_once(self.published, self.main, full=full)

    def data(self, refresh_stale=False):
        with (
            patch.object(live_build, "build", side_effect=self.fake_build),
            patch.object(live_build, "post_publish", side_effect=self.fake_advise),
        ):
            return integrator.run_data(self.published, self.main, refresh_stale=refresh_stale)

    def live_record(self):
        return json.loads((self.published / "live/live-build.json").read_text())

    def pages(self):
        (self.published / "publish").mkdir(exist_ok=True)
        (self.published / pages.CONFIG).write_text(json.dumps({"repo": "o/site"}))
        return patch.object(pages, "publish", return_value={"url": "https://o.github.io/site/"})


class IntegratorTests(IntegratorFixture):
    def test_green_batch_publishes_main_head(self):
        self.land("one", "game/a.ts", "a")
        head = self.land("two", "game/b.ts", "b")
        row = self.integrate()
        self.assertEqual((row["result"], row["from"], row["to"]), ("green", self.base, head))
        self.assertEqual(integrator.live_commit(self.published), head)
        # Only the game bundle was built; the release ships the live tiles and names them.
        self.assertEqual((row["builds"], self.classes, row["facades_bound"]), (1, ["code"], 2))
        self.assertEqual((row["stage_seconds"], row["tiles_from"]), ({"game-build": 0.01}, "live"))
        record = self.live_record()
        self.assertEqual(
            record["tiles_from"],
            {"source": "live", "release": self.base, "commit": self.base},
        )
        self.assertEqual(record["tile_commits"], {tile: self.base for tile in CORE})
        # A game-only batch leaves the tile code alone: nothing is stale.
        self.assertEqual(record["stale_by_code"], [])
        verified = self.published / "live-releases" / self.base
        self.assertTrue(live_build.same_tree(verified / "tiles", self.published / "live/tiles"))
        self.assertEqual((self.published / "live/game/asset.bin").read_text(), head)
        self.assertEqual(integrator.last(self.published), row)
        self.assertIsNone(self.integrate())

    def test_inert_batch_repoints_live_to_main_without_a_build(self):
        self.land("docs", "docs/notes.md", "prose")
        head = self.land("readme", "game/README.md", "prose")
        (self.published / "publish").mkdir()
        (self.published / pages.CONFIG).write_text(json.dumps({"repo": "o/site"}))
        with patch.object(pages, "publish") as publish:
            row = self.integrate()
        publish.assert_not_called()
        self.assertEqual((row["result"], row["class"], row["skipped"]), ("green", "inert", True))
        self.assertEqual((row["to"], row["changed_paths"], row["tiles"]), (head, 2, []))
        self.assertEqual(self.builds, [])
        self.assertEqual(integrator.live_commit(self.published), head)
        release = self.published / "live-releases" / head
        self.assertEqual((self.published / "live").resolve(), release)
        record = json.loads((release / "live-build.json").read_text())
        self.assertEqual(
            (record["verified_commit"], record["repointed_from"]), (self.base, self.base)
        )
        # The release content is the verified one, byte for byte.
        verified = self.published / "live-releases" / self.base
        self.assertTrue(live_build.same_tree(verified / "game", release / "game"))
        self.assertTrue(live_build.same_tree(verified / "textures", release / "textures"))
        self.assertIsNone(self.integrate())
        # A second inert batch keeps naming the build that verified the content.
        later = self.land("licence", "docs/published-images.toml", "[images]\n")
        with patch.object(pages, "publish", return_value={"url": "u"}) as publish:
            row = self.integrate()
        publish.assert_called_once_with(self.published)
        self.assertEqual((row["class"], row["pages"]), ("inert", {"url": "u"}))
        record = json.loads((self.published / "live/live-build.json").read_text())
        self.assertEqual((record["commit"], record["verified_commit"]), (later, self.base))

    def test_changed_approvals_are_the_data_lanes_and_inert_paths_repoint(self):
        head = self.land("note", "docs/reports/facades.md", "report")
        self.approve(3)
        # The code lane classifies by paths only: prose re-points live, no build.
        row = self.integrate()
        self.assertEqual((row["class"], row["data_delta"], self.classes), ("inert", [], []))
        self.assertTrue(integrator.data_due(self.published))
        row = self.data()
        self.assertEqual((row["class"], row["data_delta"]), ("data", ["facade-approvals"]))
        self.assertEqual(row["tiles"], list(CORE))
        self.assertEqual((row["result"], self.classes), ("green", ["data"]))
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(self.live_record()["tile_commits"], {tile: self.base for tile in CORE})

    def test_newly_listed_tile_is_built_by_the_data_lane_only(self):
        head = self.land("voice", "game/public/assets/voice/fi/a.ogg", "clip")
        tiles = [*CORE, "t0_1_-1"]
        live_build.C.write_playable_tiles(self.published / "playable-tiles.json", tiles)
        row = self.integrate()
        # Voice clips are game content: the code lane ships them with the live tiles.
        self.assertEqual((row["class"], row["data_delta"]), ("data", []))
        self.assertEqual((row["result"], self.classes), ("green", ["code"]))
        self.assertEqual(self.live_record()["tiles"], list(CORE))
        self.assertEqual(integrator.live_commit(self.published), head)
        row = self.data()
        self.assertEqual((row["data_delta"], row["tiles"]), (["playable-tiles"], ["t0_1_-1"]))
        self.assertEqual((row["result"], self.classes), ("green", ["code", "data"]))
        self.assertEqual(sorted(self.live_record()["tiles"]), sorted(tiles))
        self.assertEqual(self.live_record()["tile_commits"]["t0_1_-1"], self.base)
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_tooling_batch_is_code(self):
        self.land("docs", "docs/notes.md", "prose")
        self.land("tooling", "scripts/dev/watch.py", "print()")
        row = self.integrate()
        self.assertEqual((row["class"], row["result"], self.classes), ("code", "green", ["code"]))

    def test_landing_during_preparation_joins_the_batch(self):
        self.land("one", "docs/one.md", "prose")
        self.land("tooling", "scripts/dev/watch.py", "print()")
        later = []
        self.preparing.append(lambda: later.append(self.land("two", "game/b.ts", "b")))
        row = self.integrate()
        # The joined head is built once; the batch became code with the joined landing.
        self.assertEqual(
            (row["result"], row["to"], row["batch_joined"]), ("green", later[0], later)
        )
        self.assertEqual((self.builds, self.classes, row["class"]), (later, ["code"], "code"))
        self.assertEqual(integrator.live_commit(self.published), later[0])

    def test_landing_after_the_first_expensive_stage_waits_for_the_next_run(self):
        head = self.land("one", "game/a.ts", "a")
        late = []
        self.during.append(lambda: late.append(self.land("late", "game/b.ts", "b")))
        row = self.integrate()
        self.assertEqual((row["to"], row["batch_joined"]), (head, []))
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(
            (self.integrate()["from"], integrator.live_commit(self.published)), (head, late[0])
        )

    def test_registry_outside_the_temporary_root_is_refused(self):
        with patch.dict(os.environ, {"TURKU3D_WT_ROOT": str(Path.home() / "turku3d/wt")}):
            with self.assertRaisesRegex(AssertionError, "real registry reached"):
                integrator.ledger_path()

    def test_join_repeats_a_cached_stage_at_most_once_and_takes_three_heads(self):
        start = self.base
        run = integrator.Run(self.published, self.main, integrator.NAME, start)
        heads = []

        def join(cached):
            heads.append(self.land(f"head-{len(heads)}", f"game/{len(heads)}.ts", "x"))
            update = run.join(start, cached)
            return update and update["commit"]

        # Joins before refresh repeat nothing; after a cached refresh the second repeat of
        # refresh is refused, and the batch takes at most JOIN_LIMIT heads.
        self.assertEqual(join(("refresh",)), heads[0])
        self.assertIsNone(join(("refresh", "tile-build")))
        self.assertEqual(join(()), heads[2])
        self.assertEqual(join(("tile-build",)), heads[3])
        self.assertIsNone(join(()))
        self.assertEqual(run.joined, [heads[0], heads[2], heads[3]])
        self.assertEqual((run.end, run.restarts), (heads[3], {"refresh": 1, "tile-build": 1}))

    def test_ledger_row_records_joins_restarts_and_stage_seconds(self):
        self.land("one", "game/a.ts", "a")
        later = []
        self.preparing.append(lambda: later.append(self.land("two", "game/b.ts", "b")))
        row = self.integrate()
        self.assertEqual((row["result"], row["joins"], row["restarts"]), ("green", 1, {}))
        self.assertEqual(row["stage_seconds"], {"game-build": 0.01})

    def test_data_lane_publishes_live_commit_with_new_data(self):
        self.assertFalse(integrator.data_due(self.published))
        self.approve(3)
        self.assertTrue(integrator.data_due(self.published))
        row = self.data()
        key = row["data_key"]
        self.assertEqual((row["lane"], row["result"], row["class"]), ("data", "green", "data"))
        self.assertEqual(
            (row["from"], row["to"], row["data_delta"]),
            (self.base, self.base, ["facade-approvals"]),
        )
        self.assertEqual((self.classes, row["tiles_commit"]), (["data"], self.base))
        # Same commit, new data: a release of its own next to the code release.
        self.assertEqual(integrator.live_commit(self.published), self.base)
        live = (self.published / "live").resolve()
        self.assertEqual(live.name, f"{self.base}-{key[:12]}")
        self.assertEqual(
            json.loads((live / "textures/assembly/MANIFEST.json").read_text())["bound"][2], "wall-2"
        )
        self.assertEqual(self.live_record()["tiles_from"], {"source": "build", "commit": self.base})
        self.assertFalse(integrator.data_due(self.published))
        # Data-lane rows never settle or unsettle the code lane.
        self.assertNotEqual(integrator.last(self.published).get("lane"), "data")

    def test_data_lane_runs_beside_the_code_lane_once_per_live_commit_and_data_key(self):
        self.approve(3)
        head = self.land("game", "game/a.ts", "a")
        # The code lane does not cover data: the data lane is due while main moved.
        self.assertTrue(integrator.data_due(self.published))
        lanes = {integrator.NAME: {"to": head}, integrator.DATA_NAME: None}
        with patch.object(
            integrator, "running", side_effect=lambda lane=integrator.NAME: lanes[lane]
        ):
            self.assertTrue(integrator.data_due(self.published))
            lanes[integrator.DATA_NAME] = {"to": self.base}
            self.assertFalse(integrator.data_due(self.published))
        self.assertEqual(self.data()["result"], "green")
        self.assertFalse(integrator.data_due(self.published))

    def test_pipeline_change_never_rebuilds_tiles_and_marks_them_stale(self):
        head = self.land("pipeline", "pipeline/ground.py", "texture_key = 2\n")
        row = self.integrate()
        self.assertEqual((row["result"], self.classes), ("green", ["code"]))
        record = self.live_record()
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(record["stale_by_code"], list(CORE))
        # New data is still built with the tile code that built the live tiles.
        self.approve(3)
        row = self.data()
        self.assertEqual(
            (row["result"], row["tiles_commit"], self.builds[-1]), ("green", self.base, self.base)
        )
        self.assertEqual(self.live_record()["stale_by_code"], list(CORE))
        self.assertEqual(integrator.live_commit(self.published), head)
        # The idle-host refresh rebuilds the stale tiles with main's tile code.
        row = self.data(refresh_stale=True)
        self.assertEqual((row["result"], row["refresh_stale"]), ("green", list(CORE)))
        self.assertEqual((row["tiles_commit"], self.builds[-1]), (head, head))
        record = self.live_record()
        self.assertEqual((record["stale_by_code"], record["tile_commits"]["t0_0_0"]), ([], head))
        self.assertIsNone(self.data(refresh_stale=True))

    def test_data_lane_falls_back_to_mains_tile_code_when_the_live_code_cannot_build(self):
        head = self.land("pipeline", "pipeline/new.py", "fetch = 'per tile'\n")
        self.integrate()
        (self.published / "needs-new-code").write_text("")
        self.approve(3)
        row = self.data()
        self.assertEqual((row["result"], row["tiles_commit"]), ("green", head))
        self.assertEqual(self.builds[-2:], [self.base, head])
        self.assertEqual(self.live_record()["stale_by_code"], [])

    def test_code_lane_recomposes_on_a_data_release_published_meanwhile(self):
        head = self.land("game", "game/a.ts", "a")
        data = []

        def data_lane():
            self.approve(3)
            data.append(self.data())

        self.during.append(data_lane)
        row = self.integrate()
        self.assertEqual(data[0]["result"], "green")
        self.assertEqual(
            (row["result"], row["rebuilt_on"]), ("green", f"{self.base}-{data[0]['data_key'][:12]}")
        )
        # Game build, the data lane's tile build of G, then M's game recomposed on its tiles.
        self.assertEqual(self.classes, ["code", "data", "code"])
        self.assertEqual(self.builds, [head, self.base, head])
        live = self.published / "live"
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(
            len(json.loads((live / "textures/assembly/MANIFEST.json").read_text())["bound"]), 3
        )

    def test_code_lane_recomposes_on_a_newer_data_release_at_most_once(self):
        head = self.land("game", "game/a.ts", "a")
        data = []

        def data_lane(walls):
            self.approve(walls)
            data.append(self.data())

        # The data lane publishes during the code build and again during its recomposition;
        # each data-lane build pops a hook of its own.
        self.during.extend((lambda: data_lane(3), lambda: None, lambda: data_lane(4)))
        row = self.integrate()
        self.assertEqual([lane["result"] for lane in data], ["green", "green"])
        self.assertEqual(row["result"], "failed")
        self.assertIn("again during the rebuild", row["failure"]["detail"])
        self.assertEqual(self.classes, ["code", "data", "code", "data"])
        self.assertEqual(self.builds, [head, self.base, head, self.base])
        self.assertEqual(integrator.live_commit(self.published), self.base)

    def test_data_lane_ships_a_newer_code_release_game_on_the_same_tiles(self):
        self.approve(3)
        code = []

        def code_lane():
            self.land("game", "game/a.ts", "a")
            code.append(integrator.run_once(self.published, self.main))

        self.during.append(code_lane)
        row = self.data()
        head = code[0]["to"]
        self.assertEqual((code[0]["result"], row["result"], row["to"]), ("green", "green", head))
        # The data release carries the code release's game, and the new data.
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual((self.published / "live/game/asset.bin").read_text(), head)
        self.assertEqual(
            len(
                json.loads((self.published / "live/textures/assembly/MANIFEST.json").read_text())[
                    "bound"
                ]
            ),
            3,
        )
        self.assertFalse(integrator.data_due(self.published))

    def test_data_lane_is_superseded_by_new_tiles_published_meanwhile(self):
        self.approve(3)
        inner = []

        def other_data_release():
            # Another data release lands first (simulated as a direct publication).
            self.approve(4)
            work = self.root / "other"
            self.outputs(work, 4)
            record = {"facades_bound": 4, "seconds": {}, "cache_hits": [], "advisory": []}
            live_build.prepare(work, self.published, self.base, record, "other")
            live_build.publish(self.published, self.base, "other")
            inner.append(True)

        self.during.append(other_data_release)
        row = self.data()
        self.assertEqual((row["result"], inner), ("superseded", [True]))
        self.assertEqual((self.published / "live").resolve().name, "other")

    def test_bad_commit_in_batch_of_five_is_bisected_and_reverted(self):
        commits = [
            self.land(f"task-{n}", f"game/{'bad' if n == 3 else f'f{n}'}.ts", str(n))
            for n in range(1, 6)
        ]
        row = self.integrate()
        self.assertEqual(row["result"], "red")
        self.assertEqual((row["culprit"], row["culprit_task"]), (commits[2], "task-3"))
        # Head, then the base check on G, then ceil(log2 5) - 1 bisection builds; only the
        # head build runs the advisory checks.
        self.assertEqual(self.builds[:2], [commits[-1], self.base])
        self.assertEqual(row["builds"], 4)
        self.assertEqual(self.checks, [True, False, False, False])
        self.assertEqual(row["failure"]["stage"], "game-build")
        self.assertEqual(integrator.live_commit(self.published), self.base)
        # The revert landed on main through the fast gate and removed only the culprit.
        main = self.git("rev-parse", "main")
        self.assertEqual(row["revert"]["commit"], main)
        self.assertEqual(self.git("rev-parse", "main~1"), commits[-1])
        self.assertFalse((self.main / "game/bad.ts").exists())
        self.assertTrue((self.main / "game/f5.ts").exists())
        landed = json.loads((self.wt / ".registry/landed.jsonl").read_text().splitlines()[-1])
        self.assertEqual(landed["reverts"], f"{commits[1]}..{commits[2]}")
        self.assertEqual((landed["reverted"], landed["integration"]), ([commits[2]], row["id"]))
        self.assertEqual(row["revert"]["reverted"], [commits[2]])
        self.assertEqual(row["id"], f"{commits[-1][:12]}.1")
        [request] = coordination.inbox("task-3", unread=True)
        self.assertEqual(request["kind"], "request")
        self.assertIn("game-build failed", request["body"])
        self.assertIn(commits[2][:12], request["subject"])
        # The next run integrates the rest of the batch plus the revert and goes green.
        self.builds.clear()
        self.assertEqual(self.integrate()["result"], "green")
        self.assertEqual(integrator.live_commit(self.published), main)

    def test_revert_message_names_integration_stage_and_commits(self):
        self.land("good", "game/a.ts", "a")
        # One landing of two commits: only its head has a landed.jsonl row.
        (self.main / "game/bad.ts").write_text("bad")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add the broken module")
        first = self.git("rev-parse", "HEAD")
        culprit = self.land("pair", "game/c.ts", "c")
        before = self.git("rev-parse", "main")
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"]), ("red", culprit))
        # The whole landing is reverted as one commit, the only revert of the run.
        self.assertEqual(self.git("rev-parse", "main~1"), before)
        self.assertEqual(row["revert"]["reverted"], [first, culprit])
        message = self.git("log", "-1", "--format=%B", "main")
        self.assertIn(f"integration {row['id']} red at game-build", message.splitlines()[0])
        self.assertIn(f"Integration: {row['id']}", message)
        self.assertIn("Failing stage: game-build", message)
        self.assertIn(f"  {first} Add the broken module", message)
        self.assertIn(f"  {culprit} pair", message)
        self.assertFalse((self.main / "game/bad.ts").exists())
        self.assertFalse((self.main / "game/c.ts").exists())

    def test_red_base_is_data_red_and_reverts_nothing(self):
        self.land("game", "game/a.ts", "a")
        head = self.land("more", "game/b.ts", "b")
        (self.published / "game-broken").write_text("")
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"], row["revert"]), ("data-red", None, None))
        self.assertEqual((row["failure"]["stage"], row["data_cause"]), ("game-build", "data-root"))
        self.assertEqual(row["head_failure"]["stage"], "game-build")
        # Head, then the base check; no bisection, no revert, live stays at G.
        self.assertEqual(self.builds, [head, self.base])
        self.assertEqual(self.git("rev-parse", "main"), head)
        self.assertEqual(integrator.live_commit(self.published), self.base)
        [warning] = [m for m in coordination.board() if m["kind"] == "warning"]
        self.assertIn(f"integration {row['id']} data-red at game-build", warning["subject"])
        self.assertIn("No commit was reverted", warning["body"])
        self.assertEqual([coordination.inbox(n, unread=True) for n in ("game", "more")], [[], []])
        # No code-lane retry until main moves; data root changes are the data lane's.
        self.assertTrue(integrator.settled(self.published, head))
        self.approve(3)
        self.assertTrue(integrator.settled(self.published, head))
        self.assertFalse(integrator.settled(self.published, self.land("next", "game/c.ts", "c")))

    def test_approval_drop_is_green_and_reported_against_the_baseline(self):
        self.approve(1)
        row = self.data()
        self.assertEqual((row["result"], row["facades_bound"]), ("green", 1))
        self.assertEqual([entry["stage"] for entry in row["advisory"]], ["facade-baseline"])
        self.assertEqual(self.builds, [self.base])

    def test_code_batch_never_changes_the_facade_count(self):
        self.land("drop", "game/facades.txt", "1\n")
        row = self.integrate()
        self.assertEqual((row["result"], row["facades_bound"], row["advisory"]), ("green", 2, []))

    def test_code_batch_runs_no_chrome(self):
        self.land("render", "game/src/render/a.ts", "a")
        row = self.integrate()
        self.assertEqual((self.plans, row["chrome_plan"], row["chrome"]), ([None], None, None))

    def test_data_build_runs_streaming_specs_only_when_the_tile_list_changed(self):
        self.approve(3)
        self.data()
        self.assertEqual(self.plans[-1]["specs"], [])
        tiles = [*CORE, "t0_1_-1"]
        live_build.C.write_playable_tiles(self.published / "playable-tiles.json", tiles)
        self.data()
        self.assertEqual(self.plans[-1]["specs"], ["game/tests/e2e-tile/tile.spec.ts"])
        self.assertEqual(self.plans[-1]["projects"], ["webgpu"])

    def test_full_suite_is_due_two_hours_after_the_last_with_a_landing_since(self):
        now = 1_000_000.0
        self.assertFalse(integrator.full_due(self.published, now))
        self.land("game", "game/a.ts", "a", when=now - 10)
        self.assertTrue(integrator.full_due(self.published, now))
        self.integrate(full=True)
        last = json.loads(integrator.ledger_path().read_text().splitlines()[-1])["time"]
        # Two hours later a full run is due only once something landed after the last one.
        self.assertFalse(integrator.full_due(self.published, last + 7201))
        self.land("more", "game/b.ts", "b", when=last + 60)
        self.assertFalse(integrator.full_due(self.published, last + 7199))
        self.assertTrue(integrator.full_due(self.published, last + 7201))

    def test_due_full_run_of_live_main_checks_the_release_in_place(self):
        head = self.land("game", "game/a.ts", "a")
        self.assertEqual(self.integrate()["result"], "green")
        self.assertFalse(self.live_record()["full_suite"])
        self.assertTrue(integrator.settled(self.published, head))
        with patch.object(integrator, "full_due", return_value=True):
            self.assertFalse(integrator.settled(self.published, head))
            row = self.integrate()
        self.assertEqual((row["result"], row["full_suite"], row["class"]), ("green", True, "code"))
        # With no earlier green full run, G is the live commit.
        self.assertEqual((row["from"], row["to"]), (head, head))
        self.assertEqual(self.plans[-1]["specs"], "all")
        self.assertEqual(self.plans[-1]["projects"], ["webgpu", "webgl"])
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertTrue(self.live_record()["full_suite"])
        self.assertEqual(self.live_record()["advisory"], [])

    def test_pages_failure_is_recorded_and_keeps_the_local_release(self):
        head = self.land("game", "game/a.ts", "a")
        failure = pages.PhotoFacades("2 bound facades are not generated-style")
        with self.pages() as publish:
            publish.side_effect = failure
            row = self.integrate()
        self.assertEqual(row["result"], "green")
        self.assertIn("not generated-style", row["pages"]["error"])
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_failed_publish_leaves_live_on_verified_release(self):
        self.land("game", "game/a.ts", "a")
        # The version file write after the link swap fails; publish swaps the link back.
        (self.root / "logs/live-version.txt").unlink()
        (self.root / "logs").rmdir()
        (self.root / "logs").write_text("not a directory")
        row = self.integrate()
        self.assertEqual(row["result"], "failed")
        self.assertEqual(integrator.live_commit(self.published), self.base)
        self.assertEqual(
            (self.published / "build/game").resolve(),
            (self.published / "live-releases" / self.base / "game").resolve(),
        )

    def test_environment_sync_failure_is_failed_without_culprit(self):
        # The executable bit survives the rewrite, so the sandbox's sync now fails.
        self.land("sync", "scripts/dev/wt", "#!/bin/sh\necho FAIL npm ci\nexit 1\n")
        self.land("game", "game/a.ts", "a")
        row = self.integrate()
        self.assertEqual((row["result"], self.builds), ("failed", []))
        self.assertIn("environment sync failed", row["failure"]["detail"])
        self.assertEqual(integrator.live_commit(self.published), self.base)

    def test_status_reports_running_stage(self):
        self.assertIsNone(integrator.running())
        log = self.root / "build.log"
        log.write_text("== refresh: make refresh ==\n== tile-build: make tile-build ==\n")
        integrator.status_path().parent.mkdir(parents=True, exist_ok=True)
        integrator.status_path().write_text(
            json.dumps({"from": "a", "to": "b", "log": str(log), "started": 1})
        )
        integrator.lock_path().touch()
        # A held lock (a running integrator child) makes the non-blocking probe fail.
        with patch.object(integrator.fcntl, "flock", side_effect=BlockingIOError):
            current = integrator.running()
        self.assertEqual(
            (current["from"], current["to"], current["stage"]), ("a", "b", "tile-build")
        )


if __name__ == "__main__":
    unittest.main()
