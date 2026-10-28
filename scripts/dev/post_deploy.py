"""One agent-owned selective test run per deployment, frozen at the deployed commit."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import bounded_checks
import coordination as coord
import sandbox as s

SECONDS = 600


def history():
    file = s.root() / ".registry/checks.jsonl"
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def append(row):
    file = s.root() / ".registry/checks.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a") as output:
        output.write(json.dumps(row) + "\n")


def copy_dependencies(repo, snapshot):
    """Clone local dependencies while preserving package executable symlinks."""
    for relative in (
        "node_modules",
        "game/node_modules",
        "tools/validator/node_modules",
        "pipeline/.venv",
        "textures/.venv",
        "qa/.venv",
        ".venv",
    ):
        original, target = repo / relative, snapshot / relative
        if original.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.resolve().is_relative_to(repo.parent):
                subprocess.run(["cp", "-cR", str(original.resolve()), str(target)], check=True)
            else:
                target.symlink_to(original.resolve(), target_is_directory=True)


def start(name, repo, commit, plan, env):
    created = time.time()
    root = s.tasks_root() / name / "checks" / commit
    if (root / "request.json").exists():
        return str(root / "request.json")
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / "repo"
    snapshot.mkdir(exist_ok=True)
    # Freeze the exact deployed source; a next rebase or sandbox removal cannot change it.
    with subprocess.Popen(["git", "archive", commit], cwd=repo, stdout=subprocess.PIPE) as archive:
        with tarfile.open(fileobj=archive.stdout, mode="r|") as files:
            files.extractall(snapshot, filter="data")
        if archive.wait():
            raise ValueError("could not freeze deployed source for async checks")
    # Keep source-control-aware tests usable without borrowing the mutable worktree.
    common = Path(s.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=repo))
    subprocess.run(["git", "init", "-q", str(snapshot)], check=True)
    (snapshot / ".git/objects/info/alternates").write_text(str(common / "objects") + "\n")
    s.git("update-ref", "refs/heads/deployed", commit, cwd=snapshot)
    s.git("symbolic-ref", "HEAD", "refs/heads/deployed", cwd=snapshot)
    s.git("read-tree", commit, cwd=snapshot)
    copy_dependencies(repo, snapshot)
    job_env = {
        **env,
        "REPO": str(snapshot),
        "TURKU3D_WORK": str(root / "work"),
        "TURKU3D_BROWSER_DIR": str(root / "browser"),
        "PYTHONPATH": str(snapshot / "pipeline"),
    }
    request = {
        "name": name,
        "commit": commit,
        "commands": plan["async_tests"],
        "reason": plan.get("async_reason"),
        "fix_for": plan.get("fix_for"),
        "created": created,
    }
    file = root / "request.json"
    file.write_text(json.dumps(request))
    file.chmod(0o600)
    command = [sys.executable, str(snapshot / "scripts/dev/post_deploy.py"), "--request", str(file)]
    with (root / "worker.log").open("w") as output:
        subprocess.Popen(
            command, cwd=snapshot, env=job_env, stdout=output, stderr=output, start_new_session=True
        )
    append(
        {
            "job": str(file),
            "name": name,
            "commit": commit,
            "state": "running",
            "time": time.time(),
            "commands": request["commands"],
            "reason": request["reason"],
            "fix_for": request["fix_for"],
        }
    )
    return str(file)


def work(file):
    request = json.loads(file.read_text())
    root, repo = file.parent, file.parent / "repo"
    env = dict(os.environ)
    # Queue time is included in the ten-minute wall budget. Async work uses bulk
    # capacity and cannot reserve the publishing or short-check lanes.
    commands = [
        f"{sys.executable} scripts/dev/host_slots.py build -- /bin/sh -c "
        + __import__("shlex").quote(command)
        for command in request["commands"]
    ]
    remaining = max(0.001, SECONDS - (time.time() - request["created"]))
    result = bounded_checks.commands(
        commands, cwd=repo, env=env, directory=root / "logs", seconds=remaining
    )
    row = {
        "job": str(file),
        "name": request["name"],
        "commit": request["commit"],
        "state": "passed" if result["ok"] else "failed",
        "time": time.time(),
        "commands": request["commands"],
        "fix_for": request.get("fix_for"),
        **result,
    }
    s.write_json(root / "result.json", row)
    append(row)
    if not result["ok"]:
        fixed = bool(request.get("fix_for"))
        recipient = "orchestrator" if fixed else request["name"]
        body = f"Deployed {request['commit']}; result {root / 'result.json'}. " + (
            "The one forward fix has been used; report further work to the orchestrator."
            if fixed
            else "At most one forward fix is allowed: finish --fix-for " + str(file)
        )
        with contextlib.suppress(OSError, ValueError):
            coord.send(
                recipient,
                "warning" if fixed else "request",
                f"Selective post-deploy tests failed: {request['name']}",
                body,
            )
        if not fixed:
            with contextlib.suppress(OSError, ValueError):
                coord.send("orchestrator", "warning", "Agent-owned async checks failed", body)


def validate_fix(file):
    if not file:
        return
    previous = [row for row in history() if row["job"] == file]
    if not previous or previous[-1]["state"] != "failed":
        raise ValueError("--fix-for must name a failed async run")
    if any(row.get("fix_for") == file for row in history()):
        raise ValueError("the one forward fix has already been used; report to the orchestrator")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    work(parser.parse_args().request)
