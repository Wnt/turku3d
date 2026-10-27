"""Synchronize checkout dependencies only after their content changes."""

import hashlib
import os
import subprocess
import time
from pathlib import Path

from sandbox import lock, read_json, write_json


def digest(root, paths):
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def invoke(command, **kwargs):
    return subprocess.run(command, **kwargs)


def sync(root, env=None):
    root = Path(root)
    env = dict(os.environ if env is None else env)
    work = Path(env.get("TURKU3D_WORK", root / ".cache/work"))
    env.setdefault("UV_CACHE_DIR", str(work / "cache/uv"))
    env.setdefault("npm_config_cache", str(work / "cache/npm"))
    cache = root / ".cache/env-sync"
    cache.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    output = []

    def run(command, cwd=root):
        result = invoke(
            command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        output.append("$ " + " ".join(map(str, command)) + "\n" + result.stdout)
        result.check_returncode()

    try:
        with lock(cache / "lock"):
            stamp = read_json(cache / "stamp.json", {})
            manifests = [
                root / name / "pyproject.toml"
                for name in ("pipeline", "textures")
                if (root / name / "pyproject.toml").exists()
            ]
            requirements = [
                root / p
                for p in (
                    "scripts/dev/requirements.txt",
                    "qa/requirements.txt",
                    "textures/requirements-streetlevel.txt",
                )
                if (root / p).is_file()
            ]
            python = Path(env.get("GATE_PYTHON", root / ".venv/bin/python"))
            if not python.is_absolute():
                python = root / python
            key = digest(root, manifests + requirements)
            if manifests and (stamp.get("python") != key or not python.exists()):
                if python.parent.parent.is_symlink():
                    raise ValueError(
                        "linked Python env is stale; run wt sync-env in its source checkout"
                    )
                if not python.exists():
                    run(["uv", "venv", "--python", "3.12", str(python.parent.parent)])
                command = ["uv", "pip", "install", "--python", str(python)]
                for project, extras in (
                    ("pipeline", "dev,fetch,lidar,buildings,region"),
                    ("textures", "post,ground,dev,streetview"),
                ):
                    if (root / project / "pyproject.toml").exists():
                        command += ["-e", f"{root / project}[{extras}]"]
                for path in requirements:
                    command += ["-r", str(path)]
                run(command)
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(python),
                        "--no-deps",
                        "streetlevel==0.12.13",
                    ]
                )
                stamp["python"] = key
                write_json(cache / "stamp.json", stamp)
            for package in (
                ".",
                "game",
                "tools/validator",
                "tools/music-lab",
                "qa/render",
                "textures/node",
            ):
                directory = root / package
                paths = [directory / name for name in ("package.json", "package-lock.json")]
                if not all(p.exists() for p in paths):
                    continue
                key = digest(root, paths)
                if (
                    stamp.get(package) != key
                    or not (directory / "node_modules/.package-lock.json").exists()
                ):
                    if (directory / "node_modules").is_symlink():
                        raise ValueError(
                            "linked Node env is stale; run wt sync-env in its source checkout"
                        )
                    run(["npm", "ci", "--no-audit", "--no-fund"], directory)
                    stamp[package] = key
                    write_json(cache / "stamp.json", stamp)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "name": "env-sync",
            "status": "FAIL",
            "seconds": round(time.monotonic() - started, 3),
            "output": "\n".join(output) + "\n" + str(exc),
        }
    return {
        "name": "env-sync",
        "status": "PASS",
        "seconds": round(time.monotonic() - started, 3),
        "output": "\n".join(output) or "dependency hashes unchanged",
    }
