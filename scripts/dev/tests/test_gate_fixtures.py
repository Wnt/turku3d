"""Cached synthetic tiles must rebuild for producer or output changes."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_fixtures as fixtures  # noqa: E402


class FixtureCacheTests(unittest.TestCase):
    def test_assembly_and_producer_and_output_invalidation(self):
        with patch.object(fixtures.subprocess, "run") as run:
            fixtures.build(Path("/repo"), Path("/repo/tiles"))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][2], "fixtures.make_layers")
        self.assertEqual(commands[1][2], "turku3d.package.assemble")
        self.assertEqual(
            commands[1][commands[1].index("--ground") + 1],
            "/repo/.cache/gate-inputs/ground-preview",
        )
        self.assertEqual(commands[1][-4:], ["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "scripts/dev/gate_fixtures.py"
            for name in (
                "scripts/dev/gate_fixtures.py",
                "pipeline/turku3d/config.py",
                "pipeline/turku3d/col/meshopt_bridge.mjs",
                "pipeline/env/package-lock.json",
                "pipeline/tests/fixtures/make_layers.py",
                "pipeline/pyproject.toml",
                "tools/validator/package-lock.json",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("initial")
            out = root / "tiles"
            out.mkdir()
            with (
                patch.object(fixtures, "__file__", str(script)),
                patch.object(sys, "argv", ["fixture", str(out)]),
            ):
                with (
                    patch.object(fixtures, "build") as build,
                    patch.object(fixtures, "verify_tree", return_value=[]) as verify,
                ):
                    fixtures.main()
                    fixtures.main()
                    self.assertEqual(build.call_count, 1)
                    (root / "pipeline/turku3d/config.py").write_text("changed")
                    fixtures.main()
                    self.assertEqual(build.call_count, 2)
                    (root / "pipeline/tests/fixtures/make_layers.py").write_text("changed")
                    fixtures.main()
                    self.assertEqual(build.call_count, 3)
                    (root / "pipeline/turku3d/col/meshopt_bridge.mjs").write_text("changed")
                    fixtures.main()
                    self.assertEqual(build.call_count, 4)
                    verify.side_effect = [["bad hash"], []]
                    fixtures.main()
                    self.assertEqual(build.call_count, 5)
