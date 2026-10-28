"""Own Playwright and its dist server in one cancellable process group."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import sandbox as s
from host_slots import identity

TIMEOUT = 2 * 3600


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def clear_stale(path, port):
    """Only reap a server whose recorded birth identity and group still match."""
    try:
        record = json.loads(path.read_text())
        pid, group = record["pid"], record["group"]
        owned = (
            record["port"] == port
            and record["identity"] is not None
            and identity(pid) == record["identity"]
            and os.getpgid(pid) == group
            and group != os.getpgrp()
        )
    except (FileNotFoundError, ProcessLookupError, ValueError, KeyError, TypeError):
        owned = False
    if owned:
        try:
            os.killpg(group, signal.SIGTERM)
            until = time.monotonic() + 2
            while identity(pid) == record["identity"] and time.monotonic() < until:
                time.sleep(0.05)
            # The server may have exited while another member ignored SIGTERM.
            if identity(pid) == record["identity"] or listening(port):
                os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    path.unlink(missing_ok=True)
    if listening(port):
        raise RuntimeError(f"advisory port {port} is occupied by an unowned listener")


def run(command, *, cwd, env, cancelled=None, timeout=TIMEOUT):
    port = (
        int(env["E2E_TILE_PORT"])
        if "E2E_TILE_PORT" in env
        else int(env.get("TURKU3D_PORT_BASE", "5170")) + 1
    )
    record = Path(env["TURKU3D_WORK"]) / "cache/advisory-chrome-server.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    clear_stale(record, port)
    try:
        return s.run_group(
            [sys.executable, str(Path(__file__).resolve()), str(record), str(port), *command],
            cwd=cwd,
            env={**env, "E2E_TILE_EXTERNAL_SERVER": "1"},
            cancelled=cancelled,
            timeout=timeout,
        )
    finally:
        clear_stale(record, port)


def main():
    record, port, *command = sys.argv[1:]
    port = int(port)
    work = Path(os.environ["TURKU3D_WORK"])
    # Playwright's webServer uses a detached group. Launch it here instead, without
    # detaching, so run_group owns the wrapper, server, Playwright and descendants.
    server = subprocess.Popen(
        [
            "node",
            "../tools/dev/serve-dist.mjs",
            "--root",
            str(work / "build/game"),
            "--port",
            str(port),
            "--mount",
            f"/tiles={work / 'dist/tiles'}",
        ]
    )
    Path(record).write_text(
        json.dumps(
            {
                "pid": server.pid,
                "identity": identity(server.pid),
                "group": os.getpgrp(),
                "port": port,
            }
        )
    )
    until = time.monotonic() + 30
    while not listening(port):
        if server.poll() is not None:
            return server.returncode or 1
        if time.monotonic() >= until:
            raise TimeoutError(f"advisory server did not start on port {port}")
        time.sleep(0.05)
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
