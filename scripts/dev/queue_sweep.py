"""Nonblocking full-main verification after queue publication."""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import live_build
import sandbox as s

NAME = "merge-queue-sweep"


def path():
    return s.root() / ".registry/queue-sweep.json"


def domains(paths):
    return sorted({"/".join(name.split("/")[:2]) for name in paths})


def blocked(repo, record):
    holds = set(s.read_json(path(), {}).get("blocked_domains", []))
    if not holds:
        return False
    changed = s.git("diff", "--name-only", "main...HEAD", cwd=repo).splitlines()
    return "*" in holds or bool(holds.intersection(domains(changed)))


def start():
    """Start one detached sweep; its lock and loop coalesce later publications."""
    log_path = s.root() / ".registry/queue-sweep.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("TURKU3D_LAND_OWNER", None)
    env.pop("TURKU3D_WARM_BROWSER_STATE", None)
    with log_path.open("a") as output:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run"],
            cwd=s.REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def fix_task(commit, detail, log_path):
    from task_runner import launch

    name = f"fix-queue-sweep-{commit[:8]}"
    brief = s.root() / ".registry/briefs" / f"{name}.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(
        "# Fix post-merge full-sweep regression\n\n"
        f"Main commit: {commit}. Failure: {detail}. Log: {log_path}.\n"
        "Reproduce in your own sandbox, fix the responsible area, commit, and run "
        "codex-task precheck before DONE. Do not land main.\n"
    )
    if not (s.tasks_root() / name / "meta.json").exists():
        launch(name, brief=brief, ref="main", route_name="task_rebase")
    return name


def checkout(commit):
    try:
        repo = s.sandbox(NAME) / "repo"
    except ValueError:
        return s.new(NAME, commit)
    if s.git("status", "--porcelain", cwd=repo):
        raise ValueError("full-sweep sandbox is dirty")
    s.git("reset", "--hard", commit, cwd=repo)
    return repo


def verify(commit):
    repo = checkout(commit)
    env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
    env.pop("TURKU3D_CHECK_DEADLINE", None)
    env.pop("TURKU3D_WARM_BROWSER_STATE", None)
    log = s.root() / ".registry" / f"queue-sweep-{commit[:12]}.log"
    started = time.monotonic()
    gate = s.run_group([str(repo / "scripts/dev/gate"), "--all"], cwd=repo, env=env)
    log.write_text(gate.stdout)
    if gate.returncode:
        raise ValueError(f"full gate failed: {s.failure_detail(gate.stdout)}; {log}")
    report = live_build.build(
        repo,
        Path(env["TURKU3D_WORK"]),
        Path(env["TURKU3D_DATA"]),
        env,
        log.with_suffix(".live.log"),
    )
    return {"seconds": round(time.monotonic() - started, 3), "live_build": report}


def run():
    lock_path = s.root() / ".registry/queue-sweep.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        while True:
            commit = s.git("rev-parse", "main")
            previous = s.read_json(path(), {})
            if previous.get("commit") == commit:
                return
            started = time.time()
            try:
                report = verify(commit)
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                changed = (
                    s.git("diff", "--name-only", previous["commit"], commit).splitlines()
                    if previous.get("commit")
                    else []
                )
                blocked_domains = sorted(
                    set(previous.get("blocked_domains", [])) | set(domains(changed) or ["*"])
                )
                detail = str(exc)
                row = {
                    "commit": commit,
                    "status": "red",
                    "time": started,
                    "blocked_domains": blocked_domains,
                    "failure": detail,
                }
                s.write_json(path(), row)
                try:
                    row["fix_task"] = fix_task(commit, detail, path())
                except (ValueError, OSError, subprocess.CalledProcessError) as launch_error:
                    row["fix_task_error"] = str(launch_error)
                s.write_json(path(), row)
            else:
                s.write_json(
                    path(),
                    {
                        "commit": commit,
                        "status": "green",
                        "time": started,
                        "blocked_domains": [],
                        **report,
                    },
                )
            if s.git("rev-parse", "main") == commit:
                return


if __name__ == "__main__":
    if sys.argv[1:] != ["run"]:
        raise SystemExit("usage: queue_sweep.py run")
    run()
