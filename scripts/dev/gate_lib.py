"""Selection and parallel execution for the offline quality gate."""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

from checks import check_policy


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def paths_from(data):
    return {p for p in data.decode().split("\0") if p}


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def inventory(root):
    return sorted(
        p
        for p in paths_from(
            git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        )
        if (root / p).is_file()
    )


def changes(root, base):
    ancestor = git(root, "merge-base", "HEAD", base).decode().strip()
    paths = paths_from(git(root, "diff", "--name-only", "--no-renames", "-z", ancestor, "HEAD"))
    paths |= paths_from(git(root, "diff", "--name-only", "--no-renames", "-z", "HEAD"))
    paths |= paths_from(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    return sorted(paths)


def related(stage, changed, key):
    """Union the dependency map; unknown or deleted modules select the full package."""
    touched = [p for p in changed if matches(p, stage.get("triggers", []))]
    selected = set()
    for path in touched:
        rows = [row for row in stage.get("related", []) if matches(path, row["paths"])]
        if not rows:
            return None
        for row in rows:
            selected.update(row[key])
    return sorted(selected) or None


def environment(root, config):
    env = os.environ.copy()
    python = Path(env.get("GATE_PYTHON", config["gate"]["python"]))
    if not python.is_absolute():
        python = root / python
    env["PIPELINE_PY"] = str(python)
    # Native CLI dependencies are prepared by make pipeline-bootstrap, never by this gate.
    env["PATH"] = os.pathsep.join(
        [
            env.get("PATH", ""),
            *(str(Path(path).expanduser()) for path in config["gate"].get("tool_dirs", [])),
        ]
    )
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / p) for p in ("pipeline", "textures", "qa", "qa/tests")
    )
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = str(config["gate"]["python_workers"])
    env["RUFF_CACHE_DIR"] = str(root / ".cache/ruff")
    env["TMPDIR"] = str(root / ".cache/tmp")
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    # Prevent each numeric test process from spawning another pool of BLAS threads.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[name] = "1"
    return env


