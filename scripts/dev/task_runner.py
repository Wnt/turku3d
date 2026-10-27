"""Headless Codex tasks with durable logs; landing.py lands finished branches."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import browser_budget
import coordination as coord
import external_worker
import host_slots
import landing
import merge_queue
import sandbox as s


def task(name):
    s.name_ok(name)
    path = s.tasks_root() / name
    if not (path / "meta.json").exists():
        raise ValueError(f"no task: {name}")
    return path


def identity(pid, *, locale="C"):
    # Persist a stable start time even when launcher and observer locales differ.
    result = s.run(
        "ps",
        "-p",
        str(pid),
        "-o",
        "lstart=",
        "-o",
        "stat=",
        check=False,
        env={**os.environ, "LC_ALL": locale},
    ).stdout.strip()
    return (
        " ".join(result.split()[:-1]) if result and not result.split()[-1].startswith("Z") else None
    )


def running(meta):
    if not meta.get("pid") or not meta.get("identity"):
        return False
    if identity(meta["pid"]) == meta["identity"]:
        return True
    # Existing tasks stored localized ps output. Never rewrite those records just
    # to inspect them; compare the full pid/start identity in the launcher's locale.
    legacy_locale = os.environ.get("LC_TIME") or os.environ.get("LANG", "C")
    return legacy_locale != "C" and identity(meta["pid"], locale=legacy_locale) == meta["identity"]


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


def check_disk():
    path = s.root()
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    shared = (
        Path(os.environ.get("TURKU3D_DATA", str(Path.home() / "turku3d/data")))
        / "cache/streetlevel"
    )
    shared_size = s.disk_bytes(shared) if shared.exists() else 0
    print(f"disk: free={s.format_bytes(free)} shared-streetlevel={s.format_bytes(shared_size)}")
    required = 30 * 1024**3
    if free < required:
        raise ValueError(
            f"free disk {s.format_bytes(free)} is below 30 GiB "
            f"(shared streetlevel cache {s.format_bytes(shared_size)}); "
            "launch refused. Run scripts/dev/wt du and scripts/dev/wt prune-landed"
        )


def launch(
    name,
    brief=None,
    ref="main",
    effort=None,
    force=False,
    followup=None,
    model=None,
    route_name=None,
    resume_reason="follow-up",
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
            check_disk()
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
            coord.record_resume(path, meta, resume_reason)
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
                    try:
                        event = json.loads(line)
                    except ValueError:
                        event = {}
                    # Codex events carry no time; stamp turn ends for between-turn attribution.
                    if event.get("type") in ("turn.completed", "turn.failed"):
                        line = json.dumps({**event, "time": time.time()}) + "\n"
                    events.write(line)
                    events.flush()
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
        if meta["state"] == "done":
            meta.setdefault("first_finished", meta["finished"])
        meta["last_commit"] = s.git("log", "-1", "--format=%h %s", cwd=meta["repo"])
        s.write_json(path / "meta.json", meta)


def status(name):
    path = task(name)
    meta = s.read_json(path / "meta.json")
    state = meta["state"]
    if state in ("starting", "running") and not running(meta):
        state = "interrupted"
    if kind := host_slots.waiting(name):
        state = f"waiting for {kind} slot"
    if reason := merge_queue.returned(name) or (
        meta.get("returned_reason") if state == "returned" else None
    ):
        state = f"returned: {reason}"
    last = deque(read_events(path), maxlen=1)
    elapsed = (meta.get("finished") or time.time()) - meta.get("started", meta["created"])
    commit = (
        s.git("log", "-1", "--format=%h %s", cwd=meta["repo"])
        if Path(meta["repo"]).exists()
        else meta.get("last_commit", "?")
    )
    idle = coord.idle_minutes(path, meta)
    account = browser_budget.read(name)
    print(
        f"{name}: {state}, {elapsed:.1f}s, last event={last[-1].get('type') if last else 'none'}, "
        f"commit={commit}, {unread_status(name, state)}"
        + (f", idle {idle} min" if idle is not None else "")
        + (
            f", browser {account['used_minutes']:.1f}/{account['budget_minutes']:g} min"
            if account
            else ""
        )
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
    out = s.run(
        "ps",
        "-axo",
        "pid=,pgid=,stat=,lstart=",
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout
    members = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 8 and int(fields[1]) == pgid and not fields[2].startswith("Z"):
            members[int(fields[0])] = " ".join(fields[3:])
    return members


def signal_group(meta):
    pid = meta["pid"]
    members = group_members(pid)
    if not meta.get("identity") or pid not in members:
        return {}
    if members[pid] != meta["identity"]:
        # A legacy localized identity must match both the persisted record and
        # this canonical snapshot. Recheck birth time to reject a recycled PID.
        if not running(meta) or identity(pid) != members[pid]:
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
    # A landing holds the task lock for its whole fast gate, so signal the group
    # first; the lock is only needed for the state update.
    meta = s.read_json(path / "meta.json")
    if not running(meta):
        s.reap_browsers(name)
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
    with s.lock(path / "lock"):
        meta = s.read_json(path / "meta.json")
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
    s.reap_browsers(name)


def main_checkout(repo):
    for block in s.git("worktree", "list", "--porcelain", cwd=repo).split("\n\n"):
        if "branch refs/heads/main" in block.splitlines():
            return Path(block.splitlines()[0].removeprefix("worktree "))
    return None


def cleanup_landed(name, integrated_commit=None):
    """Best-effort cleanup after main advanced; never turn a landing into a false failure."""
    try:
        home = s.sandbox(name)
        s.reap_browsers(name)
        repo = home / "repo"
        if integrated_commit is not None:
            # Batch cherry-picks preserve the source SHA in the landing ledger, but
            # their original branches are not ancestors of main.
            if s.git("status", "--porcelain", cwd=repo) or s.live_processes(home):
                raise ValueError("dirty worktree or live processes")
            if s.git("rev-parse", "HEAD", cwd=repo) != integrated_commit:
                s.git("reset", "--hard", integrated_commit, cwd=repo)
        freed = s.remove(name)
        print(f"{name}: sandbox removed; freed {freed} bytes ({s.format_bytes(freed)})")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"{name}: landed, sandbox retained: {error}; use wt rm after resolving it")


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
    p = subs.add_parser("adopt", help="register an existing sandbox as an externally worked task")
    p.add_argument("name")
    p.add_argument("--brief", required=True)
    p = subs.add_parser("wait-cmd", help="run a long command to exit; print its tail and exit code")
    p.add_argument("--tail", type=int, default=40, help="output lines to print (default 40)")
    p.add_argument("--timeout", type=float, help="seconds before the command group is terminated")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = subs.add_parser("finish", help="record a DONE result written by a non-Codex worker")
    p.add_argument("name")
    p.add_argument("--result", required=True, help="markdown file with the run result headings")
    queue = subs.add_parser("queue")
    queue.add_argument("action", choices=("run", "status"))
    queue.add_argument("--once", action="store_true")
    queue.add_argument("--limit", type=int, default=4)
    for cmd in ("status", "log", "result", "resume", "stop", "land", "_worker"):
        p = subs.add_parser(cmd)
        p.add_argument("name", nargs="?" if cmd == "land" else None)
        if cmd == "land":
            p.add_argument("--batch", nargs="+", help="land these tasks in order")
        if cmd == "log":
            p.add_argument("n", type=int, default=20, nargs="?")
        if cmd == "resume":
            p.add_argument("text")
            p.add_argument("--reason", choices=coord.RESUME_REASONS, default="follow-up")
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
            resume_reason=a.reason,
            model=a.model,
            effort=a.effort,
            route_name=a.route,
        )
    elif a.cmd == "ls":
        list_tasks()
    elif a.cmd == "adopt":
        external_worker.adopt(a.name, a.brief)
    elif a.cmd == "finish":
        external_worker.finish(a.name, a.result)
    elif a.cmd == "wait-cmd":
        # A cancelled tool call must still tear down the command's process group.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        sys.exit(s.wait_cmd(a.command, a.tail, a.timeout))
    elif a.cmd == "queue":
        if a.action == "status":
            merge_queue.status()
        else:
            merge_queue.run(once=a.once, limit=a.limit)
    elif a.cmd == "status":
        status(a.name)
    elif a.cmd == "stop":
        stop(a.name)
    elif a.cmd == "land":
        if bool(a.batch) == bool(a.name):
            ap.error("land takes one name or --batch")
        if a.batch:
            landing.batch(a.batch, a.force)
        else:
            landing.land(a.name, a.force)
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
