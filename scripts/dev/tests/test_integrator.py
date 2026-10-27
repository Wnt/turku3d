"""Synthetic integration batches: classes, publish, re-point, base check, bisect, facades."""

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
import coordination  # noqa: E402
import integrator  # noqa: E402
import live_build  # noqa: E402
import pages  # noqa: E402

GATE = "#!/bin/sh\necho PASS fast-gate 0.01s\n"
REPO = Path(__file__).resolve().parents[3]
SPECS = {
    "game/tests/e2e-tile/tile.spec.ts": "test('walks', { tag: ['@walk', '@streaming'] }, f);\n",
    "game/tests/e2e-tile/integration.spec.ts": "test('office', { tag: ['@render'] }, f);\n",
}


class IntegratorTests(unittest.TestCase):
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
        self.chrome_flakes = 0

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
        # The seed release counts as verified by a full Chrome suite.
        report = {"facades_bound": 2, "seconds": {}, "cache_hits": [], "full_suite": True}
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
        landing_class="code",
        join=None,
        chrome=None,
    ):
        """Stands in for the real stages: red on game/bad.ts, real facade inspection.

        Before its first expensive stage it calls join, like live_build.build, and then
        rebuilds the joined head; `self.during` runs as that stage starts.
        game/full-red.ts fails only the full Chrome suite.
        """
        if self.preparing:
            self.preparing.pop(0)()
        if join and (update := join()):
            landing_class = update["landing_class"]
            chrome = update.get("chrome", chrome)
        self.plans.append(chrome)
        if (repo / "game/full-red.ts").exists() and chrome["full_suite"]:
            self.builds.append(self.git("rev-parse", "HEAD", cwd=repo))
            raise live_build.StageFailed("chrome", f"chrome failed (1); {log}")
        self.assertEqual(previous, (published / "live").resolve())
        self.builds.append(self.git("rev-parse", "HEAD", cwd=repo))
        self.classes.append(landing_class)
        if self.during:
            self.during.pop(0)()
        log.write_text("== tile-build: make tile-build ==\n")
        if (repo / "game/bad.ts").exists():
            raise live_build.StageFailed("tile-build", f"tile-build failed (2); {log}")
        if (published / "raw-broken").exists():
            raise live_build.StageFailed("refresh", f"refresh failed (1); {log}")
        if self.chrome_flakes:
            self.chrome_flakes -= 1
            raise live_build.StageFailed("chrome", f"chrome failed (1); {log}")
        # The assembler binds the walls the code places that the approval root still holds.
        approved = len(json.loads(self.approvals().read_text())["bound"])
        facades = min(int((repo / "game/facades.txt").read_text()), approved)
        manifest = self.outputs(work, facades)
        report = live_build.inspect(
            manifest, facades, live_build.baseline(published), live_build.tiles(published), reason
        )
        chrome_stats = {"full_suite": chrome["full_suite"], "selected": chrome["specs"]}
        return {
            **report,
            "seconds": {"tile-build": 0.01},
            "cache_hits": ["refresh"],
            "chrome": chrome_stats,
        }

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
        with patch.object(live_build, "build", side_effect=self.fake_build):
            return integrator.run_once(self.published, self.main, full=full)

    def live_record(self):
        return json.loads((self.published / "live/live-build.json").read_text())

    def test_green_batch_publishes_main_head(self):
        self.land("one", "game/a.ts", "a")
        head = self.land("two", "game/b.ts", "b")
        row = self.integrate()
        self.assertEqual((row["result"], row["from"], row["to"]), ("green", self.base, head))
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(
            (row["builds"], row["cache_hits"], row["facades_bound"]), (1, ["refresh"], 2)
        )
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

    def test_inert_paths_with_changed_approvals_are_a_data_batch(self):
        self.land("note", "docs/reports/facades.md", "report")
        self.approve(3)
        row = self.integrate()
        self.assertEqual((row["class"], row["data_delta"]), ("data", ["facade-approvals"]))
        self.assertEqual(row["tiles"], list(live_build.C.CORE_TILES))
        self.assertEqual((row["result"], self.classes), ("green", ["data"]))

    def test_newly_listed_tile_is_a_data_batch_for_that_tile(self):
        head = self.land("voice", "game/public/assets/voice/fi/a.ogg", "clip")
        tiles = [*live_build.C.CORE_TILES, "t0_1_-1"]
        live_build.C.write_playable_tiles(self.published / "playable-tiles.json", tiles)
        row = self.integrate()
        self.assertEqual((row["class"], row["data_delta"]), ("data", ["playable-tiles"]))
        self.assertEqual(
            (row["tiles"], row["paths"]), (["t0_1_-1"], ["game/public/assets/voice/fi/a.ogg"])
        )
        self.assertEqual((row["result"], self.classes), ("green", ["data"]))
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_tooling_batch_is_code(self):
        self.land("docs", "docs/notes.md", "prose")
        self.land("tooling", "scripts/dev/watch.py", "print()")
        row = self.integrate()
        self.assertEqual((row["class"], row["result"], self.classes), ("code", "green", ["code"]))

    def test_landing_during_preparation_joins_the_batch(self):
        self.land("one", "docs/one.md", "prose")
        self.approve(3)
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

    def test_data_lane_publishes_live_commit_with_new_data(self):
        self.assertFalse(integrator.data_due(self.published, self.main))
        self.approve(3)
        self.assertTrue(integrator.data_due(self.published, self.main))
        with patch.object(live_build, "build", side_effect=self.fake_build):
            row = integrator.run_data(self.published, self.main)
        key = row["data_key"]
        self.assertEqual((row["lane"], row["result"], row["class"]), ("data", "green", "data"))
        self.assertEqual(
            (row["from"], row["to"], row["data_delta"]),
            (self.base, self.base, ["facade-approvals"]),
        )
        self.assertEqual(self.classes, ["data"])
        # Same commit, new data: a release of its own next to the code release.
        self.assertEqual(integrator.live_commit(self.published), self.base)
        live = (self.published / "live").resolve()
        self.assertEqual(live.name, f"{self.base}-{key[:12]}")
        self.assertEqual(
            json.loads((live / "textures/assembly/MANIFEST.json").read_text())["bound"][2], "wall-2"
        )
        self.assertFalse(integrator.data_due(self.published, self.main))
        # Data-lane rows never settle or unsettle the code lane.
        self.assertNotEqual(integrator.last(self.published).get("lane"), "data")

    def test_data_lane_waits_while_main_or_the_code_lane_covers_the_data(self):
        self.approve(3)
        head = self.land("game", "game/a.ts", "a")
        # Main moved: the code lane's next run covers the new data.
        self.assertFalse(integrator.data_due(self.published, self.main))
        key = integrator.data_key(self.published)
        lanes = {integrator.DATA_NAME: None}
        with patch.object(
            integrator, "running", side_effect=lambda lane=integrator.NAME: lanes[lane]
        ):
            for code_key, due in ((key, False), ("older", True)):
                lanes[integrator.NAME] = {"to": head, "data_key": code_key}
                self.assertEqual(integrator.data_due(self.published, self.main), due, code_key)

    def test_code_lane_rebuilds_on_top_of_a_data_release_published_meanwhile(self):
        head = self.land("game", "game/a.ts", "a")
        data = []

        def data_lane():
            self.approve(3)
            data.append(integrator.run_data(self.published, self.main))

        self.during.append(data_lane)
        row = self.integrate()
        self.assertEqual(data[0]["result"], "green")
        self.assertEqual(
            (row["result"], row["rebuilt_on"]), ("green", f"{self.base}-{data[0]['data_key'][:12]}")
        )
        # Code build, the data lane's build of G, then the data rebuild of M on top.
        self.assertEqual(self.classes, ["code", "data", "data"])
        self.assertEqual(self.builds, [head, self.base, head])
        live = self.published / "live"
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertEqual(
            len(json.loads((live / "textures/assembly/MANIFEST.json").read_text())["bound"]), 3
        )

    def test_data_lane_never_publishes_over_a_newer_code_release(self):
        self.approve(3)
        code = []

        def code_lane():
            self.land("game", "game/a.ts", "a")
            code.append(integrator.run_once(self.published, self.main))

        self.during.append(code_lane)
        with patch.object(live_build, "build", side_effect=self.fake_build):
            row = integrator.run_data(self.published, self.main)
        self.assertEqual((code[0]["result"], row["result"]), ("green", "superseded"))
        self.assertEqual(integrator.live_commit(self.published), code[0]["to"])
        # The code release already carries the new data; nothing is left for the data lane.
        self.assertFalse(integrator.data_due(self.published, self.main))

    def test_bad_commit_in_batch_of_five_is_bisected_and_reverted(self):
        commits = [
            self.land(f"task-{n}", f"game/{'bad' if n == 3 else f'f{n}'}.ts", str(n))
            for n in range(1, 6)
        ]
        row = self.integrate()
        self.assertEqual(row["result"], "red")
        self.assertEqual((row["culprit"], row["culprit_task"]), (commits[2], "task-3"))
        # Head, then the base check on G, then ceil(log2 5) - 1 bisection builds.
        self.assertEqual(self.builds[:2], [commits[-1], self.base])
        self.assertEqual(row["builds"], 4)
        self.assertEqual(row["failure"]["stage"], "tile-build")
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
        self.assertIn("tile-build failed", request["body"])
        self.assertIn(commits[2][:12], request["subject"])
        # The next run integrates the rest of the batch plus the revert and goes green.
        self.builds.clear()
        self.assertEqual(self.integrate()["result"], "green")
        self.assertEqual(integrator.live_commit(self.published), main)

    def test_flaky_chrome_stage_that_passes_on_rerun_is_green(self):
        head = self.land("game", "game/a.ts", "a")
        self.chrome_flakes = 1
        row = self.integrate()
        self.assertEqual((row["result"], row["builds"]), ("green", 2))
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_chrome_red_twice_is_red(self):
        self.land("game", "game/a.ts", "a")
        self.chrome_flakes = 2
        row = self.integrate()
        self.assertEqual((row["result"], row["failure"]["stage"]), ("red", "chrome"))

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
        self.assertIn(f"integration {row['id']} red at tile-build", message.splitlines()[0])
        self.assertIn(f"Integration: {row['id']}", message)
        self.assertIn("Failing stage: tile-build", message)
        self.assertIn(f"  {first} Add the broken module", message)
        self.assertIn(f"  {culprit} pair", message)
        self.assertFalse((self.main / "game/bad.ts").exists())
        self.assertFalse((self.main / "game/c.ts").exists())

    def test_red_base_is_data_red_and_reverts_nothing(self):
        self.land("game", "game/a.ts", "a")
        head = self.land("more", "game/b.ts", "b")
        (self.published / "raw-broken").write_text("")
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"], row["revert"]), ("data-red", None, None))
        self.assertEqual((row["failure"]["stage"], row["data_cause"]), ("refresh", "data-root"))
        self.assertEqual(row["head_failure"]["stage"], "refresh")
        # Head, then the base check; no bisection, no revert, live stays at G.
        self.assertEqual(self.builds, [head, self.base])
        self.assertEqual(self.git("rev-parse", "main"), head)
        self.assertEqual(integrator.live_commit(self.published), self.base)
        [warning] = [m for m in coordination.board() if m["kind"] == "warning"]
        self.assertIn(f"integration {row['id']} data-red at refresh", warning["subject"])
        self.assertIn("No commit was reverted", warning["body"])
        self.assertEqual([coordination.inbox(n, unread=True) for n in ("game", "more")], [[], []])
        # No retry until main moves or the data root changes.
        self.assertTrue(integrator.settled(self.published, head))
        self.approve(3)
        self.assertFalse(integrator.settled(self.published, head))
        self.approve(2)
        self.assertTrue(integrator.settled(self.published, head))
        self.assertFalse(integrator.settled(self.published, self.land("next", "game/c.ts", "c")))

    def test_facade_drop_without_reason_is_red(self):
        culprit = self.land("drop", "game/facades.txt", "1\n")
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"]), ("red", culprit))
        self.assertEqual(row["failure"]["stage"], "facade-baseline")
        self.assertIn("facades_bound regressed", row["failure"]["detail"])
        self.assertEqual(integrator.live_commit(self.published), self.base)

    def test_facade_drop_from_changed_approvals_is_data_red(self):
        head = self.land("docs-and-prompts", "game/a.ts", "a")
        published = live_build.approval_manifests(self.published)["published"]
        self.approve(1)
        row = self.integrate()
        self.assertEqual((row["result"], row["culprit"]), ("data-red", None))
        self.assertEqual(row["data_cause"], "approval-root-changed")
        self.assertEqual(
            (row["failure"]["stage"], row["failure"]["facades_bound"]), ("facade-baseline", 1)
        )
        self.assertEqual(row["failure"]["facades_baseline"], 2)
        self.assertEqual(row["approval_manifest"]["published"], published)
        self.assertNotEqual(row["approval_manifest"]["current"], published)
        self.assertEqual(self.git("rev-parse", "main"), head)
        self.assertEqual(integrator.live_commit(self.published), self.base)
        [warning] = [m for m in coordination.board() if m["kind"] == "warning"]
        self.assertIn("1 facades bound, published baseline 2", warning["body"])
        self.assertIn("MANIFEST.json differs", warning["body"])

    def test_approval_drop_with_ledger_reason_is_green(self):
        self.approve(1)
        self.land("accept", "docs/a.ts", "a", reason="photo facades retired by decision")
        self.land("game", "game/a.ts", "a")
        row = self.integrate()
        self.assertEqual((row["result"], row["facades_bound"]), ("green", 1))
        self.assertEqual(integrator.live_commit(self.published), self.git("rev-parse", "main"))
        self.assertEqual(self.builds, [self.git("rev-parse", "main")])

    def test_facade_drop_with_ledger_reason_is_green(self):
        self.land("drop", "game/facades.txt", "1\n", reason="old facade map was wrong")
        head = self.land("later", "game/a.ts", "a")
        row = self.integrate()
        self.assertEqual((row["result"], row["facades_bound"]), ("green", 1))
        self.assertEqual(integrator.live_commit(self.published), head)

    def test_green_full_run_deploys_pages_when_configured(self):
        head = self.land("game", "game/a.ts", "a")
        (self.published / "publish").mkdir()
        (self.published / pages.CONFIG).write_text(json.dumps({"repo": "o/site"}))
        deployed = {"url": "https://o.github.io/site/", "release": head}
        with patch.object(pages, "publish", return_value=deployed) as publish:
            row = self.integrate(full=True)
        publish.assert_called_once_with(self.published)
        self.assertEqual((row["result"], row["pages"]), ("green", deployed))
        self.assertTrue(row["full_suite"])
        self.assertTrue(self.live_record()["full_suite"])

    def test_selected_green_run_publishes_live_but_not_pages(self):
        head = self.land("game", "game/src/render/a.ts", "a")
        (self.published / "publish").mkdir()
        (self.published / pages.CONFIG).write_text(json.dumps({"repo": "o/site"}))
        with patch.object(pages, "publish") as publish:
            row = self.integrate()
        publish.assert_not_called()
        self.assertEqual((row["result"], row["full_suite"]), ("green", False))
        self.assertEqual(integrator.live_commit(self.published), head)
        self.assertFalse(self.live_record()["full_suite"])

    def test_code_batch_runs_the_specs_its_paths_select(self):
        self.land("render", "game/src/render/a.ts", "a")
        row = self.integrate()
        [plan] = self.plans
        self.assertEqual(plan["specs"], ["game/tests/e2e-tile/integration.spec.ts"])
        self.assertEqual(plan["projects"], ["webgpu", "webgl"])
        self.assertEqual(row["chrome_plan"], plan)
        self.assertEqual(row["chrome"]["selected"], plan["specs"])

    def test_data_batch_runs_streaming_specs_only_when_the_tile_list_changed(self):
        self.land("voice", "game/public/assets/voice/fi/a.ogg", "a")
        self.assertEqual(self.integrate()["chrome_plan"]["specs"], [])
        self.land("more", "game/public/assets/voice/fi/b.ogg", "b")
        tiles = [*live_build.C.CORE_TILES, "t0_1_-1"]
        live_build.C.write_playable_tiles(self.published / "playable-tiles.json", tiles)
        row = self.integrate()
        self.assertEqual(row["class"], "data")
        self.assertEqual(row["chrome_plan"]["specs"], ["game/tests/e2e-tile/tile.spec.ts"])
        self.assertEqual(row["chrome_plan"]["projects"], ["webgpu"])

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

    def test_due_full_run_of_live_main_verifies_the_release_in_place(self):
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

    def test_red_full_run_bisects_landings_since_the_last_green_full_run(self):
        first = self.land("first", "game/a.ts", "a")
        self.assertEqual(self.integrate(full=True)["result"], "green")
        culprit = self.land("culprit", "game/full-red.ts", "x")
        self.assertEqual(self.integrate()["result"], "green")
        head = self.land("later", "game/c.ts", "c")
        self.assertEqual(self.integrate()["result"], "green")
        self.builds.clear()
        row = self.integrate(full=True)
        self.assertEqual((row["result"], row["from"], row["culprit"]), ("red", first, culprit))
        # Head (and its one Chrome rerun), then the base check on the last green full run.
        self.assertEqual(self.builds[:3], [head, head, first])
        self.assertFalse((self.main / "game/full-red.ts").exists())
        self.assertTrue((self.main / "game/c.ts").exists())

    def test_pages_failure_is_recorded_and_keeps_the_local_release(self):
        head = self.land("game", "game/a.ts", "a")
        (self.published / "publish").mkdir()
        (self.published / pages.CONFIG).write_text(json.dumps({"repo": "o/site"}))
        failure = pages.PhotoFacades("2 bound facades are not generated-style")
        with patch.object(pages, "publish", side_effect=failure):
            row = self.integrate(full=True)
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
