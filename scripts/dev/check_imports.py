"""Import package modules in a fresh env built solely from declared dependencies."""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / ".cache", prefix="imports-") as temp:
        python = str(Path(temp) / "bin/python")
        env = {**os.environ, "PYTHONPATH": ""}
        commands = [
            ["uv", "venv", "--python", "3.12", temp],
            [
                "uv",
                "pip",
                "install",
                "--python",
                python,
                "-e",
                f"{ROOT}/pipeline[dev,fetch,lidar,buildings,region]",
                "-e",
                f"{ROOT}/textures[post,dev,streetview]",
            ],
            ["uv", "pip", "install", "--python", python, "--no-deps", "streetlevel==0.12.13"],
            [
                python,
                "-c",
                """import importlib, pathlib
for package in ('turku3d', 'turkutex'):
    module = importlib.import_module(package)
    directory = pathlib.Path(module.__file__).parent
    for path in sorted(directory.rglob('*.py')):
        relative = path.relative_to(directory).with_suffix('')
        if 'tests' in relative.parts or relative.name == '__main__':
            continue
        parts = relative.parts[:-1] if relative.name == '__init__' else relative.parts
        name = '.'.join((package, *parts))
        print(name, flush=True)
        importlib.import_module(name)
""",
            ],
        ]
        for command in commands:
            subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
