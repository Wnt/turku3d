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

import browser_budget
import spec_selection
from checks import check_policy
from env_sync import sync
from gate_cache import cached_execute, stage_key, versions
from resources import jobs
from sandbox import lock, run_group


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
            selected.update(row.get(key, ()))
    return sorted(selected) or None


def environment(root, config):
    env = os.environ.copy()
    python = Path(env.get("GATE_PYTHON", config["gate"]["python"]))
    if not python.is_absolute():
        python = root / python
    env["PIPELINE_PY"] = str(python)
    work = Path(env.get("TURKU3D_WORK", root / ".cache/work"))
    env["UV_CACHE_DIR"] = str(work / "cache/uv")
    env["npm_config_cache"] = str(work / "cache/npm")
    # Native CLI dependencies are prepared by make pipeline-bootstrap, never by this gate.
    env["PATH"] = os.pathsep.join(
        [
            *(str(Path(path).expanduser()) for path in config["gate"].get("tool_dirs", [])),
            env.get("PATH", ""),
        ]
    )
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / p) for p in ("pipeline", "textures", "qa", "qa/tests")
    )
    env["TURKU3D_JOBS"] = str(jobs())
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
        result = run_group(cmd, cwd=root / cwd, env=env)
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


def browser_plan(root, changed, full, specs=None, projects=None):
    """The gate's Chrome pick: --specs/--project, else --all's full suite, else the proposal
    (spec_selection.select for a code change), with its estimated browser-minutes."""
    if specs:
        plan = {
            "specs": spec_selection.resolve_specs(root, specs),
            "projects": projects or ["webgpu"],
            "reason": "chosen with --specs",
            "full_suite": False,
        }
    elif full:
        plan = spec_selection.full_plan("--all: every spec on both projects")
    else:
        plan = spec_selection.select(changed, "code", root=root)
        if projects:
            plan = {**plan, "projects": projects, "reason": plan["reason"] + "; --project"}
    return {**plan, "estimate_minutes": spec_selection.estimate_minutes(plan, root)}


def describe(plan):
    count = "all" if plan["specs"] == "all" else len(plan["specs"])
    return (
        f"browser pick: {count} specs on {'+'.join(plan['projects'])}, "
        f"~{plan['estimate_minutes']:g} browser-min ({plan['reason'] or 'no reason'})"
    )


def e2e(root, stage, changed, env, plan):
    """Build once, then run the plan's specs of each Playwright config through host slots.

    Each run debits the task's browser budget; a spent budget refuses the stage."""
    base = int(env.get("TURKU3D_PORT_BASE", "5170"))
    work = Path(env.get("TURKU3D_WORK", root / ".cache/work"))
    env = {
        **env,
        "E2E_PORT": str(base + 1),
        "E2E_TILE_PORT": str(base + 1),
        "TILE_DIST": str(root / ".cache/gate-tiles"),
    }
    account = browser_budget.read(env.get("TURKU3D_SANDBOX", "main"))
    output = [describe(plan)]
    if account is not None:
        output.append(browser_budget.summary(account))
    refused = browser_budget.refusal(account)
    if refused:
        return 1, "\n".join([*output, refused])
    chosen = spec_selection.spec_files(root) if plan["specs"] == "all" else plan["specs"]
    suites = [
        (config, directory)
        for config, directory in spec_selection.CONFIGS.items()
        if any(spec.startswith(directory + "/") for spec in chosen)
    ]
    if not suites:
        return 0, "\n".join([*output, "SKIP browser suites: no spec selected"])
    slots = str(root / "scripts/dev/host_slots.py")
    code, built = run_commands(
        root,
        [["python3", slots, "build", "--", str(root / "game/node_modules/.bin/vite"), "build"]],
        env,
        "game",
    )
    output.append(built)
    if code:
        return code, "\n".join(output)

    def runner(command, cwd, env):
        return run_group(["python3", slots, "browser", "--", *command], cwd=cwd, env=env)

    for config, directory in suites:
        if config == spec_selection.TILE_CONFIG:
            code, more = run_commands(
                root,
                [
                    [
                        "python3",
                        slots,
                        "build",
                        "--",
                        env["PIPELINE_PY"],
                        str(root / "scripts/dev/gate_fixtures.py"),
                        env["TILE_DIST"],
                    ]
                ],
                {
                    **env,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            env.get("PYTHONPATH", str(root / "pipeline")),
                            str(root / "pipeline/tests"),
                        ]
                    ),
                    "TURKU3D_DATA": str(root / ".cache/gate-inputs"),
                },
            )
            output.append(more)
            if code:
                return code, "\n".join(output)
        code, more, stats = spec_selection.run(
            plan,
            root=root,
            env=env,
            bundle_dir=work / "build/game",
            tiles_manifest=Path(
                env["TILE_DIST"]
                if config == spec_selection.TILE_CONFIG
                else root / "game/public/dev-tiles"
            )
            / "manifest.json",
            runner=runner,
            config=config,
        )
        output.extend(
            [
                more,
                f"{directory}: run {len(stats['run'])}, cached {len(stats['cached'])}, "
                f"{stats['seconds']:.0f} s",
            ]
        )
        if code:
            break
    if account is not None:
        output.append(browser_budget.summary(browser_budget.read(account["task"])))
    return code, "\n".join(output)


