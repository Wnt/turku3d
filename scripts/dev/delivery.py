"""Direct agent delivery: selected checks, package/upload, smoke, selective async tests."""

import contextlib
import fnmatch
import json
import os
import subprocess
import time
from pathlib import Path

import bounded_checks
import coordination as coord
import game_build
import pages
import sandbox as s

QUICK_SECONDS = 30
SMOKE_SECONDS = 5


def plan(
    path,
    repo,
    *,
    tests=None,
    async_tests=None,
    heads_up=None,
    no_tests_reason=None,
    async_reason=None,
    fix_for=None,
):
    file = path / "delivery-plan.json"
    if tests is not None or no_tests_reason is not None:
        if not tests and not no_tests_reason:
            raise ValueError("select affected tests with --test, or explain --no-tests-reason")
        if not async_tests:
            raise ValueError("select the bigger run with --async-test (see codex-task checks)")
        value = {
            "tests": tests or [],
            "async_tests": async_tests,
            "heads_up": heads_up,
            "no_tests_reason": no_tests_reason,
            "async_reason": async_reason,
            "fix_for": fix_for,
            "tree": s.git("rev-parse", "HEAD^{tree}", cwd=repo),
        }
        s.write_json(file, value)
    value = s.read_json(file, {})
    if not value or value.get("tree") != s.git("rev-parse", "HEAD^{tree}", cwd=repo):
        raise ValueError(
            "select affected checks for this change: finish --test COMMAND --async-test COMMAND"
        )
    return value


def environment(repo):
    return {**os.environ, **s.read_env(repo.parent / "sandbox.env")}


def check(repo, selected, path):
    return bounded_checks.commands(
        selected["tests"],
        cwd=repo,
        env=environment(repo),
        directory=path / "quick",
        seconds=QUICK_SECONDS,
    )


def deploy(repo, commit):
    """Only package and upload. No tiles, test stage, deployment lock or rollback copy."""
    env = {**environment(repo), "VITE_PUBLIC_RELEASE": "1"}
    work, published = Path(env["TURKU3D_WORK"]), Path(env["TURKU3D_DATA"])
    started = time.monotonic()
    game_build.build(repo, work, env, reservation=lambda: contextlib.nullcontext(False))
    # A tiny release descriptor references the freshly built bundle in this sandbox.
    release = work / "reports/deploy" / commit
    release.mkdir(parents=True, exist_ok=True)
    game = release / "game"
    if not game.exists():
        game.symlink_to(work / "build/game", target_is_directory=True)
    s.write_json(release / "live-build.json", {"commit": commit})
    package_seconds = round(time.monotonic() - started, 3)
    result = pages.publish(published, release, repo)
    return {
        **result,
        "package_seconds": package_seconds,
        "total_seconds": round(time.monotonic() - started, 3),
    }


def smoke(repo, deployed, path):
    """Curl-level integration probes share a five-second wall deadline."""
    url = deployed["url"].rstrip("/")
    env = environment(repo)
    draft = env.get("VITE_DRAFT_URL", "https://turku.lab.madekivi.fi/coarse/")
    urls = [url + "/", url + "/game/", draft.rstrip("/") + "/health"]
    deadline = time.monotonic() + SMOKE_SECONDS
    # No shell quoting of URLs or environment strings.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = [
            pool.submit(
                bounded_checks.run,
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--location",
                    "--max-time",
                    "5",
                    "--output",
                    os.devnull,
                    url,
                ],
                cwd=repo,
                env=env,
                log=path / "smoke" / f"{i}.log",
                deadline=deadline,
            )
            for i, url in enumerate(urls)
        ]
        checks = [future.result() for future in futures]
    return {
        "ok": all(c["exit"] == 0 for c in checks),
        "checks": checks,
        "seconds": round(SMOKE_SECONDS - max(0, deadline - time.monotonic()), 3),
    }


def ledger(row):
    file = s.root() / ".registry/deployments.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a") as output:
        output.write(json.dumps(row) + "\n")


def after_merge(name, repo, commit, selected, path):
    """Forward only: failures retain main and the deployment, and notify the owner."""
    import post_deploy

    row = {"name": name, "commit": commit, "time": time.time()}
    try:
        row["pages"] = deploy(repo, commit)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        row.update(result="deploy-failed", error=str(exc)[-1000:])
        coord.send(
            "orchestrator",
            "request",
            f"{name}: deployment needs a forward fix",
            f"Main remains {commit}. {row['error']}",
        )
    else:
        row["result"] = "deployed"
        try:
            generator_patterns = (
                "pipeline/turku3d/coarse/*",
                "pipeline/turku3d/facade_kit.py",
                "pipeline/turku3d/facade_assets.py",
                "pipeline/turku3d/package/*",
                "pipeline/turku3d/buildings/*",
                "pipeline/turku3d/config.py",
                "textures/turkutex/facade_fast_render.py",
                "tools/remote-drafts/*",
            )
            generator_changed = any(
                fnmatch.fnmatchcase(file, pattern)
                for file in selected.get("changed_paths", [])
                for pattern in generator_patterns
            )
            if generator_changed:
                import deploy_drafts

                row["generator"] = deploy_drafts.deploy(repo, commit)
            row["smoke"] = smoke(repo, row["pages"], path)
            if not row["smoke"]["ok"]:
                coord.send(
                    "orchestrator",
                    "warning",
                    f"{name}: post-deploy smoke failed",
                    f"Main {commit}; inspect {path / 'smoke'}. Nothing reverted.",
                )
            if generator_changed:
                row["prewarm"] = deploy_drafts.prewarm()
            row["async"] = post_deploy.start(name, repo, commit, selected, environment(repo))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            row["post_deploy_error"] = str(exc)[-1000:]
            coord.send(
                "orchestrator",
                "warning",
                f"{name}: post-deploy diagnostics failed",
                f"Pages remains deployed at {commit}. {row['post_deploy_error']}",
            )
    ledger(row)
    s.write_json(path / "deployment.json", row)
    return row
