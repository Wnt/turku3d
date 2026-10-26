"""Own one persistent browser service for the merge queue when installed."""

import json
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import sandbox as s

NAME = "merge-queue-service"


def existing(state_file, repo):
    try:
        state = json.loads(state_file.read_text())
        pid = int(state["pid"])
        command = s.run("ps", "-p", str(pid), "-o", "command=", check=False).stdout
        if str(repo / "scripts/dev/warm_browser.mjs") in command and " serve " in command:
            return state_file
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def alive(state_file):
    try:
        return bool(existing(state_file, Path(json.loads(state_file.read_text())["repo"])))
    except (OSError, ValueError, KeyError):
        return False


@contextmanager
def service():
    if s.run("git", "cat-file", "-e", "main:scripts/dev/warm_browser.mjs", check=False).returncode:
        yield None
        return
    try:
        try:
            home = s.sandbox(NAME)
        except ValueError:
            repo = s.new(NAME, "main")
            home = repo.parent
    except (OSError, ValueError, subprocess.CalledProcessError):
        yield None
        return
    repo = home / "repo"
    script = repo / "scripts/dev/warm_browser.mjs"
    if not script.is_file():
        yield None
        return
    env = {**os.environ, **s.read_env(home / "sandbox.env")}
    state_file = home / "work/cache/warm-browser.json"
    if existing(state_file, repo):
        yield state_file
        return
    log_file = home / "work/reports/warm-browser.log"
    command = [
        "node",
        str(script),
        "serve",
        "--repo",
        str(repo),
        "--work",
        env["TURKU3D_WORK"],
        "--profile",
        env["TURKU3D_BROWSER_DIR"],
        "--port",
        env["TURKU3D_PORT_BASE"],
    ]
    with log_file.open("a") as log:
        try:
            proc = subprocess.Popen(
                command,
                cwd=repo,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            yield None
            return
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and proc.poll() is None:
                if existing(state_file, repo):
                    break
                time.sleep(0.25)
            yield state_file if existing(state_file, repo) else None
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # The server still owns its descendants; report its log to
                    # the next queue run instead of signalling an unknown PID.
                    pass