def execute(root, stage, changed, files, full, env, config, plan=None):
    start = time.monotonic()
    if "TURKU3D_CHECK_DEADLINE" in env:
        env = {
            **env,
            "TURKU3D_CHECK_DEADLINE": str(
                min(float(env["TURKU3D_CHECK_DEADLINE"]), start + stage.get("queue_seconds", 120))
            ),
        }
    try:
        kind = stage["kind"]
        if kind == "policy":
            errors = check_policy(root, files, config)
            code, output = (
                bool(errors),
                "\n".join(errors) or "budgets, binaries and flag registry are current",
            )
        elif kind == "e2e":
            code, output = e2e(root, stage, changed, env, plan)
        else:
            cwd = stage.get("cwd", ".")
            if kind in ("python-lint", "js-lint", "shell"):
                commands = lint_commands(root, stage, changed, files, full, env)
            elif kind == "pytest":
                tests = (None if full else related(stage, changed, "tests")) or stage["tests"]
                commands = [
                    [
                        env["PIPELINE_PY"],
                        "-m",
                        "pytest",
                        "-q",
                        "--durations=5",
                        "-n",
                        env["TURKU3D_JOBS"],
                        *tests,
                    ]
                ]
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
                commands = [
                    ["game/node_modules/.bin/vitest", *args, f"--maxWorkers={env['TURKU3D_JOBS']}"]
                ]
            else:
                commands = stage["commands"]
            code, output = run_commands(root, commands, {**env, **stage.get("env", {})}, cwd)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        code, output = 1, str(exc)
    return {
        "name": stage["name"],
        "status": "FAIL" if code else "PASS",
        "seconds": round(time.monotonic() - start, 3),
        "output": output,
    }


def browser(stage):
    """Stages that start Playwright or Chrome; `gate --fast` leaves them to the integrator."""
    return stage["kind"] == "e2e" or bool(stage.get("browser"))


def deep(stage):
    """`tier = "deep"`: over the fast gate's budget; `gate --deep`, `make gate-deep` and the
    integrator (advisory, after publishing) run them, `gate --fast` never does."""
    return stage.get("tier") == "deep"


def selection(config, changed, *, full=False, lint_only=False, fast=False, deep_only=False):
    """Return (stage, selected, reason) for every stage; full_triggers widen to all stages."""
    full = full or (
        not lint_only and any(matches(p, config["gate"].get("full_triggers", [])) for p in changed)
    )
    rows = []
    for stage in config["stages"]:
        hits = [
            p
            for p in changed
            if not matches(p, config["gate"].get("prose", ["*.md"]))
            and matches(p, stage.get("triggers", []))
        ]
        selected = full or stage.get("always") or bool(hits)
        reason = "--all" if full else "always" if stage.get("always") else ", ".join(hits[:3])
        if lint_only and stage["kind"] not in ("python-lint", "js-lint", "shell"):
            selected, reason = False, "pre-commit: lint/format only"
        elif fast and browser(stage):
            selected, reason = False, "--fast: browser stages run in the integrator"
        elif fast and deep(stage):
            selected, reason = False, "--fast: deep tier (make gate-deep, integrator)"
        elif deep_only and not deep(stage):
            selected, reason = False, "--deep: deep tier only"
        elif not selected:
            reason = "no changed path matches stage triggers"
        rows.append((stage, selected, reason))
    return full, rows


def run_gate(root, config, changed, **options):
    """Options: full, listing, as_json, lint_only (pre-commit), fast (no browser or deep
    stages), deep_only (the deep tier), and specs/projects (the Chrome pick; default the
    selector's proposal)."""
    # One checkout has one browser port/output tree. Distinct sandboxes still run in parallel.
    with lock(root / ".cache/gate.lock"):
        return _run_gate(root, config, changed, **options)


