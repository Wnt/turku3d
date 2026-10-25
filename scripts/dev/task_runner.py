"""Headless Codex tasks with durable logs and serialized, verified landing."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import coordination as coord
import sandbox as s


def task(name):
    s.name_ok(name)
    path = s.tasks_root() / name
    if not (path / "meta.json").exists():
        raise ValueError(f"no task: {name}")
    return path


def identity(pid):
    result = s.run("ps", "-p", str(pid), "-o", "lstart=", "-o", "stat=", check=False).stdout.strip()
    return (
        " ".join(result.split()[:-1]) if result and not result.split()[-1].startswith("Z") else None
    )


def running(meta):
    return bool(
        meta.get("pid") and meta.get("identity") and identity(meta["pid"]) == meta["identity"]
    )


def read_events(path):
    if (path / "events.jsonl").exists():
        with (path / "events.jsonl").open() as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def quota(force=False):
    # Reuse the texture runner's bounded JSON-RPC quota reader; it has no optional dependencies.
    sys.path.insert(0, str(s.REPO / "textures"))
    from turkutex.codex.usage import used_percent

    pct = used_percent()
    if pct is None:
        raise ValueError("Codex quota unavailable; launch refused")
    if pct >= 100 and not force:
        raise ValueError(f"Codex quota {pct}%; launch refused (override with --force)")
    return pct


def launch(
    name,
    brief=None,
    ref="main",
    effort=None,
    force=False,
    followup=None,
    model=None,
    route_name=None,
):
    s.name_ok(name)
    path = s.tasks_root() / name
    path.mkdir(parents=True, exist_ok=True)
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json", {})
        if running(meta):
            raise ValueError(f"{name} is already running")
        if followup is None and meta:
            raise ValueError("task already exists; use resume")
        pct = quota(force)
        if followup is None:
            if not brief:
                raise ValueError("--brief is required")
            brief_path = Path(brief).resolve()
            prompt_brief = brief_path.read_text()
            if not (s.root() / name / "sandbox.env").exists():
                s.new(name, ref)
            home = s.sandbox(name)
            common = (home / "repo/docs/briefs/COMMON.md").read_text()
            prompt = (
                f"You are Codex agent '{name}'. Work only in {home / 'repo'}. "
                "The exported TURKU3D_* variables define your sandbox. Do not land or merge main.\n\n"
                + common
                + "\n\n"
                + coord.PROMPT
                + "\n"
                + prompt_brief
            )
            (path / "prompt.md").write_text(prompt)
            meta = {
                "name": name,
                "repo": str(home / "repo"),
                "effort": effort,
                "brief": str(brief_path),
                "created": time.time(),
            }
        else:
            task(name)
            sessions = [
                e["thread_id"] for e in read_events(path) if e.get("type") == "thread.started"
            ]
            if not sessions:
                raise ValueError("no session ID in task events")
            meta["session_id"] = sessions[-1]
            prompt = coord.PROMPT + "\n" + followup
            (path / f"resume-{time.time_ns()}.md").write_text(prompt)
        sys.path.insert(0, str(s.REPO / "textures"))
        from turkutex.codex import routing

        selected_route = (
            route_name or os.environ.get("TURKU3D_CODEX_TASK_ROUTE") or meta.get("route", "task")
        )
        # An explicit route starts from its defaults; an ordinary resume retains its selection.
        preserve = (
            followup is not None
            and not route_name
            and not os.environ.get("TURKU3D_CODEX_TASK_ROUTE")
        )
        selected_model, selected_effort = routing.route(
            selected_route,
            model=model
            or os.environ.get("TURKU3D_CODEX_MODEL")
            or (meta.get("model") if preserve else None),
            effort=effort
            or os.environ.get("TURKU3D_CODEX_EFFORT")
            or (meta.get("effort") if preserve else None),
        )
        meta.update(model=selected_model, effort=selected_effort, route=selected_route)
        home = s.sandbox(name)
        env = {**os.environ, **s.read_env(home / "sandbox.env")}
        (path / "input.md").write_text(prompt)
        (path / "last.md").unlink(missing_ok=True)
        meta.update(state="starting", started=time.time(), finished=None, exit_code=None, quota=pct)
        s.write_json(path / "meta.json", meta)
        with (path / "runner.log").open("a") as log:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_worker", name],
                cwd=home / "repo",
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
        meta.update(pid=proc.pid, identity=identity(proc.pid), state="running")
        (path / "pid").write_text(str(proc.pid) + "\n")
        s.write_json(path / "meta.json", meta)
    print(f"{name}: running pid={proc.pid}; logs: {path}")


def worker(name):
    sys.path.insert(0, str(s.REPO / "textures"))
    from turkutex.codex import accounting, routing

    path = task(name)
    # Parent writes pid and identity under this lock before we start.
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
    signal.signal(
        signal.SIGTERM, lambda *_: None
    )  # reap Codex and record the result after group TERM
    model, effort = routing.route(
        meta.get("route", "task"), model=meta.get("model"), effort=meta["effort"]
    )
    cmd = [
        "codex",
        "exec",
        "-m",
        model,
        "--sandbox",
        "danger-full-access",
        "-C",
        meta["repo"],
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-c",
        'approval_policy="never"',
    ]
    if meta.get("session_id"):
        cmd += ["resume", meta["session_id"]]
    cmd += ["--json", "-o", str(path / "last.md"), "-"]
    try:
        with (path / "input.md").open() as prompt, (path / "events.jsonl").open("a") as events:
            started = time.time()
            thread = None
            terminal = False
            index = sum(
                e.get("type") in ("turn.completed", "turn.failed") for e in read_events(path)
            )
            with subprocess.Popen(
                cmd, stdin=prompt, stdout=subprocess.PIPE, text=True, cwd=meta["repo"]
            ) as proc:
                for line in proc.stdout:
                    events.write(line)
                    events.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "turn.started":
                        terminal = False
                    if event.get("type") == "thread.started":
                        thread = event.get("thread_id")
                    if event.get("type") in ("turn.completed", "turn.failed"):
                        terminal = True
                        accounting.record_call(
                            source=path / "events.jsonl",
                            model=model,
                            effort=effort,
                            job_type=meta.get("route", "task"),
                            task=name,
                            started=started,
                            seconds=time.time() - started,
                            tokens=event.get("usage"),
                            call_id=accounting.stable_id(path / "events.jsonl", index),
                            events=json.dumps({"type": "thread.started", "thread_id": thread}),
                            ok=event["type"] == "turn.completed",
                        )
                        index += 1
                        started = time.time()
                rc = proc.wait()
                if not terminal:
                    accounting.record_call(
                        source=path / "events.jsonl",
                        model=model,
                        effort=effort,
                        job_type=meta.get("route", "task"),
                        task=name,
                        started=started,
                        seconds=time.time() - started,
                        call_id=accounting.stable_id(
                            path / "events.jsonl", f"incomplete-{started}"
                        ),
                        ok=False,
                    )
    except OSError as e:
        print(e, file=sys.stderr)
        accounting.record_call(
            source=path / "events.jsonl",
            model=model,
            effort=effort,
            job_type=meta.get("route", "task"),
            task=name,
            started=meta["started"],
            seconds=time.time() - meta["started"],
            ok=False,
        )
        rc = 127
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        meta.update(exit_code=rc, finished=time.time())
        if meta["state"] != "stopped":
            meta["state"] = "done" if rc == 0 else "failed"
        meta["last_commit"] = s.git("log", "-1", "--format=%h %s", cwd=meta["repo"])
        s.write_json(path / "meta.json", meta)


def status(name):
    path = task(name)
    meta = s.read_json(path / "meta.json")
    state = meta["state"]
    if state in ("starting", "running") and not running(meta):
        state = "interrupted"
    last = deque(read_events(path), maxlen=1)
    elapsed = (meta.get("finished") or time.time()) - meta["started"]
    commit = (
        s.git("log", "-1", "--format=%h %s", cwd=meta["repo"])
        if Path(meta["repo"]).exists()
        else meta.get("last_commit", "?")
    )
    print(
        f"{name}: {state}, {elapsed:.1f}s, last event={last[-1].get('type') if last else 'none'}, "
        f"commit={commit}, {unread_status(name, state)}"
    )


def unread_status(name, state):
    count = len(coord.inbox(name, unread=True))
    flag = " UNACKNOWLEDGED" if count and state not in ("starting", "running") else ""
    return f"unread={count}{flag}"


def list_tasks():
    paths = {
        p.parent
        for pattern in ("*/meta.json", "*/inbox.jsonl")
        for p in s.tasks_root().glob(pattern)
    }
    for path in sorted(paths):
        if (path / "meta.json").exists():
            status(path.name)
        else:
            print(f"{path.name}: inbox only, {unread_status(path.name, 'inbox only')}")


def group_members(pgid):
    """Live members and start identities; zombies cannot receive signals on macOS."""
    out = s.run("ps", "-axo", "pid=,pgid=,stat=,lstart=", check=True).stdout
    members = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 8 and int(fields[1]) == pgid and not fields[2].startswith("Z"):
            members[int(fields[0])] = " ".join(fields[3:])
    return members


def signal_group(meta):
    pid = meta["pid"]
    members = group_members(pid)
    if not meta.get("identity") or members.get(pid) != meta["identity"]:
        return {}
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        # Exit between ps and killpg may leave only unsignalable zombies. A live
        # matching member means a real permission failure, which must stay visible.
        current = group_members(pid)
        if any(current.get(p) == born for p, born in members.items()):
            raise
    return members


def kill_members(members):
    """Escalate only identities observed in the original, still-live group."""
    for member, born in members.items():
        if identity(member) == born:
            try:
                os.kill(member, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if identity(member) == born:
                    raise


def stop(name):
    path = task(name)
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        if not running(meta):
            print(f"{name}: not running")
            return
        pid = meta["pid"]
        try:
            if os.getpgid(pid) != pid:
                raise ValueError("task no longer owns its process group; refusing signal")
        except ProcessLookupError:
            print(f"{name}: not running")
            return
        members = signal_group(meta)
        meta.update(state="stopped", finished=time.time())
        s.write_json(path / "meta.json", meta)
    deadline = time.monotonic() + 3
    while members:
        current = group_members(pid)
        # A surviving known identity anchors the group even after the supervisor
        # exits; include descendants created since the previous snapshot.
        anchored = any(current.get(p) == born for p, born in members.items())
        members = current if anchored else {}
        if time.monotonic() >= deadline:
            # Never signal a recycled group ID after its supervisor has exited.
            kill_members(members)
            break
        if members:
            time.sleep(0.05)
    print(f"{name}: stopped")


def main_checkout(repo):
    for block in s.git("worktree", "list", "--porcelain", cwd=repo).split("\n\n"):
        if "branch refs/heads/main" in block.splitlines():
            return Path(block.splitlines()[0].removeprefix("worktree "))
    return None


def land(name, force=False):
    path = task(name)
    with s.lock(s.root() / ".registry/land.lock"), s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
        if running(meta) or meta.get("state") != "done" or meta.get("exit_code") != 0:
            raise ValueError("task must have completed successfully before landing")
        with coord.landing_guard(name, force):
            pass
        if not (path / "last.md").exists() or [
            line
            for line in (path / "last.md").read_text().splitlines()
            if line.startswith("## Result:")
        ] != ["## Result: DONE"]:
            raise ValueError("result must contain an exact ## Result: DONE heading")
        repo = s.sandbox(name) / "repo"
        if s.git("branch", "--show-current", cwd=repo) != f"wt/{name}":
            raise ValueError("sandbox is not on its expected branch")
        if s.git("status", "--porcelain", cwd=repo):
            raise ValueError("sandbox is dirty")
        main = main_checkout(repo)
        if main and s.git("status", "--porcelain", cwd=main):
            raise ValueError("main worktree is dirty")
        if not (repo / "scripts/dev/gate").is_file():
            raise ValueError("missing scripts/dev/gate; main unchanged")
        before = s.git("rev-parse", "main", cwd=repo)
        start = time.monotonic()
        for _attempt in range(3):
            base = s.git("rev-parse", "main", cwd=repo)
            try:
                s.git("rebase", "main", cwd=repo)
            except subprocess.CalledProcessError:
                s.git("rebase", "--abort", cwd=repo, check=False)
                raise
            head = s.git("rev-parse", "HEAD", cwd=repo)
            env = {**os.environ, **s.read_env(repo.parent / "sandbox.env")}
            gate = repo / "scripts/dev/gate"
            if not gate.is_file():
                raise ValueError("missing scripts/dev/gate; main unchanged")
            commands = [[str(gate), "--base", before]]
            with (path / "gate.log").open("w") as log:
                for command in commands:
                    rc = subprocess.call(
                        command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT
                    )
                    if rc:
                        raise ValueError(f"gate failed ({rc}): {path / 'gate.log'}; main unchanged")
            if (
                s.git("status", "--porcelain", cwd=repo)
                or s.git("rev-parse", "HEAD", cwd=repo) != head
            ):
                raise ValueError("gate changed sandbox files or HEAD; main unchanged")
            if s.git("rev-parse", "main", cwd=repo) == base:
                before = base
                break
        else:
            raise ValueError("main changed during gate three times; retry landing")
        with coord.landing_guard(name, force):
            if main:
                if s.git("status", "--porcelain", cwd=main):
                    raise ValueError("main became dirty during gate; main unchanged")
                s.git("merge", "--ff-only", head, cwd=main)
            else:
                s.git("update-ref", "refs/heads/main", head, before, cwd=repo)
        record = {
            "name": name,
            "before": before,
            "commit": head,
            "time": time.time(),
            "gate_seconds": round(time.monotonic() - start, 3),
        }
        meta["landed"] = record
        s.write_json(path / "meta.json", meta)
        with (s.root() / ".registry/landed.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"{name}: landed {head[:12]} (gate {record['gate_seconds']}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="cmd", required=True)
    coord.add_commands(subs)
    p = subs.add_parser("run")
    p.add_argument("name")
    p.add_argument("--brief", required=True)
    p.add_argument("--from", dest="ref", default="main")
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max", "ultra"))
    p.add_argument("--model")
    p.add_argument("--route", choices=("task", "task_rebase", "light"))
    p.add_argument("--force", action="store_true")
    subs.add_parser("ls")
    for cmd in ("status", "log", "result", "resume", "stop", "land", "_worker"):
        p = subs.add_parser(cmd)
        p.add_argument("name", nargs="?" if cmd == "land" else None)
        if cmd == "land":
            p.add_argument("--batch", nargs="+")
        if cmd == "log":
            p.add_argument("n", type=int, default=20, nargs="?")
        if cmd == "resume":
            p.add_argument("text")
            p.add_argument("--model")
            p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max", "ultra"))
            p.add_argument("--route", choices=("task", "task_rebase", "light"))
        if cmd in ("resume", "land"):
            p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if coord.dispatch(a):
        return
    if a.cmd == "run":
        launch(a.name, a.brief, a.ref, a.effort, a.force, model=a.model, route_name=a.route)
    elif a.cmd == "resume":
        launch(
            a.name,
            force=a.force,
            followup=a.text,
            model=a.model,
            effort=a.effort,
            route_name=a.route,
        )
    elif a.cmd == "ls":
        list_tasks()
    elif a.cmd == "status":
        status(a.name)
    elif a.cmd == "stop":
        stop(a.name)
    elif a.cmd == "land":
        if a.batch:
            if a.name:
                ap.error("choose name or --batch")
            from landing import batch

            batch(a.batch, a.force)
        elif a.name:
            land(a.name, a.force)
        else:
            ap.error("land requires a name or --batch")
    elif a.cmd == "_worker":
        worker(a.name)
    elif a.cmd == "result":
        print((task(a.name) / "last.md").read_text(), end="")
    else:
        for event in deque(read_events(task(a.name)), maxlen=max(0, a.n)):
            print(json.dumps(event))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(
            f"codex-task: {e}"
            + (f"\n{e.stderr}" if isinstance(e, subprocess.CalledProcessError) else ""),
            file=sys.stderr,
        )
        sys.exit(1)
