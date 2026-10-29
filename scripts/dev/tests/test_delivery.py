"""Small boundary checks justify the quick suite: deadlines, merge safety and forward-only deploy."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bounded_checks  # noqa: E402
import delivery  # noqa: E402
import landing  # noqa: E402
import post_deploy  # noqa: E402

SERIAL = True  # Cases patch shared delivery/sandbox globals.


class DeliveryTests(unittest.TestCase):
    def test_parallel_checks_share_one_deadline_and_kill_timed_out_children(self):
        with tempfile.TemporaryDirectory() as temp:
            result = bounded_checks.commands(
                [
                    f"{sys.executable} -c 'import time; time.sleep(30)'",
                    f"{sys.executable} -c 'print(1)'",
                ],
                cwd=temp,
                env=dict(os.environ),
                directory=Path(temp),
                # Allow interpreter startup while the other gate stages run in parallel.
                seconds=0.5,
            )
        self.assertFalse(result["ok"])
        self.assertEqual([r["exit"] for r in result["checks"]], [124, 0])
        self.assertLess(result["seconds"], 2)

    def test_plan_requires_explicit_affected_and_async_selections_for_current_tree(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(delivery.s, "git", return_value="tree"),
        ):
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "select affected checks"):
                delivery.plan(root, root)
            value = delivery.plan(
                root, root, tests=["unit --grep affected"], async_tests=["unit broader"]
            )
            self.assertEqual(value["tests"], ["unit --grep affected"])
            with patch.object(delivery.s, "git", return_value="new-tree"):
                with self.assertRaisesRegex(ValueError, "select affected checks"):
                    delivery.plan(root, root)

    def test_every_selection_runs_repository_policy_with_the_same_parallel_deadline(self):
        for tests in ([], ["unit --grep affected"]):
            with (
                patch.object(delivery, "environment", return_value={}),
                patch.object(bounded_checks, "commands", return_value={"ok": False}) as run,
            ):
                result = delivery.check(Path("repo"), {"tests": tests}, Path("task"))
            self.assertFalse(result["ok"])
            self.assertEqual(
                run.call_args.args[0],
                ["python3 scripts/dev/repository_policy.py", *tests],
            )
            self.assertEqual(run.call_args.kwargs["seconds"], 30)

    def test_deploy_failure_keeps_merged_commit_and_never_runs_rollback(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(delivery, "deploy", side_effect=ValueError("upload failed")),
            patch.object(delivery.coord, "send"),
            patch.object(delivery, "ledger"),
        ):
            result = delivery.after_merge("test", Path(temp), "a" * 40, {}, Path(temp))
        self.assertEqual(result["result"], "deploy-failed")
        self.assertEqual(result["commit"], "a" * 40)

    def test_server_only_changes_deploy_oci(self):
        self.check_source_deployment("server/economy.js")

    def test_facade_program_only_changes_reach_oci(self):
        for path in (
            "pipeline/turku3d/facade_programs.py",
            "pipeline/turku3d/facade_program_geometry.py",
            "pipeline/turku3d/wall_surfaces.py",
            "pipeline/turku3d/wall_assembly.py",
            "textures/turkutex/facade_program.py",
            "textures/turkutex/facade_archetypes.py",
            "textures/library/facade-programs.json",
        ):
            with self.subTest(path=path):
                self.check_source_deployment(path)

    def check_source_deployment(self, changed):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(delivery, "deploy", return_value={}),
            patch("deploy_drafts.deploy", return_value={"multiplayer": "presence ok"}) as deploy,
            patch.object(delivery, "smoke", return_value={"ok": True}),
            patch.object(delivery, "environment", return_value={}),
            patch.object(post_deploy, "start", return_value={}),
            patch.object(delivery, "ledger"),
        ):
            result = delivery.after_merge(
                "test",
                Path(temp),
                "a" * 40,
                {"changed_paths": [changed]},
                Path(temp),
            )
        deploy.assert_called_once_with(Path(temp), "a" * 40)
        self.assertEqual(result["generator"]["multiplayer"], "presence ok")

    def test_active_agent_finish_keeps_its_running_state_and_sandbox(self):
        import external_worker

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = root / "result.md"
            result.write_text("## Result: DONE\n")
            (root / "meta.json").write_text(
                json.dumps({"state": "running", "repo": str(root), "pid": 123})
            )
            with (
                patch.dict(os.environ, {"TURKU3D_SANDBOX": "self"}),
                patch("task_runner.task", return_value=root),
                patch("task_runner.running", return_value=True),
                patch.object(delivery, "plan"),
                patch.object(landing, "land") as merge,
                patch.object(external_worker.s, "git", return_value="commit"),
            ):
                external_worker.finish("self", result)
            self.assertEqual(json.loads((root / "meta.json").read_text())["state"], "running")
            self.assertTrue(merge.call_args.args[1])

    def test_smoke_uses_public_health_route_with_one_shared_deadline(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs["deadline"]))
            return {"exit": 0, "seconds": 0.01}

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(delivery, "environment", return_value={}),
            patch.object(bounded_checks, "run", side_effect=run),
        ):
            result = delivery.smoke(Path(temp), {"url": "https://example.test/"}, Path(temp))
        self.assertTrue(result["ok"])
        self.assertEqual(len({deadline for _, deadline in calls}), 1)
        urls = [command[-1] for command, _ in calls]
        self.assertIn("https://turku.lab.madekivi.fi/coarse/health", urls)
        self.assertFalse(any(url.endswith("/status") for url in urls))

    def test_snapshot_preserves_dependency_executable_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, snapshot = root / "repo", root / "snapshot"
            package = repo / "node_modules/package/bin"
            package.mkdir(parents=True)
            (package / "cli.js").write_text("require('../package.json')")
            executable = repo / "node_modules/.bin/cli"
            executable.parent.mkdir()
            executable.symlink_to("../package/bin/cli.js")
            server_dependency = repo / "server/node_modules/ws/index.js"
            server_dependency.parent.mkdir(parents=True)
            server_dependency.write_text("export default 'websocket dependency';")
            post_deploy.copy_dependencies(repo, snapshot)
            copied = snapshot / "node_modules/.bin/cli"
            self.assertTrue(copied.is_symlink())
            self.assertEqual(
                copied.resolve(), (snapshot / "node_modules/package/bin/cli.js").resolve()
            )
            self.assertEqual(
                (snapshot / "server/node_modules/ws/index.js").read_text(),
                server_dependency.read_text(),
            )

    def test_second_forward_fix_is_rejected(self):
        rows = [
            {"job": "failed", "state": "failed"},
            {"job": "fix", "state": "running", "fix_for": "failed"},
        ]
        with patch.object(post_deploy, "history", return_value=rows):
            with self.assertRaisesRegex(ValueError, "one forward fix"):
                post_deploy.validate_fix("failed")

    def test_merge_does_not_advance_main_when_selected_checks_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            meta = {"state": "done", "finished": 1}
            with (
                patch.object(landing, "member", return_value=(path, meta, "")),
                patch.object(landing, "checkout", return_value=path),
                patch.object(landing.s, "git", return_value="head"),
                patch.object(delivery, "plan", return_value={}),
                patch.object(landing, "rebase", return_value=[]),
                patch.object(delivery, "check", return_value={"ok": False}),
                patch.object(landing, "advance") as advance,
            ):
                with self.assertRaisesRegex(ValueError, "affected tests failed"):
                    landing.land("test")
                advance.assert_not_called()