def _run_gate(
    root,
    config,
    changed,
    *,
    full=False,
    listing=False,
    as_json=False,
    lint_only=False,
    fast=False,
    deep_only=False,
    specs=None,
    projects=None,
):
    start = time.monotonic()
    files = inventory(root)
    # Only an explicit --all runs every browser spec; gate machinery widening does not.
    plan = None
    if not (fast or lint_only or deep_only) and any(s["kind"] == "e2e" for s in config["stages"]):
        plan = browser_plan(root, changed, full, specs, projects)
    full, rows = selection(
        config, changed, full=full, lint_only=lint_only, fast=fast, deep_only=deep_only
    )
    if plan and (specs or projects):
        rows = [
            (stage, True, "--specs/--project") if stage["kind"] == "e2e" else (stage, *rest)
            for stage, *rest in rows
        ]
    rows = [
        (
            stage,
            selected,
            describe(plan) if plan and stage["kind"] == "e2e" and selected else reason,
        )
        for stage, selected, reason in rows
    ]
    chosen, results = [], []
    for stage, selected, reason in rows:
        if selected and not listing:
            chosen.append({**stage, "configs": []} if lint_only else stage)
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
        if not lint_only:
            synced = sync(root, env)
            results.append(synced)
            if not as_json:
                print(f"{synced['status']:6} env-sync {synced['seconds']:.3f}s", flush=True)
                if synced["status"] == "FAIL":
                    print(synced["output"], flush=True)
            if synced["status"] == "FAIL":
                chosen = []
        tools = versions(env) if chosen and not lint_only else {}

        def run_stage(stage):
            def action():
                return execute(root, stage, changed, files, full, env, config, plan)

            # Policy depends on current main's merge count and is already inexpensive.
            if lint_only or stage["kind"] == "policy":
                return action()
            kind = stage["kind"]
            if kind in ("python-lint", "js-lint", "shell"):
                scope = lint_commands(root, stage, changed, files, full, env)
            elif kind == "pytest":
                scope = None if full else related(stage, changed, "tests")
            elif kind == "e2e":
                scope = {"specs": plan["specs"], "projects": plan["projects"]}
            elif kind == "vitest":
                scope = "full" if full else [p for p in changed if matches(p, stage["triggers"])]
            else:
                scope = None
            key = stage_key(root, stage, files, scope, env, tools)
            return {**cached_execute(root, stage, key, action, env), "input_hash": key}

        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs()) as pool:
            futures = [pool.submit(run_stage, s) for s in chosen]
            for job in concurrent.futures.as_completed(futures):
                result = job.result()
                results.append(result)
                if not as_json:
                    print(
                        f"{'cached ' if result.get('cached') else ''}{result['status']:6} {result['name']:24} {result['seconds']:7.3f}s",
                        flush=True,
                    )
                    if result["status"] == "FAIL":
                        print(result["output"], flush=True)
    order = {s["name"]: i for i, s in enumerate(config["stages"])}
    results.sort(key=lambda r: order.get(r["name"], -1))
    report = {
        "changed": changed,
        "full": full,
        "stages": results,
        "seconds": round(time.monotonic() - start, 3),
        "ok": all(r["status"] != "FAIL" for r in results),
        "browser": plan,
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for result in results:
            if result["status"] in ("SKIP", "SELECT"):
                print(
                    f"{'cached ' if result.get('cached') else ''}{result['status']:6} {result['name']:24} {result['reason']}"
                )
        if plan and (account := browser_budget.read(os.environ.get("TURKU3D_SANDBOX", "main"))):
            print(browser_budget.summary(account))
        print(f"TOTAL  {'GREEN' if report['ok'] else 'RED'} {report['seconds']:.3f}s")
    return 0 if report["ok"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", metavar="REF")
    parser.add_argument("--all", action="store_true", dest="full")
    parser.add_argument("--list", action="store_true", dest="listing")
    parser.add_argument("--json", action="store_true", dest="as_json")
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument(
        "--fast",
        action="store_true",
        help="skip browser and deep-tier stages; the lander's pre-merge gate",
    )
    tier.add_argument(
        "--deep", action="store_true", dest="deep_only", help="only the deep-tier stages"
    )
    parser.add_argument(
        "--paths", nargs="+", help="profile explicit repo-relative paths instead of Git changes"
    )
    parser.add_argument(
        "--specs",
        nargs="+",
        metavar="SPEC",
        help="Chrome specs to run instead of the proposal (paths, file names or 'all')",
    )
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        choices=spec_selection.PROJECTS,
        help="Chrome project(s) to run (repeatable; default: the proposal's)",
    )
    args = parser.parse_args(argv)
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        config = tomllib.loads((root / "scripts/dev/gate.toml").read_text())
        changed = args.paths if args.paths is not None else changes(root, args.base)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(f"cannot select changes: {exc}")
    try:
        if args.specs:
            spec_selection.resolve_specs(root, args.specs)
    except ValueError as exc:
        parser.error(str(exc))
    return run_gate(
        root,
        config,
        changed,
        full=args.full,
        listing=args.listing,
        as_json=args.as_json,
        fast=args.fast,
        deep_only=args.deep_only,
        specs=args.specs,
        projects=args.projects,
    )
