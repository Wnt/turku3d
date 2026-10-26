"""Small macOS/Linux worktree registry; no third-party Python dependencies."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from resources import jobs

REPO = Path(__file__).resolve().parents[2]


def run(*args, cwd=REPO, check=True, **kwargs):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, **kwargs)


def run_group(command, *, cwd, env, log=None, timeout=None):
    """Own the entire command tree, including a Playwright server and Chrome.

    Output streams to `log` when given (readable while the command runs), else to a
    private temporary file. `timeout` seconds bound the run like a queue deadline.
    """
    temp_dir = Path(env.get("TURKU3D_WORK", cwd)) / "cache"
    temp_dir.mkdir(parents=True, exist_ok=True)
    with (
        Path(log).open("w+t") if log else tempfile.TemporaryFile(mode="w+t", dir=temp_dir)
    ) as output:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = env.get("TURKU3D_CHECK_DEADLINE")
            limit = (
                timeout
                if timeout is not None
                else max(0.001, float(deadline) - time.monotonic())
                if deadline
                else None
            )
            try:
                rc = proc.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                rc = 124
                output.write(
                    f"\nSLOW: command exceeded {timeout}s timeout\n"
                    if timeout is not None
                    else "\nSLOW: check exceeded queue deadline\n"
                )
        finally:

            def members():
                rows = run("ps", "-axo", "pid=,pgid=,stat=").stdout
                return {
                    int(fields[0])
                    for line in rows.splitlines()
                    if len(fields := line.split()) == 3
                    and fields[1] == str(proc.pid)
                    and not fields[2].startswith("Z")
                }

            owned = members()
            for pid in owned:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 2
            while owned and time.monotonic() < deadline:
                owned &= members()
                if owned:
                    time.sleep(0.05)
            for pid in owned & members():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        output.seek(0)
        return subprocess.CompletedProcess(command, rc, output.read())


def wait_cmd(command, tail=40, timeout=None):
    """Run a long command to exit in one call: stream it to a log, then print its tail.

    Agents use this instead of polling a running gate, precheck or build every few seconds.
    """
    command = command[1:] if command[:1] == ["--"] else command
    if not command:
        raise ValueError("wait-cmd needs a command after --")
    if tail < 0:
        raise ValueError("--tail must be non-negative")
    name = os.environ.get("TURKU3D_SANDBOX") or "main"
    env = dict(os.environ)
    if name != "main" and (root() / name_ok(name) / "sandbox.env").exists():
        env.update(read_env(root() / name / "sandbox.env"))
    if (tasks_root() / name / "meta.json").exists():
        log = tasks_root() / name / "wait-cmd.log"
    else:
        work = env.get("TURKU3D_WORK") or env.get("TURKU3D_DATA") or Path.home() / "turku3d/data"
        log = Path(work) / "wait-cmd.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    result = run_group(command, cwd=Path.cwd(), env=env, log=log, timeout=timeout)
    for line in result.stdout.splitlines()[-tail:] if tail else ():
        print(line)
    print(
        f"wait-cmd: exit {result.returncode} after {time.monotonic() - start:.1f}s; "
        f"full log: {log}",
        flush=True,
    )
    return result.returncode


def failure_detail(output):
    lines = output.splitlines()
    for line in reversed(lines):
        if any(mark in line for mark in ("FAILED ", "FAIL ", "Error:", "error:")):
            return line.strip()[:240]
    return (lines[-1].strip() if lines else "no test output")[:240]


def host_load():
    try:
        return ", ".join(f"{value:.2f}" for value in os.getloadavg())
    except OSError:
        return "unavailable"


def git(*args, cwd=REPO, check=True):
    return run("git", *args, cwd=cwd, check=check).stdout.strip()


def rebase(base, cwd):
    """Rebase HEAD onto base, aborting on conflict.

    A branch that already contains base is left alone: git would otherwise replay and
    linearize its merge commits, which turns an integrated merge into fresh conflicts.
    """
    if (
        run("git", "merge-base", "--is-ancestor", base, "HEAD", cwd=cwd, check=False).returncode
        == 0
    ):
        return
    try:
        git("rebase", base, cwd=cwd)
    except subprocess.CalledProcessError:
        git("rebase", "--abort", cwd=cwd, check=False)
        raise


def root():
    return (
        Path(os.environ.get("TURKU3D_WT_ROOT", Path.home() / "turku3d/wt")).expanduser().resolve()
    )


def tasks_root():
    return (
        Path(os.environ.get("TURKU3D_TASKS_ROOT", Path.home() / "turku3d/tasks"))
        .expanduser()
        .resolve()
    )


def name_ok(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name == "main":
        raise ValueError(
            "name must be 1–64 lowercase letters/digits/hyphens/underscores, except main"
        )
    return name


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path, value):
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def exports(env):
    return "".join(f"export {key}={shlex.quote(str(value))}\n" for key, value in env.items())


def read_env(path):
    env = {}
    for line in path.read_text().splitlines():
        parts = shlex.split(line)
        if len(parts) == 2 and parts[0] == "export":
            key, value = parts[1].split("=", 1)
            env[key] = value
    return env


def environment(name, port):
    home = root() / name
    return {
        "TURKU3D_DATA": os.environ.get("TURKU3D_DATA", str(Path.home() / "turku3d/data")),
        "TURKU3D_WORK": str(home / "work"),
        "TURKU3D_SANDBOX": name,
        "TURKU3D_JOBS": str(jobs()),
        "TURKU3D_PORT_BASE": str(port),
        "TURKU3D_BROWSER_DIR": str(home / "browser"),
    }


def registry():
    records = read_json(root() / ".registry/sandboxes.json", {})
    # Adopt the orchestrator's manually created worktrees without changing their env or ports.
    for env_file in root().glob("*/sandbox.env"):
        name = env_file.parent.name
        if name not in records and (env_file.parent / "repo/.git").exists():
            env = read_env(env_file)
            records[name] = {
                "port": int(env["TURKU3D_PORT_BASE"]),
                "created": env_file.stat().st_mtime,
            }
    ports = [r["port"] for r in records.values()]
    if len(ports) != len(set(ports)):
        raise ValueError("duplicate port blocks in existing sandbox.env files")
    return records


def save_registry(records):
    write_json(root() / ".registry/sandboxes.json", records)


def ports_free(base):
    with contextlib.ExitStack() as stack:
        try:
            for port in range(base, base + 10):
                s = stack.enter_context(socket.socket())
                s.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def new(name, ref="main"):
    name_ok(name)
    with lock(root() / ".registry/lock"):
        records = registry()
        home = root() / name
        if name in records or home.exists():
            raise ValueError(f"sandbox already exists: {name}")
        base = next(
            (
                p
                for p in range(5180, 65000, 10)
                if all(abs(p - r["port"]) >= 10 for r in records.values()) and ports_free(p)
            ),
            None,
        )
        if base is None:
            raise ValueError("no free port block")
        # Git validates REF before any registry entry is committed.
        git("rev-parse", "--verify", ref + "^{commit}")
        home.mkdir(parents=True)
        try:
            git("worktree", "add", "-b", f"wt/{name}", str(home / "repo"), ref)
            for sub in ("build", "cache", "dist", "reports", "qa"):
                (home / "work" / sub).mkdir(parents=True)
            (home / "browser").mkdir()
            (home / "sandbox.env").write_text(exports(environment(name, base)))
            records[name] = {"port": base, "created": time.time()}
            save_registry(records)
        except BaseException:
            if (home / "repo/.git").exists():
                git("worktree", "remove", "--force", str(home / "repo"), check=False)
                git("branch", "-D", f"wt/{name}", check=False)
            shutil.rmtree(home)
            raise
    return home / "repo"


def sandbox(name):
    name_ok(name)
    home = root() / name
    if not (home / "sandbox.env").is_file() or not (home / "repo/.git").exists():
        raise ValueError(f"no sandbox: {name}")
    return home


def cwd_processes():
    """One host scan, reusable when inspecting many sandboxes."""
    out = run("lsof", "-nP", "-Fpnf", "-a", "-d", "cwd", check=False).stdout
    rows = []
    pid, descriptor = None, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("f"):
            descriptor = line[1:]
        elif line.startswith("n") and descriptor == "cwd":
            rows.append((pid, line[1:]))
    return rows


def live_processes(home, cwd_rows=None):
    """Processes whose cwd is inside the sandbox, or which listen on its ports."""
    env = read_env(home / "sandbox.env")
    base = int(env["TURKU3D_PORT_BASE"])
    pids = set()
    # OR-ing cwd and socket selectors makes lsof inspect every descriptor on the
    # host. A cwd-only scan asks just for vnode paths; scan sockets only if a port
    # cannot be bound. Check IPv6 too (an IPv6-only listener permits an IPv4 bind).
    pids.update(
        pid
        for pid, path in (cwd_rows if cwd_rows is not None else cwd_processes())
        if path == str(home) or path.startswith(str(home) + "/")
    )
    occupied = not ports_free(base)
    for port in range(base, base + 10) if socket.has_ipv6 else ():
        with socket.socket(socket.AF_INET6) as probe:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                probe.bind(("::", port))
            except OSError:
                occupied = True
    if occupied:
        out = run(
            "lsof", "-nP", "-Fpnf", f"-iTCP:{base}-{base + 9}", "-sTCP:LISTEN", check=False
        ).stdout
        pid = None
        for line in out.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
                pids.add(pid)
    return sorted(pids - {os.getpid()})


def reap_browsers(name):
    """Stop Chrome processes with a profile strictly inside this sandbox."""
    home = sandbox(name)
    browser_dir = (home / "browser").resolve()
    output = run("ps", "-axo", "pid=,command=").stdout
    candidates = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if not match:
            continue
        pid, command = int(match[1]), match[2]
        if pid == os.getpid() or not re.search(
            r"(?:^|/)(?:Google Chrome|chrome|Chromium|chromium)(?:\s|$)", command
        ):
            continue
        profile = re.search(r"--user-data-dir=(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))", command)
        if not profile:
            continue
        candidate = Path(next(part for part in profile.groups() if part)).expanduser().resolve()
        if candidate.is_relative_to(browser_dir) and candidate != browser_dir:
            candidates.append((pid, command))
    for pid, command in candidates:
        current = run("ps", "-p", str(pid), "-o", "command=", check=False).stdout.strip()
        if current == command:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 2
    remaining = candidates
    while remaining and time.monotonic() < deadline:
        remaining = [
            (pid, command)
            for pid, command in remaining
            if run("ps", "-p", str(pid), "-o", "command=", check=False).stdout.strip() == command
        ]
        if remaining:
            time.sleep(0.05)
    for pid, command in remaining:
        if run("ps", "-p", str(pid), "-o", "command=", check=False).stdout.strip() == command:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return [pid for pid, _ in candidates]


def remove(name, force=False):
    with lock(root() / ".registry/lock"):
        home = sandbox(name)
        repo = home / "repo"
        if not force:
            if git("status", "--porcelain", cwd=repo):
                raise ValueError("worktree is dirty; use --force to discard it")
            if run(
                "git", "merge-base", "--is-ancestor", "HEAD", "main", cwd=repo, check=False
            ).returncode:
                raise ValueError("unmerged commits; land first or use --force")
        live = live_processes(home)
        # --force may discard work, but never kills somebody else's processes.
        if live:
            raise ValueError(f"live processes in {name}: {live}; stop them before removal")
        records = registry()
        size = disk_bytes(home)
        git("worktree", "remove", *(["--force"] if force else []), str(repo))
        git("branch", "-D", f"wt/{name}")
        shutil.rmtree(home)
        records.pop(name, None)
        save_registry(records)
        return size


def disk_bytes(path):
    """Allocated bytes, with hard links counted once within this directory."""
    result = run("du", "-sk", str(path))
    return int(result.stdout.split()[0]) * 1024


def format_bytes(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def sandbox_rows(measure=True):
    with lock(root() / ".registry/lock"):
        records = registry()
        save_registry(records)
    rows = []
    cwd_rows = cwd_processes()
    for name in records:
        home = root() / name
        repo = home / "repo"
        if not repo.exists():
            rows.append((name, None, "missing"))
            continue
        merged = (
            run(
                "git", "merge-base", "--is-ancestor", "HEAD", "main", cwd=repo, check=False
            ).returncode
            == 0
        )
        dirty = bool(git("status", "--porcelain", cwd=repo))
        live = bool(live_processes(home, cwd_rows))
        status = (
            ("landed" if merged else "unmerged")
            + (",dirty" if dirty else "")
            + (",live" if live else "")
        )
        rows.append((name, disk_bytes(home) if measure else None, status))
    return sorted(rows, key=lambda row: -(row[1] or 0)) if measure else rows


def du():
    for name, size, status in sandbox_rows():
        print(f"{name:24} {format_bytes(size) if size is not None else '-':>12} {status}")


def prune_landed():
    for name, _size, status in sandbox_rows(measure=False):
        if status != "landed":
            continue
        try:
            freed = remove(name)
            print(f"{name}: removed; freed {freed} bytes ({format_bytes(freed)})")
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            print(f"{name}: retained ({error})")


def gc():
    with lock(root() / ".registry/lock"):
        git("worktree", "prune")
        records = registry()
        # Never delete directories or release a block for a damaged but extant sandbox.
        for name in list(records):
            if not (root() / name).exists():
                del records[name]
        save_registry(records)


def list_sandboxes():
    with lock(root() / ".registry/lock"):
        records = registry()
        save_registry(records)
    for name, row in sorted(records.items()):
        home = root() / name
        repo = home / "repo"
        if not repo.exists():
            print(f"{name}: missing (run wt gc)")
            continue
        behind, ahead = git("rev-list", "--left-right", "--count", "main...HEAD", cwd=repo).split()
        size = run("du", "-sh", str(home)).stdout.split("\t")[0]
        print(
            f"{name:20} {git('branch', '--show-current', cwd=repo):24} +{ahead}/-{behind} "
            f"ports={row['port']}..{row['port'] + 9} pids={live_processes(home)} size={size}"
        )
