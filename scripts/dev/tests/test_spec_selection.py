"""Synthetic spec selection, the per-spec pass cache and the full-suite schedule."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import integration_chrome as chrome  # noqa: E402
import spec_selection as sel  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FILES = {
    "game/playwright.config.ts": "import { port } from '../tools/dev/environment.mjs';\n",
    "game/playwright.tile.config.ts": (
        "import { port } from '../tools/dev/environment.mjs';\n"
        "const command = `node ../tools/dev/serve-dist.mjs --root x`;\n"
    ),
    "tools/dev/environment.mjs": "export const port = 1;\n",
    "tools/dev/serve-dist.mjs": "export {};\n",
    "game/tests/e2e/fixtures.ts": "export { test } from '@playwright/test';\n",
    "game/tests/e2e/smoke.spec.ts": (
        "import { test } from './fixtures';\n"
        "test('loads', { tag: ['@walk', '@streaming'] }, f);\n"
        "test('QA camera', { tag: ['@qa', '@render'] }, f);\n"
    ),
    "game/tests/e2e/render-materials.spec.ts": (
        "import { test } from './fixtures';\ntest(`@render ${quality}: shaders`, f);\n"
    ),
    "game/tests/e2e/save-menu.spec.ts": (
        "import { test } from './fixtures';\n"
        "import { SaveStore } from '../../src/core/save';\n"
        "test('save', { tag: ['@gameplay'] }, f);\n"
    ),
    "game/tests/e2e-tile/area.spec.ts": (
        "import { test } from '../e2e/fixtures';\n"
        "const poses = read(path.join(import.meta.dirname, 'viewpoints.json'));\n"
        "test('draw budget', { tag: ['@pipeline', '@render'] }, f);\n"
    ),
    "game/tests/e2e-tile/viewpoints.json": "[]\n",
    "game/tests/e2e-tile/missions.spec.ts": (
        "import { test } from '../e2e/fixtures';\ntest('pay', { tag: ['@gameplay', '@walk'] }, f);\n"
    ),
    "game/src/core/save.ts": "export class SaveStore {}\n",
}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "scripts/dev").mkdir(parents=True)
        shutil.copy(REPO / "scripts/dev/gate.toml", self.root / "scripts/dev/gate.toml")
        for name, text in FILES.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def select(self, paths, landing_class="code", **options):
        return sel.select(paths, landing_class, root=self.root, **options)

    def test_code_change_selects_tagged_specs_on_webgpu(self):
        plan = self.select(["game/src/gameplay/missions.ts"])
        self.assertEqual(
            plan["specs"],
            ["game/tests/e2e-tile/missions.spec.ts", "game/tests/e2e/save-menu.spec.ts"],
        )
        self.assertEqual(plan["projects"], ["webgpu"])
        self.assertIn("@gameplay", plan["reason"])

    def test_render_change_selects_both_projects(self):
        plan = self.select(["game/src/render/sky.ts"])
        self.assertEqual(
            plan["specs"],
            [
                "game/tests/e2e-tile/area.spec.ts",
                "game/tests/e2e/render-materials.spec.ts",
                "game/tests/e2e/smoke.spec.ts",
            ],
        )
        self.assertEqual(plan["projects"], ["webgpu", "webgl"])

    def test_data_change_runs_no_spec_unless_the_tile_list_changed(self):
        paths = ["game/public/assets/voice/a.ogg"]
        self.assertEqual(self.select(paths, "data")["specs"], [])
        plan = self.select(paths, "data", tiles_changed=True)
        self.assertEqual(plan["specs"], ["game/tests/e2e/smoke.spec.ts"])
        self.assertEqual(plan["projects"], ["webgpu"])

    def test_inert_change_runs_nothing(self):
        plan = self.select(["docs/a.md", "game/src/render/README.md"], "inert", tiles_changed=True)
        self.assertEqual((plan["specs"], plan["projects"]), ([], ["webgpu"]))

    def test_prose_and_untriggered_code_select_nothing(self):
        self.assertEqual(
            self.select(["game/src/render/README.md", "scripts/dev/a.py"])["specs"], []
        )

    def test_helpers_and_imported_modules_select_their_importers(self):
        everything = sorted(name for name in FILES if name.endswith(".spec.ts"))
        self.assertEqual(self.select(["game/tests/e2e/fixtures.ts"])["specs"], everything)
        self.assertEqual(
            self.select(["game/tests/e2e-tile/viewpoints.json"])["specs"],
            ["game/tests/e2e-tile/area.spec.ts"],
        )
        self.assertIn(
            "game/tests/e2e/save-menu.spec.ts", self.select(["game/src/core/save.ts"])["specs"]
        )
        self.assertEqual(self.select(["tools/dev/environment.mjs"])["specs"], everything)

    def test_spec_selects_itself_and_a_deleted_spec_nothing(self):
        spec = "game/tests/e2e-tile/missions.spec.ts"
        self.assertEqual(self.select([spec])["specs"], [spec])
        self.assertEqual(self.select(["game/tests/e2e/gone.spec.ts"])["specs"], [])

    def test_playwright_config_selects_its_directory_on_both_projects(self):
        plan = self.select(["game/playwright.tile.config.ts"])
        self.assertEqual(
            plan["specs"],
            ["game/tests/e2e-tile/area.spec.ts", "game/tests/e2e-tile/missions.spec.ts"],
        )
        self.assertEqual(plan["projects"], ["webgpu", "webgl"])

    def test_unmapped_trigger_selects_everything_and_says_so(self):
        plan = self.select(["game/src/newsystem/a.ts"])
        self.assertEqual(len(plan["specs"]), 5)
        self.assertIn("unmapped trigger", plan["reason"])

    def test_batch_class_is_the_widest_and_unknown_is_code(self):
        self.assertEqual(sel.batch_class(["inert", "data"]), "data")
        self.assertEqual(sel.batch_class(["data", "code", "inert"]), "code")
        self.assertEqual(sel.batch_class(["inert"]), "inert")
        self.assertEqual(sel.batch_class(["data", None]), "code")
        self.assertEqual(sel.batch_class([]), "code")

    # Cache ---------------------------------------------------------------------------

    def built(self):
        bundle = self.root / "work/build/game"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "index.js").write_text("bundle-1")
        manifest = self.root / "work/dist/tiles/manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"built": 1, "visual": {"t0_0_0": {"sha256": "a"}}}))
        return bundle, manifest

    def runner(self, failing=()):
        calls = []

        def run(command, cwd, env):
            calls.append(command)
            specs = [arg for arg in command if arg.endswith(".spec.ts")]
            projects = [command[i + 1] for i, arg in enumerate(command) if arg == "--project"]
            rows = [
                {
                    "file": Path(spec).name,
                    "tests": [
                        {
                            "projectName": project,
                            "status": "unexpected" if Path(spec).name in failing else "expected",
                        }
                        for project in projects
                    ],
                }
                for spec in specs
            ]
            report = {
                "config": {"rootDir": str(self.root / "game/tests/e2e-tile")},
                "suites": [{"specs": rows[:1]}, {"suites": [{"specs": rows[1:]}]}],
            }
            Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(json.dumps(report))
            code = 1 if any(Path(spec).name in failing for spec in specs) else 0
            return type("Completed", (), {"returncode": code, "stdout": "ran\n"})()

        return run, calls

    def run_plan(self, plan, runner):
        bundle, manifest = self.built()
        return sel.run(
            plan,
            root=self.root,
            env={"TURKU3D_WORK": str(self.root / "work")},
            bundle_dir=bundle,
            tiles_manifest=manifest,
            runner=runner,
            cache=self.root / "cache",
        )

    def test_passing_specs_are_cached_and_a_change_misses(self):
        plan = self.select(["game/src/render/sky.ts"])
        run, calls = self.runner(failing={"area.spec.ts"})
        code, _, stats = self.run_plan(plan, run)
        self.assertEqual(code, 1)
        self.assertEqual(
            stats["run"],
            ["webgpu:game/tests/e2e-tile/area.spec.ts", "webgl:game/tests/e2e-tile/area.spec.ts"],
        )
        self.assertEqual(
            stats["skipped"],
            [
                "webgpu:game/tests/e2e-tile/missions.spec.ts",
                "webgl:game/tests/e2e-tile/missions.spec.ts",
            ],
        )
        # The failing pairs are named for the integrator's advisory warning.
        self.assertEqual(stats["failed"], stats["run"])
        # Failures are not cached: the next run repeats the spec.
        code, _, stats = self.run_plan(plan, self.runner()[0])
        self.assertEqual((code, stats["cached"], len(stats["run"])), (0, [], 2))
        self.assertEqual(stats["failed"], [])
        run, calls = self.runner()
        code, _, stats = self.run_plan(plan, run)
        self.assertEqual((code, calls, stats["run"]), (0, [], []))
        self.assertEqual(len(stats["cached"]), 2)
        # A rebuilt manifest with the same content still hits; a changed helper misses.
        self.assertEqual(len(self.run_plan(plan, run)[2]["cached"]), 2)
        (self.root / "game/tests/e2e-tile/viewpoints.json").write_text("[1]\n")
        self.assertEqual(len(self.run_plan(plan, run)[2]["run"]), 2)

    def test_cache_key_covers_bundle_tiles_config_and_project(self):
        plan = {"specs": "all", "projects": ["webgpu"], "reason": "full", "full_suite": True}
        run, calls = self.runner()
        self.run_plan(plan, run)
        self.assertEqual(len(calls), 1)
        bundle, manifest = self.built()
        self.assertEqual(len(self.run_plan(plan, run)[2]["cached"]), 2)
        for change in (
            lambda: (bundle / "index.js").write_text("bundle-2"),
            lambda: manifest.write_text(json.dumps({"visual": {"t0_0_0": {"sha256": "b"}}})),
            lambda: (self.root / "tools/dev/serve-dist.mjs").write_text("export const x = 1;\n"),
        ):
            change()
            self.assertEqual(len(self.run_plan_keep(plan, run)[2]["run"]), 2)
        webgl = {**plan, "projects": ["webgl"]}
        self.assertEqual(len(self.run_plan_keep(webgl, run)[2]["run"]), 2)

    def run_plan_keep(self, plan, runner):
        """run_plan without rewriting the build outputs."""
        return sel.run(
            plan,
            root=self.root,
            env={"TURKU3D_WORK": str(self.root / "work")},
            bundle_dir=self.root / "work/build/game",
            tiles_manifest=self.root / "work/dist/tiles/manifest.json",
            runner=runner,
            cache=self.root / "cache",
        )

    def test_empty_selection_starts_no_browser(self):
        run, calls = self.runner()
        code, _, stats = self.run_plan(self.select(["docs/a.md"]), run)
        self.assertEqual((code, calls, stats["selected"], len(stats["skipped"])), (0, [], [], 4))

    def test_prune_keeps_the_newest_results(self):
        directory = self.root / "results"
        directory.mkdir()
        for n in range(5):
            path = directory / f"{n}.json"
            path.write_text("{}")
            os.utime(path, (n, n))
        sel.prune(directory, keep=3)
        self.assertEqual(
            sorted(p.name for p in directory.iterdir()), ["2.json", "3.json", "4.json"]
        )

    # Schedule ------------------------------------------------------------------------

    def test_full_suite_schedule(self):
        hour = 3600
        landed = [{"time": 10.0}]
        self.assertTrue(chrome.full_due([], landed, 3 * hour))
        self.assertFalse(chrome.full_due([], [], 3 * hour))
        rows = [{"full_suite": True, "time": 100.0, "result": "red"}]
        self.assertFalse(chrome.full_due(rows, landed, 100.0 + 3 * hour))
        landed.append({"time": 200.0})
        self.assertFalse(chrome.full_due(rows, landed, 100.0 + hour))
        self.assertTrue(chrome.full_due(rows, landed, 100.0 + 2 * hour))
        rows.append({"full_suite": False, "time": 300.0, "result": "green", "to": "b"})
        self.assertEqual(chrome.last_full(rows)["time"], 100.0)


class MapTests(unittest.TestCase):
    """The real repository: every tracked trigger path has a row or an import route."""

    def test_no_real_trigger_path_is_unmapped(self):
        import subprocess

        files = subprocess.run(
            ["git", "ls-files"], cwd=REPO, check=True, text=True, capture_output=True
        ).stdout.splitlines()
        self.assertNotIn("unmapped trigger", sel.select(files, "code")["reason"])


if __name__ == "__main__":
    unittest.main()
