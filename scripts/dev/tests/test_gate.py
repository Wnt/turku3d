"""Exercise real lint tools and policy failures in disposable Git repositories."""

import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

DEV = Path(__file__).resolve().parents[1]
REPO = DEV.parents[1]
sys.path.insert(0, str(DEV))
from gate_lib import changes, e2e, related  # noqa: E402
from hooks import snapshot  # noqa: E402


def run(root, *args, check=True, input=None):
    result = subprocess.run(
        args, cwd=root, text=True, input=input, capture_output=True, check=False
    )
    if check and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gate-test-")
        self.root = Path(self.temp.name)
        run(self.root, "git", "init", "-q", "-b", "main")
        run(self.root, "git", "config", "user.email", "gate@example.invalid")
        run(self.root, "git", "config", "user.name", "Gate test")
        for name in ("gate", "gate_lib.py", "checks.py", "hooks.py"):
            self.write("scripts/dev/" + name, (DEV / name).read_text())
        (self.root / "scripts/dev/gate").chmod(0o755)
        self.write("eslint.config.mjs", (REPO / "eslint.config.mjs").read_text())
        self.write("ruff.toml", (REPO / "ruff.toml").read_text())
        self.write(".gitignore", "node_modules\n.venv\n.cache/\n__pycache__/\n")
        self.write("package.json", '{"name":"gate-fixture","private":true,"type":"module"}\n')
        self.write(
            "knip.json",
            json.dumps({"entry": ["game/src/main.ts"], "project": ["game/src/**/*.ts"]}),
        )
        self.write("game/src/main.ts", "import { answer } from './value';\nconsole.log(answer);\n")
        self.write("game/src/value.ts", "export const answer = 42;\n")
        self.write("pipeline/example.py", "VALUE = 42\n")
        self.write("docs/flags.toml", "flags = []\n")
        self.write(
            "scripts/dev/gate.toml",
            """
[gate]
workers = 3
python = ".venv/bin/python"
python_workers = 2
[budgets]
lines = { py = 800, ts = 600 }
binary_kib = 512
binary_allowed = []
[flags]
registry = "docs/flags.toml"
main = "main"
sources = ["game/src/*.ts", "pipeline/*.py"]
readers = []
[[stages]]
name = "policy"
kind = "policy"
always = true
[[stages]]
name = "python-lint"
kind = "python-lint"
triggers = ["pipeline/*.py"]
sources = ["pipeline/*.py"]
[[stages]]
name = "js-lint"
kind = "js-lint"
triggers = ["game/*.ts"]
sources = ["game/*.ts"]
[[stages]]
name = "knip"
kind = "command"
triggers = ["game/*.ts"]
commands = [["node_modules/.bin/knip", "--no-progress", "--include", "files,exports"]]
""",
        )
        run(self.root, "git", "add", ".")
        run(self.root, "git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "baseline")
        for name in ("node_modules", ".venv"):
            (self.root / name).symlink_to(REPO / name, target_is_directory=True)

    def write(self, name, text):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def gate(self, *args):
        result = run(self.root, "scripts/dev/gate", "--json", *args, check=False)
        if result.returncode == 2:
            return result, None
        return result, json.loads(result.stdout)

    def close(self):
        self.temp.cleanup()


class GateTest(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    def test_clean_and_docs_only(self):
        result, report = self.fixture.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([s["name"] for s in report["stages"] if s["status"] == "PASS"], ["policy"])
        self.fixture.write("docs/note.md", "hello\n")
        result, report = self.fixture.gate()
        self.assertEqual(result.returncode, 0)
        self.assertLess(report["seconds"], 2)
        self.assertTrue(all(s.get("reason") for s in report["stages"] if s["status"] == "SKIP"))

    def test_changed_union_and_deletions(self):
        f = self.fixture
        f.write("pipeline/example.py", "VALUE = 43\n")
        run(f.root, "git", "add", "pipeline/example.py")
        f.write("pipeline/example.py", "VALUE = 44\n")
        f.write("new file.py", "VALUE = 1\n")
        (f.root / "game/src/value.ts").unlink()
        self.assertEqual(
            changes(f.root, "main"), ["game/src/value.ts", "new file.py", "pipeline/example.py"]
        )
        run(f.root, "git", "checkout", "-qb", "topic")
        run(f.root, "git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "staged only")
        self.assertIn("pipeline/example.py", changes(f.root, "main"))

    def test_usage_and_list(self):
        result, _ = self.fixture.gate("--base", "does-not-exist")
        self.assertEqual(result.returncode, 2)
        result, _ = self.fixture.gate("--bad-option")
        self.assertEqual(result.returncode, 2)
        result, report = self.fixture.gate("--all", "--list")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(all(s["status"] == "SELECT" for s in report["stages"]))

    def test_related_union_and_conservative_fallback(self):
        stage = {
            "triggers": ["pkg/*"],
            "related": [
                {"paths": ["pkg/a.py"], "tests": ["a", "common"]},
                {"paths": ["pkg/b.py"], "tests": ["b", "common"]},
            ],
        }
        self.assertEqual(related(stage, ["pkg/a.py", "pkg/b.py"], "tests"), ["a", "b", "common"])
        self.assertIsNone(related(stage, ["pkg/new.py"], "tests"))

    def test_vehicle_selection_does_not_run_an_empty_pipeline_browser_suite(self):
        config = tomllib.loads((DEV / "gate.toml").read_text())
        stage = next(s for s in config["stages"] if s["kind"] == "e2e")
        env = {"PIPELINE_PY": sys.executable, "TURKU3D_PORT_BASE": "5190"}
        with patch("gate_lib.run_commands", return_value=(0, "browser suite passed")) as run:
            code, output = e2e(REPO, stage, ["game/src/vehicle/vehicle.ts"], False, env)
            self.assertEqual(code, 0)
            self.assertIn("SKIP pipeline browser suite", output)
            self.assertEqual(run.call_count, 1)
            self.assertIn("@drive", run.call_args.args[1][1])
        with patch("gate_lib.run_commands", return_value=(0, "browser suite passed")) as run:
            code, output = e2e(REPO, stage, ["game/src/render/idPass.ts"], False, env)
            self.assertEqual(code, 0)
            self.assertNotIn("SKIP", output)
            self.assertEqual(run.call_count, 3)

    def test_index_snapshot_excludes_unstaged_and_untracked(self):
        f = self.fixture
        f.write("pipeline/example.py", "VALUE = 43\n")
        run(f.root, "git", "add", "pipeline/example.py")
        f.write("pipeline/example.py", "syntax error !!!\n")
        f.write("pipeline/unrelated.py", "syntax error !!!\n")
        f.write("game/src/new.ts", "const x = 1;\nconsole.log(x);\n")
        run(f.root, "git", "add", "game/src/new.ts")
        with snapshot(f.root, "HEAD", staged=True) as target:
            self.assertEqual((target / "pipeline/example.py").read_text(), "VALUE = 43\n")
            self.assertFalse((target / "pipeline/unrelated.py").exists())
            self.assertTrue((target / "game/src/new.ts").exists())
            result = run(target, sys.executable, "scripts/dev/hooks.py", "pre-commit")
            self.assertIn("GREEN", result.stdout)
        self.assertEqual((f.root / "pipeline/example.py").read_text(), "syntax error !!!\n")

    def test_push_uses_committed_tree_and_ignores_deletion(self):
        f = self.fixture
        sha = run(f.root, "git", "rev-parse", "HEAD").stdout.strip()
        f.write("pipeline/untracked.py", "syntax error !!!\n")
        f.write("pipeline/example.py", "syntax error !!!\n")
        data = f"refs/heads/main {sha} refs/heads/main {sha}\n"
        result = run(f.root, sys.executable, "scripts/dev/hooks.py", "pre-push", input=data)
        self.assertIn("GREEN", result.stdout)
        result = run(
            f.root,
            sys.executable,
            "scripts/dev/hooks.py",
            "pre-push",
            input=f"(delete) {'0' * 40} refs/heads/old {sha}\n",
        )
        self.assertEqual(result.returncode, 0)

    def test_generated_material_drift(self):
        f = self.fixture
        shutil.copytree(
            REPO / "pipeline/turku3d",
            f.root / "pipeline/turku3d",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        f.write("docs/specs/materials.md", (REPO / "docs/specs/materials.md").read_text())
        f.write(
            "game/src/shared/col/materials.ts",
            (REPO / "game/src/shared/col/materials.ts").read_text(),
        )
        with (f.root / "scripts/dev/gate.toml").open("a") as file:
            file.write(
                '\n[[stages]]\nname="generated"\nkind="command"\nalways=true\n'
                'commands=[["{python}","-m","turku3d.col.materials"]]\n'
            )
        # Change only the generated artifact; the canonical Markdown spec stays intact.
        f.write("pipeline/turku3d/col/materials_v1.json", "{}\n")
        result, report = f.gate()
        self.assertEqual(result.returncode, 1)
        generated = next(stage for stage in report["stages"] if stage["name"] == "generated")
        self.assertEqual(generated["status"], "FAIL")
        self.assertIn("stale", generated["output"])

    def test_real_tools_reject_planted_defects(self):
        def plant(kind):
            f = Fixture()
            try:
                if kind == "unused export":
                    f.write(
                        "game/src/value.ts", "export const answer = 42;\nexport const unused = 1;\n"
                    )
                    expected = "knip"
                elif kind == "eslint":
                    f.write("game/src/main.ts", "const unused = 3;\nconsole.log(missingName);\n")
                    expected = "js-lint"
                elif kind == "ruff":
                    f.write("pipeline/example.py", "import os\n")
                    expected = "python-lint"
                elif kind == "binary":
                    (f.root / "big.png").write_bytes(
                        b"\x89PNG\r\n\x1a\n" + b"\0" * (2 * 1024 * 1024)
                    )
                    expected = "policy"
                else:
                    f.write(
                        "pipeline/example.py",
                        'from turku3d.flags import feature_flag\n\nVALUE = feature_flag("trial")\n',
                    )
                    if kind == "expired flag":
                        f.write(
                            "docs/flags.toml",
                            '[[flags]]\nname="trial"\nowner="test"\nreason="A/B test"\nadded_at_merge=0\nremove_after_merges=1\n',
                        )
                    expected = "policy"
                result, report = f.gate()
                self.assertEqual(result.returncode, 1, (kind, report))
                failed = {s["name"] for s in report["stages"] if s["status"] == "FAIL"}
                self.assertIn(expected, failed, (kind, report))
                if kind == "expired flag":
                    self.assertIn("expired", report["stages"][0]["output"])
            finally:
                f.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(
                pool.map(
                    plant,
                    [
                        "unused export",
                        "unregistered flag",
                        "expired flag",
                        "binary",
                        "ruff",
                        "eslint",
                    ],
                )
            )


if __name__ == "__main__":
    unittest.main()