def run_commands(root, commands, env, cwd="."):
    output = []
    for cmd in commands:
        cmd = [str(x).replace("{python}", env["PIPELINE_PY"]) for x in cmd]
        if "/" in cmd[0] and not Path(cmd[0]).is_absolute():
            cmd[0] = str(root / cmd[0])
        output.append("$ " + " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=root / cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output.append(result.stdout)
        if result.returncode:
            return 1, "\n".join(output)
    return 0, "\n".join(output)


def lint_commands(root, stage, changed, files, full, env):
    candidates = [p for p in files if matches(p, stage["sources"])]
    if stage["kind"] == "python-lint":
        candidates = [
            p
            for p in candidates
            if p.endswith(".py")
            or (root / p)
            .read_bytes()
            .split(b"\n", 1)[0]
            .startswith((b"#!/usr/bin/env python", b"#!/usr/bin/python"))
        ]
    # Config edits can change lint semantics for every source.
    config_changed = any(matches(p, stage.get("configs", [])) for p in changed)
    chosen = candidates if full or config_changed else [p for p in candidates if p in changed]
    if not chosen:
        return []
    if stage["kind"] == "python-lint":
        return [
            [env["PIPELINE_PY"], "-m", "ruff", "check", *chosen],
            [env["PIPELINE_PY"], "-m", "ruff", "format", "--check", *chosen],
        ]
    if stage["kind"] == "js-lint":
        return [
            [
                "node_modules/.bin/eslint",
                "--cache",
                "--cache-location",
                ".cache/eslint",
                "--max-warnings=0",
                *chosen,
            ]
        ]
    return [["shfmt", "-d", "-i", "2", *chosen], ["shellcheck", *chosen]]


def e2e(root, stage, changed, full, env):
    base = int(env.get("TURKU3D_PORT_BASE", "5190"))
    env = {
        **env,
        "E2E_PORT": str(base),
        "E2E_TILE_PORT": str(base + 1),
        "TILE_DIST": str(root / ".cache/gate-tiles"),
    }
    tags = None if full else related(stage, changed, "tags")
    grep = ["--grep", "|".join(tags)] if tags else []
    commands = [
        ["game/node_modules/.bin/vite", "build"],
        ["game/node_modules/.bin/playwright", "test", *grep],
    ]
    code, output = run_commands(root, commands, env, "game")
    if code:
        return code, output
    code, more = run_commands(
        root,
        [
            [
                env["PIPELINE_PY"],
                "-m",
                "turku3d.package.stub_tile",
                "--synthetic",
                "--out",
                env["TILE_DIST"],
                "--tile",
                "t0_0_0",
            ]
        ],
        env,
    )
    output += more
    if code:
        return code, output
    code, more = run_commands(
        root,
        [["game/node_modules/.bin/playwright", "test", "-c", "playwright.tile.config.ts", *grep]],
        env,
        "game",
    )
    return code, output + more


def execute(root, stage, changed, files, full, env, config):
    start = time.monotonic()
    try:
        kind = stage["kind"]
        if kind == "policy":
            errors = check_policy(root, files, config)
            code, output = (
                bool(errors),
                "\n".join(errors) or "budgets, binaries and flag registry are current",
            )
        elif kind == "e2e":
            code, output = e2e(root, stage, changed, full, env)
        else:
            cwd = stage.get("cwd", ".")
            if kind in ("python-lint", "js-lint", "shell"):
                commands = lint_commands(root, stage, changed, files, full, env)
            elif kind == "pytest":
                tests = (None if full else related(stage, changed, "tests")) or stage["tests"]
                commands = [[env["PIPELINE_PY"], "-m", "pytest", "-q", "-n", "auto", *tests]]
            elif kind == "vitest":
                paths = [
                    os.path.relpath(root / p, root / cwd)
                    for p in changed
                    if p.endswith(".ts") and (root / p).is_file()
                ]
                run_all = full or any(
                    not p.endswith(".ts") or "config" in p or not (root / p).exists()
                    for p in changed
                    if matches(p, stage["triggers"])
                )
                args = ["run"] if run_all else ["related", "--run", "--passWithNoTests", *paths]
                commands = [["game/node_modules/.bin/vitest", *args, "--maxWorkers=2"]]
            else:
                commands = stage["commands"]
            code, output = run_commands(root, commands, env, cwd)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        code, output = 1, str(exc)
    return {
        "name": stage["name"],
        "status": "FAIL" if code else "PASS",
        "seconds": round(time.monotonic() - start, 3),
        "output": output,
    }


def run_gate(root, config, changed, *, full=False, listing=False, as_json=False, fast=False):
    start = time.monotonic()
    files = inventory(root)
    full = full or (
        not fast and any(matches(p, config["gate"].get("full_triggers", [])) for p in changed)
    )
    chosen, results = [], []
    for stage in config["stages"]:
        hits = [
            p
            for p in changed
            if not matches(p, config["gate"].get("prose", ["*.md"]))
            and matches(p, stage.get("triggers", []))
        ]
        selected = full or stage.get("always") or bool(hits)
        reason = "--all" if full else "always" if stage.get("always") else ", ".join(hits[:3])
        if fast and stage["kind"] not in ("python-lint", "js-lint", "shell"):
            selected, reason = False, "pre-commit: lint/format only"
        elif not selected:
            reason = "no changed path matches stage triggers"
        if selected and not listing:
            chosen.append({**stage, "configs": []} if fast else stage)
        else:
            results.append(
                {
                    "name": stage["name"],
                    "status": "SELECT" if selected else "SKIP",
                    "seconds": 0,
                    "reason": reason,
                }
            )
    if chosen:
        env = environment(root, config)
        with concurrent.futures.ThreadPoolExecutor(max_workers=config["gate"]["workers"]) as pool:
            jobs = [
                pool.submit(execute, root, s, changed, files, full, env, config) for s in chosen
            ]
            for job in concurrent.futures.as_completed(jobs):
                result = job.result()
                results.append(result)
                if not as_json:
                    print(
                        f"{result['status']:6} {result['name']:24} {result['seconds']:7.3f}s",
                        flush=True,
                    )
                    if result["status"] == "FAIL":
                        print(result["output"], flush=True)
    order = {s["name"]: i for i, s in enumerate(config["stages"])}
    results.sort(key=lambda r: order[r["name"]])
    report = {
        "changed": changed,
        "stages": results,
        "seconds": round(time.monotonic() - start, 3),
        "ok": all(r["status"] != "FAIL" for r in results),
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for result in results:
            if result["status"] in ("SKIP", "SELECT"):
                print(f"{result['status']:6} {result['name']:24} {result['reason']}")
        print(f"TOTAL  {'GREEN' if report['ok'] else 'RED'} {report['seconds']:.3f}s")
    return 0 if report["ok"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", metavar="REF")
    parser.add_argument("--all", action="store_true", dest="full")
    parser.add_argument("--list", action="store_true", dest="listing")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        config = tomllib.loads((root / "scripts/dev/gate.toml").read_text())
        changed = changes(root, args.base)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(f"cannot select changes: {exc}")
    return run_gate(
        root, config, changed, full=args.full, listing=args.listing, as_json=args.as_json
    )
