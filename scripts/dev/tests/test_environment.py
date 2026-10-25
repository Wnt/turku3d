"""Exercise the shared host-side environment API and the local Make serving commands."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


class EnvironmentTests(unittest.TestCase):
    def test_node_paths_ports_and_browser_profile_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            env = {
                **os.environ,
                "TURKU3D_DATA": str(base / "shared"),
                "TURKU3D_WORK": str(base / "worker"),
                "TURKU3D_SANDBOX": "contract-test",
                "TURKU3D_PORT_BASE": "6000",
                "TURKU3D_BROWSER_DIR": str(base / "browser"),
            }
            env.pop("TILE_DIST", None)
            script = """
              import { workRoot, gameDist, tileDist, reportsRoot, port, isolateBrowser } from './tools/dev/environment.mjs';
              isolateBrowser();
              console.log(JSON.stringify({workRoot, gameDist, tileDist, reportsRoot, ports: [port(),port(1),port(2)], tmp: process.env.TMPDIR}));
            """
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                cwd=REPO,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            values = json.loads(result.stdout)
            self.assertEqual(
                values,
                {
                    "workRoot": str(base / "worker"),
                    "gameDist": str(base / "worker/build/game"),
                    "tileDist": str(base / "worker/dist/tiles"),
                    "reportsRoot": str(base / "worker/reports"),
                    "ports": [6000, 6001, 6002],
                    "tmp": str(base / "browser/tmp"),
                },
            )
            self.assertTrue((base / "browser/tmp").is_dir())

    def test_local_make_chain_serves_the_sandbox_build(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "TURKU3D_WORK": temp, "TURKU3D_PORT_BASE": "6000"}
            for target in ("tile-show", "tile-qa", "game-preview"):
                result = subprocess.run(
                    ["make", "-n", target],
                    cwd=REPO,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertIn(f"--root {temp}/build/game --port 6001", result.stdout)
                self.assertIn(f"--mount /tiles={temp}/dist/tiles", result.stdout)
                self.assertNotIn("sync.sh", result.stdout)
                self.assertNotIn("ssh ", result.stdout)

    def test_tile_qa_keeps_parallel_judging_and_manifest_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "TURKU3D_WORK": temp, "TURKU3D_PORT_BASE": "6000"}
            for judge in ("0", "1"):
                result = subprocess.run(
                    ["make", "-n", "tile-qa", f"JUDGE={judge}", "QA_WORKERS=4"],
                    cwd=REPO,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertIn("--workers 4", result.stdout)
                self.assertIn(f'--manifest "{temp}/dist/tiles/manifest.json"', result.stdout)
                self.assertEqual("--no-judge" in result.stdout, judge == "0")
